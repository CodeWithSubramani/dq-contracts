from __future__ import annotations

from datetime import timedelta

import pytest

from dq_contracts import MetricStore, Status, validate
from dq_contracts.backends import PandasBackend, SqlBackend

from .conftest import NOW


def _by_check(report):
    out: dict[tuple[str, str | None], object] = {}
    for r in report.results:
        out[(r.check, r.column)] = r
    return out


def test_clean_data_passes(contract, good_df):
    report = validate(contract, PandasBackend(good_df), now=NOW)
    assert report.passed, report.to_text()
    assert report.exit_code == 0


@pytest.mark.parametrize("backend_kind", ["pandas", "sqlite"])
def test_violations_are_detected_on_every_backend(backend_kind, contract, bad_df, sqlite_conn):
    backend = PandasBackend(bad_df) if backend_kind == "pandas" else SqlBackend(sqlite_conn, "orders", "sqlite")
    report = validate(contract, backend, now=NOW)
    r = _by_check(report)

    assert not report.passed
    assert r[("not_null", "customer_id")].observed == 1
    assert r[("allowed_values", "status")].observed == 1
    assert r[("allowed_values", "currency")].observed == 1
    assert r[("range", "amount")].observed == 1
    assert r[("pattern", "order_id")].observed == 1
    assert r[("primary_key", "order_id")].observed == 1
    assert r[("freshness", "updated_at")].status == Status.PASS  # newest row is 1h old


def test_sql_checks_skip_on_pandas_but_run_on_sql(contract, bad_df, sqlite_conn):
    pandas_report = validate(contract, PandasBackend(bad_df), now=NOW)
    sql_report = validate(contract, SqlBackend(sqlite_conn, "orders", "sqlite"), now=NOW)
    key = ("sql.delivered_orders_have_amount", None)
    assert _by_check(pandas_report)[key].status == Status.SKIPPED
    assert _by_check(sql_report)[key].status == Status.PASS


def test_stale_data_fails_freshness(contract, good_df):
    report = validate(contract, PandasBackend(good_df), now=NOW + timedelta(hours=12))
    fresh = _by_check(report)[("freshness", "updated_at")]
    assert fresh.status == Status.FAIL
    assert not report.passed


def test_missing_and_undeclared_columns(contract, good_df):
    df = good_df.drop(columns=["email"]).assign(shadow_col=1)
    r = _by_check(validate(contract, PandasBackend(df), now=NOW))
    assert r[("schema.column_present", "email")].status == Status.FAIL
    undeclared = r[("schema.undeclared_columns", None)]
    assert undeclared.observed == ["shadow_col"] and undeclared.severity == "warn"


def test_type_mismatch(contract, good_df):
    df = good_df.assign(amount=["ten", "twenty"])
    r = _by_check(validate(contract, PandasBackend(df), now=NOW))
    assert r[("schema.type", "amount")].status == Status.FAIL


def test_broken_check_reports_error_instead_of_crashing(contract, good_df):
    class Exploding(PandasBackend):
        def pattern_mismatch_count(self, column, pattern):
            raise RuntimeError("engine down")

    r = _by_check(validate(contract, Exploding(good_df), now=NOW))
    assert r[("pattern", "order_id")].status == Status.ERROR


def test_volume_anomaly_uses_history(tmp_path, contract, good_df):
    store = MetricStore(tmp_path / "metrics.jsonl")
    for n in [1000, 1010, 990, 1005, 995, 1002, 998]:
        store.record("orders", "row_count", n)
    r = _by_check(validate(contract, PandasBackend(good_df), metric_store=store, now=NOW))
    anomaly = r[("volume.anomaly", None)]
    assert anomaly.status == Status.FAIL  # 2 rows vs ~1000 baseline
    assert anomaly.severity == "warn"  # volume is advisory by default -> doesn't block
    assert store.history("orders", "row_count")[-1] == 2  # today's metric was recorded


def test_volume_anomaly_warms_up(tmp_path, contract, good_df):
    store = MetricStore(tmp_path / "m.jsonl")
    r = _by_check(validate(contract, PandasBackend(good_df), metric_store=store, now=NOW))
    assert r[("volume.anomaly", None)].status == Status.SKIPPED


def test_report_renderers(contract, bad_df):
    report = validate(contract, PandasBackend(bad_df), now=NOW)
    assert '"passed": false' in report.to_json()
    assert "FAILED" in report.to_markdown()
    assert "FAILED" in report.to_text()
