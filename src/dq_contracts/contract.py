"""Contract model: the declarative, versioned promise a producer makes about a dataset."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ColumnType = Literal["string", "integer", "float", "boolean", "date", "timestamp"]
Severity = Literal["error", "warn"]

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ColumnSpec(_Strict):
    name: str
    type: ColumnType
    nullable: bool = True
    unique: bool = False
    allowed_values: list[Any] | None = None
    min: float | None = None
    max: float | None = None
    pattern: str | None = None
    pii: bool = False
    description: str | None = None

    @field_validator("pattern")
    @classmethod
    def _valid_regex(cls, v: str | None) -> str | None:
        if v is not None:
            re.compile(v)
        return v

    @model_validator(mode="after")
    def _range_is_sane(self) -> ColumnSpec:
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"column {self.name!r}: min ({self.min}) > max ({self.max})")
        return self


class FreshnessSpec(_Strict):
    column: str
    max_age_hours: float = Field(gt=0)
    severity: Severity = "error"


class VolumeSpec(_Strict):
    min_rows: int | None = Field(default=None, ge=0)
    max_rows: int | None = Field(default=None, ge=0)
    # Robust z-score threshold against historical row counts (needs a metric store).
    anomaly_threshold: float | None = Field(default=3.5, gt=0)
    min_history: int = Field(default=7, ge=3)
    severity: Severity = "warn"


class SqlCheck(_Strict):
    """A custom rule expressed as a SQL query that returns the number of failing rows.

    ``{table}`` is substituted with the dataset's table name. Only run by SQL backends.
    """

    name: str
    sql: str
    severity: Severity = "error"


class Contract(_Strict):
    dataset: str
    version: str
    owner: str
    description: str | None = None
    primary_key: list[str] = Field(default_factory=list)
    columns: list[ColumnSpec]
    freshness: FreshnessSpec | None = None
    volume: VolumeSpec | None = None
    checks: list[SqlCheck] = Field(default_factory=list)

    @field_validator("version")
    @classmethod
    def _semver(cls, v: str) -> str:
        if not _SEMVER.match(v):
            raise ValueError(f"version must be semver MAJOR.MINOR.PATCH, got {v!r}")
        return v

    @model_validator(mode="after")
    def _references_exist(self) -> Contract:
        names = [c.name for c in self.columns]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"duplicate column definitions: {sorted(dupes)}")
        known = set(names)
        missing_pk = [k for k in self.primary_key if k not in known]
        if missing_pk:
            raise ValueError(f"primary_key references unknown columns: {missing_pk}")
        if self.freshness and self.freshness.column not in known:
            raise ValueError(f"freshness column {self.freshness.column!r} is not defined")
        return self

    def column(self, name: str) -> ColumnSpec | None:
        return next((c for c in self.columns if c.name == name), None)

    @property
    def version_tuple(self) -> tuple[int, int, int]:
        major, minor, patch = (int(p) for p in self.version.split("."))
        return major, minor, patch

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Contract:
        return cls.model_validate(data)

    @classmethod
    def from_yaml(cls, path: str | Path) -> Contract:
        with open(path, encoding="utf-8") as fh:
            return cls.from_dict(yaml.safe_load(fh))
