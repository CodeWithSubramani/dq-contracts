"""Command-line entry point: ``dq-contracts validate | diff | lint``."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from pydantic import ValidationError

from .anomaly import MetricStore
from .contract import Contract
from .diff import check_version_bump, diff
from .validator import validate


def _load_frame(path: Path, contract: Contract):
    import pandas as pd

    time_cols = [c.name for c in contract.columns if c.type in ("timestamp", "date")]
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    df = pd.read_csv(path)
    for c in time_cols:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce", utc=True)
    return df


def _cmd_validate(args: argparse.Namespace) -> int:
    contract = Contract.from_yaml(args.contract)
    if args.data:
        from .backends import PandasBackend

        backend = PandasBackend(_load_frame(Path(args.data), contract))
    elif args.sqlite:
        from .backends import SqlBackend

        backend = SqlBackend(sqlite3.connect(args.sqlite), args.table or contract.dataset, "sqlite")
    elif args.duckdb:
        import duckdb  # optional dependency: pip install dq-contracts[duckdb]

        from .backends import SqlBackend

        backend = SqlBackend(duckdb.connect(args.duckdb, read_only=True), args.table or contract.dataset)
    else:
        print("one of --data, --sqlite or --duckdb is required", file=sys.stderr)
        return 2

    store = MetricStore(args.history) if args.history else None
    report = validate(contract, backend, metric_store=store)
    out = {"json": report.to_json, "markdown": report.to_markdown, "text": report.to_text}[args.format]()
    print(out)
    return report.exit_code


def _cmd_diff(args: argparse.Namespace) -> int:
    old, new = Contract.from_yaml(args.old), Contract.from_yaml(args.new)
    for c in diff(old, new):
        print(f"{c.impact.value:9} {c.column or '-':20} {c.detail}")
    ok, why = check_version_bump(old, new)
    print(("OK: " if ok else "FAIL: ") + why)
    return 0 if ok else 1


def _cmd_lint(args: argparse.Namespace) -> int:
    rc = 0
    for path in args.contracts:
        try:
            c = Contract.from_yaml(path)
            print(f"ok    {path} ({c.dataset} v{c.version}, {len(c.columns)} columns)")
        except (ValidationError, ValueError) as exc:
            rc = 1
            print(f"error {path}\n{exc}", file=sys.stderr)
    return rc


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dq-contracts", description="Data contracts for pipelines")
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="validate a dataset against a contract")
    v.add_argument("contract")
    src = v.add_mutually_exclusive_group()
    src.add_argument("--data", help="CSV or Parquet file")
    src.add_argument("--sqlite", help="SQLite database path")
    src.add_argument("--duckdb", help="DuckDB database path")
    v.add_argument("--table", help="table name (defaults to contract dataset)")
    v.add_argument("--history", help="JSONL metric store for anomaly detection")
    v.add_argument("--format", choices=["text", "json", "markdown"], default="text")
    v.set_defaults(func=_cmd_validate)

    d = sub.add_parser("diff", help="classify changes and enforce semver between contract versions")
    d.add_argument("old")
    d.add_argument("new")
    d.set_defaults(func=_cmd_diff)

    lint = sub.add_parser("lint", help="parse and statically validate contract files")
    lint.add_argument("contracts", nargs="+")
    lint.set_defaults(func=_cmd_lint)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
