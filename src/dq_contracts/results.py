"""Check results and the validation report."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Status(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"
    ERROR = "error"  # the check itself could not run


@dataclass(frozen=True)
class CheckResult:
    check: str
    status: Status
    severity: str = "error"
    column: str | None = None
    observed: Any = None
    expected: Any = None
    message: str = ""

    @property
    def blocking(self) -> bool:
        return self.severity == "error" and self.status in (Status.FAIL, Status.ERROR)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class ValidationReport:
    dataset: str
    contract_version: str
    results: list[CheckResult] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def passed(self) -> bool:
        return not any(r.blocking for r in self.results)

    @property
    def exit_code(self) -> int:
        return 0 if self.passed else 1

    def summary(self) -> dict[str, int]:
        counts = {s.value: 0 for s in Status}
        for r in self.results:
            counts[r.status.value] += 1
        counts["warnings"] = sum(
            1 for r in self.results if r.severity == "warn" and r.status == Status.FAIL
        )
        return counts

    def to_json(self) -> str:
        return json.dumps(
            {
                "dataset": self.dataset,
                "contract_version": self.contract_version,
                "started_at": self.started_at,
                "passed": self.passed,
                "summary": self.summary(),
                "results": [r.to_dict() for r in self.results],
            },
            indent=2,
            default=str,
        )

    def to_markdown(self) -> str:
        icon = {"pass": "✅", "fail": "❌", "skipped": "⏭️", "error": "⚠️"}
        lines = [
            f"### Data contract: `{self.dataset}` v{self.contract_version} — "
            f"{'PASSED' if self.passed else 'FAILED'}",
            "",
            "| | Check | Column | Severity | Observed | Expected | Message |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in self.results:
            lines.append(
                f"| {icon[r.status.value]} | {r.check} | {r.column or ''} | {r.severity} "
                f"| {'' if r.observed is None else r.observed} "
                f"| {'' if r.expected is None else r.expected} | {r.message} |"
            )
        return "\n".join(lines)

    def to_text(self) -> str:
        lines = [f"{self.dataset} v{self.contract_version}: {'PASSED' if self.passed else 'FAILED'}"]
        for r in self.results:
            target = f"[{r.column}] " if r.column else ""
            lines.append(f"  {r.status.value.upper():8} {r.severity:5} {r.check} {target}{r.message}")
        s = self.summary()
        lines.append(
            f"  -- {s['pass']} passed, {s['fail']} failed ({s['warnings']} warnings), "
            f"{s['skipped']} skipped, {s['error']} errored"
        )
        return "\n".join(lines)
