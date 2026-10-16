"""Volume anomaly detection with a robust z-score and a tiny append-only metric store.

Mean/stddev is a poor fit for pipeline metrics: one bad backfill day inflates the stddev
and masks the next incident. Median + MAD is resistant to those outliers.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

MAD_TO_STDDEV = 1.4826  # makes MAD a consistent estimator of sigma for normal data


@dataclass(frozen=True)
class AnomalyResult:
    value: float
    median: float
    score: float
    threshold: float
    is_anomaly: bool
    expected_low: float
    expected_high: float


def robust_zscore(value: float, history: Sequence[float], threshold: float = 3.5) -> AnomalyResult:
    if len(history) < 3:
        raise ValueError("need at least 3 historical points")
    med = statistics.median(history)
    mad = statistics.median(abs(x - med) for x in history) * MAD_TO_STDDEV
    if mad == 0:
        # Perfectly flat history: fall back to a relative tolerance so a constant
        # metric doesn't flag every 1-row change, yet a large swing is still caught.
        mad = max(abs(med) * 0.05, 1.0)
    score = (value - med) / mad
    return AnomalyResult(
        value=value,
        median=med,
        score=round(score, 3),
        threshold=threshold,
        is_anomaly=abs(score) > threshold,
        expected_low=med - threshold * mad,
        expected_high=med + threshold * mad,
    )


class MetricStore:
    """JSON-lines store of (dataset, metric, value, ts). Swap for a warehouse table in prod."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def history(self, dataset: str, metric: str, limit: int = 30) -> list[float]:
        if not self.path.exists():
            return []
        values = []
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                rec = json.loads(line)
                if rec["dataset"] == dataset and rec["metric"] == metric:
                    values.append(float(rec["value"]))
        return values[-limit:]

    def record(self, dataset: str, metric: str, value: float, ts: datetime | None = None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        ts = ts or datetime.now(timezone.utc)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps({"dataset": dataset, "metric": metric, "value": value, "ts": ts.isoformat()})
                + "\n"
            )
