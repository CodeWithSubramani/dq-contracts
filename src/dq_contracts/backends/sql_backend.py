"""SQL pushdown backend for any DB-API 2.0 connection (DuckDB, Postgres, SQLite, ...).

Every metric is computed in the database with a single aggregate query, so validating a
billion-row table never pulls rows to the client.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Sequence

from .base import Backend

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")

_TYPE_MAP = {
    "string": ("char", "text", "string", "varchar", "uuid"),
    "integer": ("int", "bigint", "smallint", "tinyint", "hugeint"),
    "float": ("double", "float", "real", "numeric", "decimal"),
    "boolean": ("bool",),
    "timestamp": ("timestamp", "datetime"),
    "date": ("date",),
}


def _quote(ident: str) -> str:
    if not _IDENT.match(ident):
        raise ValueError(f"unsafe SQL identifier: {ident!r}")
    return ".".join(f'"{part}"' for part in ident.split("."))


def _logical(physical: str | None) -> str | None:
    if not physical:
        return None
    p = physical.lower()
    # timestamp must be checked before date ("datetime"/"timestamp" contain neither "date" alone)
    for logical in ("timestamp", "date", "boolean", "integer", "float", "string"):
        if any(token in p for token in _TYPE_MAP[logical]):
            return logical
    return None


class SqlBackend(Backend):
    """``dialect`` controls the few non-portable bits: regex and schema introspection."""

    name = "sql"

    def __init__(self, connection: Any, table: str, dialect: str = "duckdb"):
        if dialect not in {"duckdb", "postgres", "sqlite"}:
            raise ValueError(f"unsupported dialect {dialect!r}")
        self.conn = connection
        self.table = table
        self.qtable = _quote(table)
        self.dialect = dialect
        if dialect == "sqlite" and isinstance(connection, sqlite3.Connection):
            connection.create_function(
                "regexp_full", 2, lambda p, v: None if v is None else int(bool(re.fullmatch(p, str(v))))
            )

    # -- helpers ---------------------------------------------------------------------
    def _scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        cur = self.conn.cursor()
        try:
            cur.execute(sql, tuple(params))
            row = cur.fetchone()
        finally:
            cur.close()
        return row[0] if row else None

    @property
    def _ph(self) -> str:
        return "%s" if self.dialect == "postgres" else "?"

    # -- primitives ------------------------------------------------------------------
    def row_count(self) -> int:
        return int(self._scalar(f"SELECT COUNT(*) FROM {self.qtable}"))

    def column_types(self) -> dict[str, str | None]:
        cur = self.conn.cursor()
        try:
            if self.dialect == "sqlite":
                cur.execute(f"PRAGMA table_info({self.qtable})")
                return {row[1]: _logical(row[2]) for row in cur.fetchall()}
            schema, _, table = self.table.rpartition(".")
            sql = (
                "SELECT column_name, data_type FROM information_schema.columns "
                f"WHERE table_name = {self._ph}"
            )
            params: list[str] = [table]
            if schema:
                sql += f" AND table_schema = {self._ph}"
                params.append(schema)
            cur.execute(sql, params)
            return {name: _logical(dtype) for name, dtype in cur.fetchall()}
        finally:
            cur.close()

    def null_count(self, column: str) -> int:
        c = _quote(column)
        return int(self._scalar(f"SELECT COUNT(*) FROM {self.qtable} WHERE {c} IS NULL"))

    def duplicate_count(self, columns: Sequence[str]) -> int:
        cols = ", ".join(_quote(c) for c in columns)
        sql = (
            f"SELECT COALESCE(SUM(n - 1), 0) FROM "
            f"(SELECT COUNT(*) AS n FROM {self.qtable} GROUP BY {cols} HAVING COUNT(*) > 1) d"
        )
        return int(self._scalar(sql))

    def not_in_set_count(self, column: str, values: Sequence[Any]) -> int:
        c = _quote(column)
        placeholders = ", ".join(self._ph for _ in values)
        sql = f"SELECT COUNT(*) FROM {self.qtable} WHERE {c} IS NOT NULL AND {c} NOT IN ({placeholders})"
        return int(self._scalar(sql, list(values)))

    def out_of_range_count(self, column: str, lo: float | None, hi: float | None) -> int:
        c = _quote(column)
        preds, params = [], []
        if lo is not None:
            preds.append(f"{c} < {self._ph}")
            params.append(lo)
        if hi is not None:
            preds.append(f"{c} > {self._ph}")
            params.append(hi)
        if not preds:
            return 0
        sql = f"SELECT COUNT(*) FROM {self.qtable} WHERE {' OR '.join(preds)}"
        return int(self._scalar(sql, params))

    def pattern_mismatch_count(self, column: str, pattern: str) -> int:
        c = _quote(column)
        if self.dialect == "duckdb":
            pred = f"NOT regexp_full_match(CAST({c} AS VARCHAR), {self._ph})"
        elif self.dialect == "postgres":
            pred = f"NOT (CAST({c} AS TEXT) ~ {self._ph})"
            pattern = f"^(?:{pattern})$"
        else:
            pred = f"regexp_full({self._ph}, {c}) = 0"
        sql = f"SELECT COUNT(*) FROM {self.qtable} WHERE {c} IS NOT NULL AND {pred}"
        return int(self._scalar(sql, [pattern]))

    def max_timestamp(self, column: str) -> datetime | None:
        value = self._scalar(f"SELECT MAX({_quote(column)}) FROM {self.qtable}")
        if value is None:
            return None
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @property
    def supports_sql(self) -> bool:
        return True

    def run_sql_check(self, sql: str) -> int:
        return int(self._scalar(sql.format(table=self.qtable)) or 0)
