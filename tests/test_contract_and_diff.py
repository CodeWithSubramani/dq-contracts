from __future__ import annotations

import pytest
from pydantic import ValidationError

from dq_contracts import Contract, Impact, check_version_bump, diff, robust_zscore

from .conftest import EXAMPLES


def _with(contract: Contract, **changes) -> Contract:
    data = contract.model_dump()
    data.update(changes)
    return Contract.from_dict(data)


def _col(contract: Contract, name: str, **changes) -> Contract:
    data = contract.model_dump()
    for c in data["columns"]:
        if c["name"] == name:
            c.update(changes)
    return Contract.from_dict(data)


# ---- contract model -------------------------------------------------------------
def test_rejects_unknown_keys(contract):
    data = contract.model_dump()
    data["colums"] = []
    with pytest.raises(ValidationError):
        Contract.from_dict(data)


@pytest.mark.parametrize(
    "mutation",
    [
        {"version": "1.0"},
        {"primary_key": ["nope"]},
        {"freshness": {"column": "nope", "max_age_hours": 1}},
    ],
)
def test_static_validation(contract, mutation):
    with pytest.raises(ValidationError):
        _with(contract, **mutation)


def test_rejects_inverted_range(contract):
    with pytest.raises(ValidationError):
        _col(contract, "amount", min=10, max=1)


# ---- evolution ------------------------------------------------------------------
def test_example_v2_is_rejected_without_major_bump():
    v1 = Contract.from_yaml(EXAMPLES / "contracts" / "orders.v1.yml")
    v2 = Contract.from_yaml(EXAMPLES / "contracts" / "orders.v2.yml")
    impacts = {(c.column, c.impact) for c in diff(v1, v2)}
    assert ("status", Impact.BREAKING) in impacts
    assert ("coupon_code", Impact.ADDITIVE) in impacts
    ok, why = check_version_bump(v1, v2)
    assert not ok and "MAJOR" in why
    assert check_version_bump(v1, _with(v2, version="2.0.0"))[0]


@pytest.mark.parametrize(
    "column,changes,expected",
    [
        ("amount", {"type": "string"}, Impact.BREAKING),
        ("customer_id", {"nullable": True}, Impact.BREAKING),
        ("email", {"nullable": False}, Impact.PATCH),
        ("amount", {"max": 500000}, Impact.BREAKING),
        ("amount", {"max": 50000}, Impact.PATCH),
        ("status", {"allowed_values": ["placed", "paid"]}, Impact.PATCH),
        ("email", {"pii": False}, Impact.BREAKING),
    ],
)
def test_column_change_classification(contract, column, changes, expected):
    changed = diff(contract, _col(contract, column, **changes))
    assert [c.impact for c in changed] == [expected]


def test_integer_to_float_is_safe_widening():
    base = Contract.from_dict(
        {"dataset": "t", "version": "1.0.0", "owner": "o", "columns": [{"name": "n", "type": "integer"}]}
    )
    widened = _col(base, "n", type="float")
    assert [c.impact for c in diff(base, widened)] == [Impact.ADDITIVE]


def test_column_removal_and_pk_change_are_breaking(contract):
    data = contract.model_dump()
    data["columns"] = [c for c in data["columns"] if c["name"] != "email"]
    data["primary_key"] = ["order_id", "customer_id"]
    impacts = {c.detail: c.impact for c in diff(contract, Contract.from_dict(data))}
    assert impacts["column removed"] is Impact.BREAKING
    assert any(d.startswith("primary key") for d in impacts)


def test_version_must_increase_for_any_change(contract):
    ok, _ = check_version_bump(contract, _col(contract, "email", description="new docs"))
    assert not ok
    ok, _ = check_version_bump(contract, _col(contract, "email", description="x")
                               .model_copy(update={"version": "1.0.1"}))
    assert ok


# ---- anomaly --------------------------------------------------------------------
def test_robust_zscore_ignores_single_outlier_in_history():
    history = [100, 102, 98, 101, 99, 5000, 100]  # one bad backfill day
    assert robust_zscore(100, history).is_anomaly is False
    assert robust_zscore(10, history).is_anomaly is True


def test_flat_history_does_not_divide_by_zero():
    assert robust_zscore(100, [100] * 5).is_anomaly is False
    assert robust_zscore(10, [100] * 5).is_anomaly is True
