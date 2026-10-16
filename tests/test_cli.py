from __future__ import annotations

from dq_contracts.cli import main

from .conftest import EXAMPLES


def test_validate_csv_exits_nonzero_on_violations(capsys):
    rc = main(["validate", str(EXAMPLES / "contracts" / "orders.v1.yml"),
               "--data", str(EXAMPLES / "orders.csv"), "--format", "json"])
    assert rc == 1
    assert '"passed": false' in capsys.readouterr().out


def test_diff_gate(capsys):
    rc = main(["diff", str(EXAMPLES / "contracts" / "orders.v1.yml"),
               str(EXAMPLES / "contracts" / "orders.v2.yml")])
    out = capsys.readouterr().out
    assert rc == 1
    assert "breaking" in out and "FAIL" in out


def test_lint_ok(capsys):
    assert main(["lint", str(EXAMPLES / "contracts" / "orders.v1.yml")]) == 0
    assert "ok" in capsys.readouterr().out
