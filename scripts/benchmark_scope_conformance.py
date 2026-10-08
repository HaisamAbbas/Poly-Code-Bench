"""Print deterministic §5 scope, access, capability and readiness evidence as JSON."""

from __future__ import annotations

import json
from pathlib import Path

from polycodebench_services.benchmark_audit_catalog import (
    build_scope_conformance_report,
    load_audit_catalog,
)

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    bundle = load_audit_catalog(ROOT / "config" / "benchmark-audit")
    report = build_scope_conformance_report(bundle)
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
