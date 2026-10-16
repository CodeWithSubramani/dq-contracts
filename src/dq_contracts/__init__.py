"""dq-contracts: declarative, versioned data contracts with pushdown validation."""

from .anomaly import MetricStore, robust_zscore
from .contract import ColumnSpec, Contract
from .diff import Change, Impact, check_version_bump, diff
from .results import CheckResult, Status, ValidationReport
from .validator import validate

__version__ = "0.1.0"

__all__ = [
    "Change",
    "CheckResult",
    "ColumnSpec",
    "Contract",
    "Impact",
    "MetricStore",
    "Status",
    "ValidationReport",
    "check_version_bump",
    "diff",
    "robust_zscore",
    "validate",
]
