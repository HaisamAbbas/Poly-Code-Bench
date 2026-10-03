"""Produce the Prompt 24 E2E-36 evidence document from a real run.

This is the evidence generator for ``docs/implementation/evidence/prompt-24-e2e36.json``: it imports
both fixtures through the adapter, grades every candidate patch, and records what the run actually
observed. Run it with:

    uv run python plugins/suites/swebench/scripts/prompt24_evidence.py

It is a script rather than a test because the evidence document is an artifact of a run, while the
E2E-36 scenario itself is pinned by ``tests/test_e2e36_native_repo_repair.py``.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "plugins" / "suites" / "swebench" / "src"))
sys.path.insert(0, str(ROOT / "plugins" / "suites" / "swebench" / "tests"))

import adapted_port_instance as adapted  # noqa: E402
import native_compatible_instance as native  # noqa: E402
from fixture_support import write_instance, write_manifest  # noqa: E402
from polycodebench_suites_swebench import (  # noqa: E402
    SourceManifest,
    SweBenchStyleSuiteAdapter,
    UpstreamRunCache,
    evaluator_digest,
    grade_native_candidate,
    upstream_revision,
)
from polycodebench_suites_swebench.image_runner import DockerTestRunner  # noqa: E402

REGISTER = yaml.safe_load(
    (ROOT / "config" / "methodology" / "deviations-v1.yaml").read_text(encoding="utf-8")
)

START = ">>>>> Start Test Output"
END = ">>>>> End Test Output"
EXIT = ">>>>> Test Exit Code"

#: The pinned Rust runtime image, by digest. The port's test command runs in this image, so the
#: port is compiled rather than described.
RUST_RUNTIME_IMAGE = (
    "pcb-rust-runtime@sha256:a3f88da16577b2516a9f243811fe407bf58158030686b869e8fb84b203bdfb69"
)


def cargo_runner() -> DockerTestRunner:
    """Run the Rust port's frozen command in the pinned Rust image.

    The port is executed rather than described: a candidate that does not compile produces a Rust
    error on stderr, which the upstream ``parse_log_cargo`` reads as an unrun suite and therefore as
    unresolved. That is the honest outcome for a broken port, and it is only reachable if the
    command really runs.
    """
    return DockerTestRunner(
        RUST_RUNTIME_IMAGE,
        # The argv is the record's frozen ``test_command``, split as a typed vector. It carries no
        # ``--quiet``: each test must print the ``test <name> ... ok`` line the upstream
        # ``parse_log_cargo`` reads, and a quiet run prints dots it cannot read.
        argv=tuple(adapted.TEST_COMMAND.split()),
    )


def grade_fixture(adapter: SweBenchStyleSuiteAdapter, module: Any, name: str, root: Path) -> dict:
    """Import one fixture, validate its record and grade every candidate patch."""
    record_path = write_instance(root, record=module.record(), snapshot=module.BASE)
    manifest = SourceManifest.from_yaml(
        write_manifest(
            root,
            {
                "schema_version": 1,
                "kind": "suite_source_manifest",
                "name": name,
                "dataset_revision": "polycodebench-authored-2026-10-03",
                "source_url": None,
                "authored": True,
                "license_expression": "CC0-1.0",
                "records": [record_path.relative_to(root).as_posix()],
            },
        )
    )
    draft = adapter.import_tasks(manifest)[0]
    protocol = adapter.protocol(draft)
    validation = adapter.validate_methodology(draft.methodology)
    # A compiled language runs its frozen command in the pinned image; the Python fixture runs it
    # on the host. Either way the log is graded by the same pinned upstream evaluator.
    runner = cargo_runner() if draft.instance.log_parser != "parse_log_pytest" else None

    cache = UpstreamRunCache()
    graded: dict[str, Any] = {}
    for candidate, patch in module.candidate_patches().items():
        outcome = grade_native_candidate(
            draft,
            candidate_patch=patch,
            test_command=protocol.test_command,
            runner=runner,
            cache=cache,
        )
        graded[candidate] = {
            "run_identity": outcome.run_identity,
            "candidate_digest": outcome.result.candidate_digest,
            "resolved": outcome.result.resolved,
            "resolution_status": outcome.result.resolution_status,
            "native_metrics": outcome.result.native_metrics,
            "from_cache": outcome.from_cache,
            "test_edit_overwritten": outcome.test_edit_overwritten,
        }
    return {
        "instance_id": draft.instance.instance_id,
        "methodology_label": draft.methodology.compatibility_level,
        "methodology_validation_ok": validation.ok,
        "methodology_issues": list(validation.issues),
        "task_digest": draft.package_digest,
        "log_parser": draft.instance.log_parser,
        "test_command": protocol.test_command,
        "fail_to_pass": list(draft.instance.fail_to_pass),
        "pass_to_pass": list(draft.instance.pass_to_pass),
        "protocol_deviations": list(draft.methodology.deviations),
        "upstream_source_url": draft.methodology.upstream_source_url,
        "graded_candidates": graded,
        "cache_entries": len(cache),
    }


def main() -> int:
    adapter = SweBenchStyleSuiteAdapter(deviations=REGISTER)
    root = Path(tempfile.mkdtemp(prefix="pcb-prompt-24-evidence-"))
    fixtures = [
        grade_fixture(adapter, native, "pcb-authored-native-compatible", root),
        grade_fixture(adapter, adapted, "pcb-authored-adapted-port", root),
    ]
    document: dict[str, Any] = {
        "schema_version": 1,
        "kind": "prompt_24_native_repo_repair_evidence",
        "execution_tier": "local_fixture",
        "upstream_evaluator": {
            "package": "swebench",
            "revision": upstream_revision(),
            "evaluator_digest": evaluator_digest(),
        },
        "quality_admission": "pending",
        "not_claimed": [
            "no official SWE-bench dataset instance is imported; dataset, repository and patch "
            "rights are unrecorded blockers, so both fixtures are authored",
            "no production-worker grading run occurred; the Rust port executes in the pinned "
            "Rust image through the local Docker driver, which is development-tier evidence",
            "the Python fixture's frozen command runs on the host rather than in a pinned image",
        ],
        "fixtures": fixtures,
    }
    body = json.dumps(document, sort_keys=True, ensure_ascii=False).encode("utf-8")
    document["report_digest"] = "sha256:" + hashlib.sha256(body).hexdigest()
    out = ROOT / "docs" / "implementation" / "evidence" / "prompt-24-e2e36.json"
    out.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)}")
    for fixture in fixtures:
        outcomes = {k: v["resolved"] for k, v in fixture["graded_candidates"].items()}
        print(f"  {fixture['instance_id']} -> {fixture['methodology_label']} {outcomes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
