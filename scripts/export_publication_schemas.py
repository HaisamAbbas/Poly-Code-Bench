"""Generate/check strict, versioned internal metric/cohort/report schemas."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/publication/src"))

from polycodebench_publication.aggregation import (  # noqa: E402
    AggregateResult,
    BootstrapConfig,
    CohortPolicy,
    CohortTask,
    MetricDefinition,
    Observation,
    StratumWeight,
    UncertaintyResult,
)
from polycodebench_publication.reporting import ReportRequest  # noqa: E402

MODELS = (
    MetricDefinition,
    CohortTask,
    StratumWeight,
    CohortPolicy,
    Observation,
    AggregateResult,
    BootstrapConfig,
    UncertaintyResult,
    ReportRequest,
)


def generated_outputs() -> dict[Path, str]:
    outputs = {}
    for model in MODELS:
        filename = f"{model.__name__}.v1.schema.json"
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = "https://polycodebench.invalid/schemas/publication/" + filename
        outputs[ROOT / "schemas/publication" / filename] = (
            json.dumps(
                schema,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    outputs = generated_outputs()
    stale = [
        str(path.relative_to(ROOT))
        for path, content in outputs.items()
        if not path.exists() or path.read_text(encoding="utf-8") != content
    ]
    if args.check:
        if stale:
            print("Publication schemas are stale: " + ", ".join(stale), file=sys.stderr)
            return 1
        print(f"Publication schemas: PASS ({len(outputs)} files)")
        return 0
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    print(f"Generated {len(outputs)} publication schemas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
