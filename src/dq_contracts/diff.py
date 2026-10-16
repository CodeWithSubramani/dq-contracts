"""Contract evolution: classify changes between two versions and enforce semver.

Run in the producer's CI so a breaking change cannot merge without a MAJOR bump
(and therefore without a conversation with downstream consumers).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .contract import ColumnSpec, Contract

# Widening conversions every reasonable consumer can absorb.
_SAFE_WIDENING = {("integer", "float"), ("date", "timestamp")}


class Impact(str, Enum):
    BREAKING = "breaking"   # requires MAJOR
    ADDITIVE = "additive"   # requires MINOR
    PATCH = "patch"         # docs / tightening only


@dataclass(frozen=True)
class Change:
    impact: Impact
    column: str | None
    detail: str


def _column_changes(old: ColumnSpec, new: ColumnSpec) -> list[Change]:
    n, out = old.name, []
    if old.type != new.type:
        impact = Impact.ADDITIVE if (old.type, new.type) in _SAFE_WIDENING else Impact.BREAKING
        out.append(Change(impact, n, f"type {old.type} -> {new.type}"))
    if not old.nullable and new.nullable:
        out.append(Change(Impact.BREAKING, n, "now nullable (consumers may assume NOT NULL)"))
    elif old.nullable and not new.nullable:
        out.append(Change(Impact.PATCH, n, "tightened to NOT NULL"))
    if old.unique and not new.unique:
        out.append(Change(Impact.BREAKING, n, "uniqueness guarantee dropped"))
    if old.allowed_values is not None:
        if new.allowed_values is None:
            out.append(Change(Impact.BREAKING, n, "allowed_values constraint removed"))
        else:
            added = [v for v in new.allowed_values if v not in old.allowed_values]
            removed = [v for v in old.allowed_values if v not in new.allowed_values]
            if added:
                # New enum members break consumers with exhaustive CASE/match logic.
                out.append(Change(Impact.BREAKING, n, f"new allowed values {added}"))
            if removed:
                out.append(Change(Impact.PATCH, n, f"allowed values narrowed, removed {removed}"))
    if (old.min, old.max) != (new.min, new.max):
        widened = (new.min is None or (old.min is not None and new.min < old.min)) or (
            new.max is None or (old.max is not None and new.max > old.max)
        )
        out.append(Change(Impact.BREAKING if widened else Impact.PATCH, n,
                          f"range [{old.min}, {old.max}] -> [{new.min}, {new.max}]"))
    if old.pii != new.pii:
        out.append(Change(Impact.BREAKING if not new.pii else Impact.PATCH, n,
                          f"pii flag {old.pii} -> {new.pii}"))
    if old.description != new.description:
        out.append(Change(Impact.PATCH, n, "description changed"))
    return out


def diff(old: Contract, new: Contract) -> list[Change]:
    changes: list[Change] = []
    old_cols = {c.name: c for c in old.columns}
    new_cols = {c.name: c for c in new.columns}

    for name in old_cols.keys() - new_cols.keys():
        changes.append(Change(Impact.BREAKING, name, "column removed"))
    for name in new_cols.keys() - old_cols.keys():
        col = new_cols[name]
        impact = Impact.ADDITIVE if col.nullable else Impact.BREAKING
        why = "column added" if col.nullable else "NOT NULL column added (breaks existing producers/backfills)"
        changes.append(Change(impact, name, why))
    for name in old_cols.keys() & new_cols.keys():
        changes.extend(_column_changes(old_cols[name], new_cols[name]))

    if old.primary_key != new.primary_key:
        changes.append(Change(Impact.BREAKING, None, f"primary key {old.primary_key} -> {new.primary_key}"))
    if old.freshness and new.freshness and new.freshness.max_age_hours > old.freshness.max_age_hours:
        changes.append(Change(Impact.BREAKING, None, "freshness SLA relaxed"))
    if old.owner != new.owner:
        changes.append(Change(Impact.PATCH, None, f"owner {old.owner} -> {new.owner}"))
    return sorted(changes, key=lambda c: (list(Impact).index(c.impact), c.column or ""))


def required_bump(changes: list[Change]) -> Impact | None:
    impacts = {c.impact for c in changes}
    for level in (Impact.BREAKING, Impact.ADDITIVE, Impact.PATCH):
        if level in impacts:
            return level
    return None


def check_version_bump(old: Contract, new: Contract) -> tuple[bool, str]:
    """Return (ok, explanation) — is ``new.version`` a large enough bump for the diff?"""
    changes = diff(old, new)
    need = required_bump(changes)
    o, n = old.version_tuple, new.version_tuple
    if need is None:
        return True, "no contract changes"
    if n <= o:
        return False, f"contract changed ({need.value}) but version did not increase ({old.version} -> {new.version})"
    major_bumped = n[0] > o[0]
    minor_bumped = major_bumped or (n[0] == o[0] and n[1] > o[1])
    if need is Impact.BREAKING and not major_bumped:
        return False, f"breaking change requires a MAJOR bump (got {old.version} -> {new.version})"
    if need is Impact.ADDITIVE and not minor_bumped:
        return False, f"additive change requires at least a MINOR bump (got {old.version} -> {new.version})"
    return True, f"{need.value} change, version {old.version} -> {new.version} OK"
