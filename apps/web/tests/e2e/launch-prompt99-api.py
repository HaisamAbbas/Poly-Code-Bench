"""Launch a synthetic API projection for isolated Prompt 99 browser checks."""

from __future__ import annotations

import time
from collections.abc import Mapping
from uuid import UUID

from polycodebench_api.app import create_app
from polycodebench_api.auth import TokenDirectory

REPORT_ID = UUID("50000000-0000-4000-8000-000000000099")
PRIVATE_FIELD_REPORT_ID = UUID("60000000-0000-4000-8000-000000000099")
DELAYED_REPORT_ID = UUID("70000000-0000-4000-8000-000000000099")
PRIVATE_SENTINEL = "PROMPT99_PRIVATE_FIXTURE_MARKER"


class _PublicHealthFixture:
    def __init__(self) -> None:
        report: dict[str, object] = {
            "kind": "public_benchmark_health",
            "schema_version": 1,
            "report_id": str(REPORT_ID),
            "review_state": "published",
            "benchmark_label": "Synthetic HumanEval",
            "benchmark_version": "fixture-v1",
            "source_window_start": "2026-01-01T00:00:00Z",
            "source_window_end": "2026-02-01T00:00:00Z",
            "selected_tasks": 12,
            "complete_tasks": 5,
            "partial_tasks": 2,
            "unknown_tasks": 1,
            "unscanned_tasks": 3,
            "blocked_tasks": 1,
            "assessed_tasks": 9,
            "low_risk_tasks": 4,
            "medium_risk_tasks": 2,
            "high_risk_tasks": 1,
            "insufficient_risk_tasks": 2,
            "limitations": ["partial_coverage", "descriptive_only"],
        }
        self._reports: dict[UUID, Mapping[str, object]] = {
            REPORT_ID: report,
            PRIVATE_FIELD_REPORT_ID: {
                **report,
                "report_id": str(PRIVATE_FIELD_REPORT_ID),
                "private_marker": PRIVATE_SENTINEL,
            },
            DELAYED_REPORT_ID: {**report, "report_id": str(DELAYED_REPORT_ID)},
        }

    def get(self, report_id: UUID) -> Mapping[str, object] | None:
        if report_id == DELAYED_REPORT_ID:
            time.sleep(4)
        return self._reports.get(report_id)


def main() -> None:
    app = create_app(
        tokens=TokenDirectory({}),
        public_benchmark_health=_PublicHealthFixture(),
    )
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8139, log_level="warning")


if __name__ == "__main__":
    main()
