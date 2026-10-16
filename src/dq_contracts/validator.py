"""Turns a contract into checks and evaluates them against a backend."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from .anomaly import MetricStore, robust_zscore
from .backends.base import Backend
from .contract import Contract
from .results import CheckResult, Status, ValidationReport

# A float column may legally hold integer data; anything else must match exactly.
_COMPATIBLE = {("float", "integer"), ("timestamp", "date")}


def _guard(check: str, column: str | None, severity: str, fn: Callable[[], CheckResult]) -> CheckResult:
    """A broken check must never crash the run or silently pass — it reports ERROR."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — surface any engine error as a result
        return CheckResult(check, Status.ERROR, severity, column, message=f"{type(exc).__name__}: {exc}")


def _count_check(check: str, column: str | None, failing: int, severity: str, what: str) -> CheckResult:
    status = Status.PASS if failing == 0 else Status.FAIL
    msg = "" if failing == 0 else f"{failing} row(s) {what}"
    return CheckResult(check, status, severity, column, observed=failing, expected=0, message=msg)


def validate(
    contract: Contract,
    backend: Backend,
    *,
    metric_store: MetricStore | None = None,
    now: datetime | None = None,
    record_metrics: bool = True,
) -> ValidationReport:
    now = now or datetime.now(timezone.utc)
    report = ValidationReport(dataset=contract.dataset, contract_version=contract.version)
    add = report.results.append

    # ---- schema ------------------------------------------------------------------
    physical = backend.column_types()
    present: set[str] = set()
    for col in contract.columns:
        if col.name not in physical:
            add(CheckResult("schema.column_present", Status.FAIL, "error", col.name,
                            message="column missing from dataset"))
            continue
        present.add(col.name)
        actual = physical[col.name]
        if actual is None:
            add(CheckResult("schema.type", Status.SKIPPED, "warn", col.name, expected=col.type,
                            message=f"{backend.name} backend cannot infer type"))
        elif actual == col.type or (col.type, actual) in _COMPATIBLE:
            add(CheckResult("schema.type", Status.PASS, "error", col.name, observed=actual, expected=col.type))
        else:
            add(CheckResult("schema.type", Status.FAIL, "error", col.name, observed=actual,
                            expected=col.type, message=f"expected {col.type}, found {actual}"))

    undeclared = sorted(set(physical) - {c.name for c in contract.columns})
    if undeclared:
        add(CheckResult("schema.undeclared_columns", Status.FAIL, "warn", observed=undeclared,
                        message="columns not in contract (possible unannounced schema change)"))

    # ---- column rules ------------------------------------------------------------
    for col in contract.columns:
        if col.name not in present:
            continue
        n = col.name
        if not col.nullable:
            add(_guard("not_null", n, "error", lambda n=n: _count_check(
                "not_null", n, backend.null_count(n), "error", "are null")))
        if col.unique:
            add(_guard("unique", n, "error", lambda n=n: _count_check(
                "unique", n, backend.duplicate_count([n]), "error", "are duplicates")))
        if col.allowed_values is not None:
            vals = col.allowed_values
            add(_guard("allowed_values", n, "error", lambda n=n, v=vals: _count_check(
                "allowed_values", n, backend.not_in_set_count(n, v), "error", "outside allowed set")))
        if col.min is not None or col.max is not None:
            lo, hi = col.min, col.max
            add(_guard("range", n, "error", lambda n=n, lo=lo, hi=hi: _count_check(
                "range", n, backend.out_of_range_count(n, lo, hi), "error", f"outside [{lo}, {hi}]")))
        if col.pattern:
            pat = col.pattern
            add(_guard("pattern", n, "error", lambda n=n, p=pat: _count_check(
                "pattern", n, backend.pattern_mismatch_count(n, p), "error", f"do not match /{p}/")))

    if contract.primary_key and all(k in present for k in contract.primary_key):
        pk = contract.primary_key
        add(_guard("primary_key", ",".join(pk), "error", lambda: _count_check(
            "primary_key", ",".join(pk), backend.duplicate_count(pk), "error", "share a primary key")))

    # ---- freshness ---------------------------------------------------------------
    if contract.freshness and contract.freshness.column in present:
        f = contract.freshness

        def _fresh() -> CheckResult:
            latest = backend.max_timestamp(f.column)
            if latest is None:
                return CheckResult("freshness", Status.FAIL, f.severity, f.column,
                                   expected=f"<= {f.max_age_hours}h", message="no timestamps found")
            age_h = round((now - latest).total_seconds() / 3600, 2)
            ok = age_h <= f.max_age_hours
            return CheckResult("freshness", Status.PASS if ok else Status.FAIL, f.severity, f.column,
                               observed=f"{age_h}h", expected=f"<= {f.max_age_hours}h",
                               message="" if ok else f"latest record is {age_h}h old")

        add(_guard("freshness", f.column, f.severity, _fresh))

    # ---- volume ------------------------------------------------------------------
    rows = backend.row_count()
    if contract.volume:
        v = contract.volume
        if v.min_rows is not None or v.max_rows is not None:
            ok = (v.min_rows is None or rows >= v.min_rows) and (v.max_rows is None or rows <= v.max_rows)
            add(CheckResult("volume.bounds", Status.PASS if ok else Status.FAIL, v.severity,
                            observed=rows, expected=f"[{v.min_rows}, {v.max_rows}]"))
        if v.anomaly_threshold and metric_store is not None:
            history = metric_store.history(contract.dataset, "row_count")
            if len(history) < v.min_history:
                add(CheckResult("volume.anomaly", Status.SKIPPED, v.severity, observed=rows,
                                message=f"warming up: {len(history)}/{v.min_history} data points"))
            else:
                a = robust_zscore(rows, history, v.anomaly_threshold)
                add(CheckResult(
                    "volume.anomaly", Status.FAIL if a.is_anomaly else Status.PASS, v.severity,
                    observed=rows, expected=f"[{a.expected_low:.0f}, {a.expected_high:.0f}]",
                    message=f"robust z={a.score}" if a.is_anomaly else "",
                ))
    if metric_store is not None and record_metrics:
        metric_store.record(contract.dataset, "row_count", rows, now)

    # ---- custom SQL --------------------------------------------------------------
    for chk in contract.checks:
        if not backend.supports_sql:
            add(CheckResult(f"sql.{chk.name}", Status.SKIPPED, chk.severity,
                            message=f"{backend.name} backend has no SQL engine"))
            continue
        add(_guard(f"sql.{chk.name}", None, chk.severity, lambda c=chk: _count_check(
            f"sql.{c.name}", None, backend.run_sql_check(c.sql), c.severity, "violate the rule")))

    return report
