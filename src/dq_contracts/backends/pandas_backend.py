"""In-memory backend for pandas DataFrames (unit tests, notebooks, small files)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

import pandas as pd
from pandas.api import types as ptypes

from .base import Backend


def _logical_type(series: pd.Series) -> str | None:
    dtype = series.dtype
    if ptypes.is_bool_dtype(dtype):
        return "boolean"
    if ptypes.is_integer_dtype(dtype):
        return "integer"
    if ptypes.is_float_dtype(dtype):
        # Integer columns with nulls are upcast to float by pandas; accept them as integer
        # when every non-null value is integral.
        non_null = series.dropna()
        if len(non_null) and (non_null % 1 == 0).all():
            return "integer"
        return "float"
    if ptypes.is_datetime64_any_dtype(dtype):
        return "timestamp"
    if ptypes.is_string_dtype(dtype) or ptypes.is_object_dtype(dtype):
        return "string"
    return None


class PandasBackend(Backend):
    name = "pandas"

    def __init__(self, df: pd.DataFrame):
        self.df = df

    def row_count(self) -> int:
        return int(len(self.df))

    def column_types(self) -> dict[str, str | None]:
        return {c: _logical_type(self.df[c]) for c in self.df.columns}

    def null_count(self, column: str) -> int:
        return int(self.df[column].isna().sum())

    def duplicate_count(self, columns: Sequence[str]) -> int:
        return int(self.df.duplicated(subset=list(columns), keep="first").sum())

    def not_in_set_count(self, column: str, values: Sequence[Any]) -> int:
        s = self.df[column].dropna()
        return int((~s.isin(list(values))).sum())

    def out_of_range_count(self, column: str, lo: float | None, hi: float | None) -> int:
        s = pd.to_numeric(self.df[column], errors="coerce").dropna()
        mask = pd.Series(False, index=s.index)
        if lo is not None:
            mask |= s < lo
        if hi is not None:
            mask |= s > hi
        return int(mask.sum())

    def pattern_mismatch_count(self, column: str, pattern: str) -> int:
        s = self.df[column].dropna().astype(str)
        return int((~s.str.fullmatch(pattern)).sum())

    def max_timestamp(self, column: str) -> datetime | None:
        s = pd.to_datetime(self.df[column], errors="coerce", utc=True).dropna()
        if s.empty:
            return None
        return s.max().to_pydatetime().astimezone(timezone.utc)
