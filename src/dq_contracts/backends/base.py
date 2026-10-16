"""Backend protocol.

Backends expose a small set of *metric primitives*. The validator composes them into
checks, so adding a new engine (Spark, BigQuery, Snowflake...) means implementing a
handful of counting functions — not re-implementing every rule.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Sequence


class Backend(ABC):
    name: str = "abstract"

    @abstractmethod
    def row_count(self) -> int: ...

    @abstractmethod
    def column_types(self) -> dict[str, str | None]:
        """Map of physical column name -> logical type (or None when the engine can't tell)."""

    @abstractmethod
    def null_count(self, column: str) -> int: ...

    @abstractmethod
    def duplicate_count(self, columns: Sequence[str]) -> int:
        """Number of rows whose key appears more than once (excess rows, not groups)."""

    @abstractmethod
    def not_in_set_count(self, column: str, values: Sequence[Any]) -> int: ...

    @abstractmethod
    def out_of_range_count(self, column: str, lo: float | None, hi: float | None) -> int: ...

    @abstractmethod
    def pattern_mismatch_count(self, column: str, pattern: str) -> int: ...

    @abstractmethod
    def max_timestamp(self, column: str) -> datetime | None: ...

    def run_sql_check(self, sql: str) -> int:
        raise NotImplementedError(f"{self.name} backend does not support SQL checks")

    @property
    def supports_sql(self) -> bool:
        return False
