"""Internal CLI for Track A finding validation, scoring, cohort summaries and source blockers."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from polycodebench_core.solve_extraction import Finding

from polycodebench_evaluation.track_a import (
    DetectionResult,
    FindingDisposition,
    MatchEdge,
    OracleBug,
    TaskSourceProvenance,
    TrackACohort,
    TrackASample,
    aggregate_track_a,
    mutate_source,
    parse_findings,
    score_detection,
    source_requirement_report,
    validate_source_admission,
)


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def _base_files(root: Path) -> dict[str, bytes]:
    if not root.is_dir():
        raise ValueError("base snapshot directory does not exist")
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and not any(part.startswith(".") for part in path.relative_to(root).parts)
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pcb-track-a")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "source-requirements", help="show currently blocked external source requirements"
    )
    validate = subparsers.add_parser(
        "validate-findings", help="validate a findings envelope against a base snapshot"
    )
    validate.add_argument("--base", type=Path, required=True)
    validate.add_argument("--input", type=Path, required=True)
    validate.add_argument("--max-findings", type=int, default=20)
    score = subparsers.add_parser(
        "score-detection",
        help="score a sample; reviewer decisions fail closed without a trusted verifier",
    )
    score.add_argument("--input", type=Path, required=True)
    source = subparsers.add_parser(
        "validate-source", help="validate task provenance and rights evidence"
    )
    source.add_argument("--input", type=Path, required=True)
    mutation = subparsers.add_parser(
        "mutate-source", help="apply one proven, pinned source mutation"
    )
    mutation.add_argument("--input", type=Path, required=True)
    mutation.add_argument("--rule", type=Path, required=True)
    mutation.add_argument("--output", type=Path, required=True)
    mutation.add_argument("--record", type=Path, required=True)
    aggregate = subparsers.add_parser(
        "aggregate", help="aggregate fixed-cohort Track A sample JSON"
    )
    aggregate.add_argument("--input", type=Path, required=True)
    aggregate.add_argument("--entry-id", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "source-requirements":
            result: Any = source_requirement_report()
        elif args.command == "validate-findings":
            result = parse_findings(
                args.input.read_bytes(),
                base_files=_base_files(args.base),
                max_findings=args.max_findings,
            )
        elif args.command == "score-detection":
            body = json.loads(args.input.read_text(encoding="utf-8"))
            result = score_detection(
                findings=tuple(
                    Finding.model_validate_json(json.dumps(v)) for v in body["findings"]
                ),
                bugs=tuple(OracleBug.model_validate_json(json.dumps(v)) for v in body["bugs"]),
                edges=tuple(
                    MatchEdge.model_validate_json(json.dumps(v)) for v in body.get("edges", [])
                ),
                dispositions=tuple(
                    FindingDisposition.model_validate_json(json.dumps(v))
                    for v in body.get("dispositions", [])
                ),
                schema_valid=bool(body.get("schema_valid", True)),
                duplicate_of=tuple(tuple(v) for v in body.get("duplicate_of", [])),
            )
        elif args.command == "validate-source":
            source_record = TaskSourceProvenance.model_validate_json(
                args.input.read_text(encoding="utf-8")
            )
            result = validate_source_admission(source_record)
        elif args.command == "mutate-source":
            rule = json.loads(args.rule.read_text(encoding="utf-8"))
            mutated, record = mutate_source(
                args.input.read_bytes(),
                path=rule["path"],
                old=rule["old"].encode("utf-8"),
                new=rule["new"].encode("utf-8"),
                operator=rule["operator"],
                operator_version=rule["operator_version"],
                seed=rule["seed"],
                behavior_change_proof=rule["behavior_change_proof"],
                reference_repair_proof=rule["reference_repair_proof"],
                build_proof=rule["build_proof"],
                equivalent_to_existing=rule.get("equivalent_to_existing", False),
                duplicate_mutation=rule.get("duplicate_mutation", False),
                unintended_or_nonbuilding=rule.get("unintended_or_nonbuilding", False),
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.record.parent.mkdir(parents=True, exist_ok=True)
            temporary_output = args.output.with_name(args.output.name + ".tmp")
            temporary_record = args.record.with_name(args.record.name + ".tmp")
            temporary_output.write_bytes(mutated)
            temporary_record.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")
            temporary_output.replace(args.output)
            temporary_record.replace(args.record)
            result = record
        else:
            body = json.loads(args.input.read_text(encoding="utf-8"))
            cohort = TrackACohort.model_validate(body["cohort"])
            samples = tuple(TrackASample.model_validate(v) for v in body["samples"])
            result = aggregate_track_a(cohort, samples, args.entry_id)
        print(_json(result))
        if isinstance(result, DetectionResult) and result.status == "invalid_submission":
            return 2
        if args.command == "validate-source" and not result.admitted:
            return 2
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
