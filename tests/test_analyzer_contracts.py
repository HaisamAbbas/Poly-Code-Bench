"""PCB-12-2: analyzer execution/parsing contracts and normalized evidence (offline).

These tests replay *recorded* real tool executions (tests/fixtures/*_tool_output, captured by
running the pinned analyzers in the pinned images) and assert the execution/parsing contract
Prompt 12 depends on:

* an empty report is a missing scan, not a clean one, unless the tool's own declared contract
  proves emptiness clean (mypy is the only such case in the pilot);
* a crash, an undeclared exit, a timeout, a missing report and an unsupported required check are
  each an explicit, distinguishable failure - never a measured scan with zero findings;
* normalized observations name the raw report they were parsed from, so a reviewer can get from
  a finding back to the exact bytes that produced it, and the same evidence names them again on
  replay;
* tool, rule-bundle, parser, output-schema and advisory-snapshot identity is preserved, and a
  dependency audit that ran without an advisory snapshot says so instead of looking clean.

Container-level behaviour is covered by tests/test_evaluator_docker.py; nothing here needs a
sandbox.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from polycodebench_core.canonical import sha256_bytes
from polycodebench_core.identity import derived_entity_id
from polycodebench_core.models import Artifact, MeasurementStatus, Observation
from polycodebench_lang_python import PythonLanguagePlugin
from polycodebench_lang_python.observations import raw_report_ids
from polycodebench_lang_rust import RustLanguagePlugin
from polycodebench_plugins_api import AnalysisContext, DictArtifactReader, plan_status
from polycodebench_plugins_api.contracts import PlanOutput, ToolIdentity
from pydantic import ValidationError
from python_plugin_support import Recording, frozen
from rust_plugin_support import Recording as RustRecording
from rust_plugin_support import frozen as rust_frozen

plugin = PythonLanguagePlugin()
rust = RustLanguagePlugin()
MEASURED = MeasurementStatus.MEASURED
MISSING = MeasurementStatus.MISSING


def parse(rec: Recording, **reader: Any) -> list[Observation]:
    return plugin.parse_analysis(rec.reader(**reader), rec.plan)


def rust_rec(scenario: str, analyzer: str) -> RustRecording:
    return RustRecording(scenario, f"rust.analysis.{analyzer}")


def rust_parse(rec: RustRecording, **reader: Any) -> list[Observation]:
    return rust.parse_analysis(rec.reader(**reader), rec.plan)


def scan(observations: list[Observation], tool: str, language: str = "python") -> Observation:
    return next(o for o in observations if o.check_id == f"{language}.{tool}.scan")


def incomplete(observations: list[Observation], tool: str, language: str = "python") -> bool:
    """A missing scan: one observation, MISSING, and crucially no value at all."""
    return (
        len(observations) == 1
        and scan(observations, tool, language).status == MISSING
        and scan(observations, tool, language).value is None
    )


def _record(rec: Any, **changes: Any) -> dict[str, Any]:
    return {**copy.deepcopy(rec.record), **changes}


# ============================================================ empty reports are not clean


@pytest.mark.parametrize("tool", ["ruff", "bandit", "semgrep", "context"])
def test_an_empty_report_is_a_missing_scan_not_a_clean_one(tool: str) -> None:
    """A clean exit that wrote nothing is an unanswered check (Technical Spec 12.3)."""
    rec = Recording("clean", tool)
    empty = {**rec.files, f"out/{tool}.json": b""}
    assert plan_status(rec.plan, rec.reader(files=empty))[0] == "empty_report"
    assert incomplete(parse(rec, files=empty), tool)
    # whitespace-only output is equally unproven
    assert incomplete(parse(rec, files={**rec.files, f"out/{tool}.json": b"  \n"}), tool)


@pytest.mark.parametrize("tool", ["ruff", "bandit", "semgrep", "context"])
def test_a_populated_clean_report_is_measured_zero(tool: str) -> None:
    """The control: real bytes plus exit 0 *is* a clean scan for these tools."""
    observations = parse(Recording("clean", tool))
    assert len(observations) == 1
    assert scan(observations, tool).status == MEASURED and scan(observations, tool).value == 0


def test_mypy_is_the_one_declared_empty_is_clean_case() -> None:
    """mypy's own contract: exit 0 with empty stdout *is* its documented clean result."""
    typed = Recording("typed-bad", "mypy")
    assert typed.plan.outputs[0].empty_is_clean is True
    # the declaration is per-output and per-tool, not a global switch
    assert Recording("clean", "ruff").plan.outputs[0].empty_is_clean is False
    empty = {**typed.files, "out/mypy.json": b""}
    # a *findings* exit with no error rows is still a contradiction, empty-is-clean notwithstanding
    assert plan_status(typed.plan, typed.reader(files=empty))[0] == "completed_with_findings"
    assert incomplete(parse(typed, files=empty), "mypy")
    # exit 0 with an empty report is the one case mypy's contract does allow
    assert (
        plan_status(
            typed.plan,
            DictArtifactReader(
                {
                    "out/mypy.json": b"",
                    "_execution.json": json.dumps(_record(typed, exit_code=0)).encode(),
                }
            ),
        )[0]
        == "completed"
    )
    observations = parse(typed, files=empty, record=_record(typed, exit_code=0))
    assert scan(observations, "mypy").status == MEASURED and scan(observations, "mypy").value == 0


def test_a_rust_report_that_is_empty_is_also_missing() -> None:
    """No Rust analyzer declares emptiness clean, including the context scanner."""
    rec = rust_rec("reference", "context")
    empty = {**rec.files, "out/context.json": b""}
    assert plan_status(rec.plan, rec.reader(files=empty))[0] == "empty_report"
    assert incomplete(rust_parse(rec, files=empty), "context", "rust")


# ====================================================== distinct failure semantics


@pytest.mark.parametrize("tool", ["ruff", "bandit", "semgrep", "context"])
def test_crash_timeout_and_undeclared_exit_are_all_missing_never_clean(tool: str) -> None:
    rec = Recording("clean", tool)
    for label, record in {
        "declared error": _record(rec, exit_code=2),
        "undeclared crash": _record(rec, exit_code=99),
        "timeout": _record(rec, exit_code=None, timed_out=True),
    }.items():
        assert incomplete(parse(rec, record=record), tool), label
    assert incomplete(parse(rec, drop_record=True), tool)  # no supervisor record at all


def test_every_failure_status_is_reachable_and_distinguishable() -> None:
    """The contract exposes the failure classes, not one undifferentiated 'failed'."""
    rec = Recording("clean", "ruff")
    cases = {
        "timed_out": (rec.reader(record=_record(rec, exit_code=None, timed_out=True)), "timed_out"),
        "tool_error": (rec.reader(record=_record(rec, exit_code=2)), "tool_error"),
        "undeclared": (rec.reader(record=_record(rec, exit_code=99)), "tool_error"),
        "output_missing": (rec.reader(drop=("out/ruff.json",)), "output_missing"),
        "empty_report": (
            rec.reader(files={**rec.files, "out/ruff.json": b""}),
            "empty_report",
        ),
        "no_record": (rec.reader(drop_record=True), "tool_error"),
        "findings": (rec.reader(record=_record(rec, exit_code=1)), "completed_with_findings"),
        "clean": (rec.reader(), "completed"),
    }
    for label, (reader, expected) in cases.items():
        assert plan_status(rec.plan, reader)[0] == expected, label


def test_unsupported_miri_is_not_applicable_not_clean_and_not_missing() -> None:
    """Three states stay distinct: clean (measured 0), candidate UB, unsupported (N/A)."""
    clean = rust_parse(rust_rec("reference", "miri"))
    unsupported = rust_parse(rust_rec("unsupported", "miri"))
    ub = rust_parse(rust_rec("ub", "miri"))
    assert scan(clean, "miri", "rust").status == MEASURED
    assert scan(unsupported, "miri", "rust").status == MeasurementStatus.NOT_APPLICABLE
    assert scan(unsupported, "miri", "rust").value is None
    assert scan(ub, "miri", "rust").status == MEASURED and scan(ub, "miri", "rust").value == 1


def test_a_required_scan_that_is_unsupported_makes_dependent_items_missing_not_perfect() -> None:
    """The DoD in one assertion: an unsupported required check cannot yield a perfect score."""
    observations = rust.normalize(rust_parse(rust_rec("unsupported", "miri")))
    result = rust.rust_profile.evaluate(
        opportunities={"unsafe_soundness": 1},
        observations=observations,
        required_tools=("miri",),
    )
    item = next(i for i in result.diagnostic if i.item_id == "unsafe_soundness")
    assert item.status == "missing"
    assert item.reasons == ("required scan unsupported: miri",)
    assert result.diagnostic_score_bp is None  # neither 10000 nor any other invented number


def test_an_empty_report_makes_dependent_items_missing_not_perfect() -> None:
    """Same DoD for the empty-report branch: dependent items, not a perfect aggregate."""
    rec = Recording("clean", "context")
    broken = parse(rec, files={**rec.files, "out/context.json": b""})
    observed = [*parse(Recording("clean", "ruff")), *broken]
    result = plugin.python_profile.evaluate(
        opportunities={"error_handling": 2, "lint_style": 1},
        observations=plugin.normalize(observed),
        required_tools=("ruff", "context"),
    )
    states = {i.item_id: i.status for i in result.diagnostic if i.status != "not_applicable"}
    assert states == {"error_handling": "missing", "lint_style": "measured"}
    assert result.diagnostic_score_bp is None


# ====================================== raw report references on normalized evidence


@pytest.mark.parametrize("tool", ["ruff", "bandit", "semgrep", "context"])
def test_findings_name_the_raw_reports_they_were_parsed_from(tool: str) -> None:
    rec = Recording("defects", tool)
    expected = raw_report_ids(rec.plan)
    assert expected
    for obs in parse(rec):
        assert obs.raw_artifact_ids == expected, obs.check_id


def test_the_context_scanner_is_named_by_its_own_tool_identity() -> None:
    """The context scanner runs as ``context-scan``; its reports are named by that tool."""
    rec = Recording("defects", "context")
    assert rec.plan.tool.name == "context-scan"
    assert raw_report_ids(rec.plan) == [
        derived_entity_id(rec.plan.plan_id, "context-scan", "out/context.json")
    ]
    assert raw_report_ids(Recording("defects", "bandit").plan) == [
        derived_entity_id(Recording("defects", "bandit").plan.plan_id, "bandit", "out/bandit.json")
    ]


def test_rust_findings_also_carry_raw_report_references() -> None:
    rec = rust_rec("defects", "clippy")
    observations = rust_parse(rec)
    assert observations
    expected = [
        derived_entity_id(rec.plan.plan_id, "clippy", output.path)
        for output in rec.plan.outputs
        if output.required
    ]
    assert expected
    for obs in observations:
        assert obs.raw_artifact_ids == expected, obs.check_id


def test_raw_report_ids_are_stable_across_runs() -> None:
    """Replay (E2E-24) needs the same evidence to name the same artifacts."""
    first = parse(Recording("defects", "bandit"))
    second = parse(Recording("defects", "bandit"))
    assert [o.raw_artifact_ids for o in first] == [o.raw_artifact_ids for o in second]


def test_derived_entity_id_is_uuid_v4_shaped_and_content_addressed() -> None:
    from polycodebench_core.models import Visibility

    first = derived_entity_id("stage", "plan", "out/report.json")
    assert first == derived_entity_id("stage", "plan", "out/report.json")
    assert first != derived_entity_id("stage", "plan", "out/other.json")
    # it must satisfy the contract's EntityId shape, so it can back a real Artifact row
    artifact = Artifact.model_validate(
        {
            "schema_version": 1,
            "kind": "artifact",
            "artifact_id": first,
            "digest": sha256_bytes(b"x"),
            "media_type": "application/json",
            "byte_size": 1,
            "visibility": Visibility.INTERNAL,
            "producing_stage": "evaluation",
            "retention_policy": "evidence",
        }
    )
    assert artifact.artifact_id == first


# ==================================== tool / rule / parser / advisory identity is kept


def test_tool_identity_records_rule_bundle_parser_and_schema_versions() -> None:
    plans = plugin.analysis_plans(
        AnalysisContext(
            task=frozen(plugin),
            candidate_digest="sha256:" + "2" * 64,
            candidate_paths=("solution.py",),
        )
    )
    ruff_plan = next(p for p in plans if p.analyzer_id == "ruff")
    assert ruff_plan.tool.rule_bundle_digest == plugin.identities.rule_bundle_digest
    assert ruff_plan.tool.version == plugin.identities.evaluator.tools["ruff"]
    assert ruff_plan.tool.parser_version.startswith("pcb-python-parsers")
    assert ruff_plan.parser_id == "python-ruff"
    assert ruff_plan.output_schema == "ruff-json-v1"
    assert ruff_plan.tool.advisory_snapshot_state == "not_applicable"
    assert ruff_plan.tool.advisory_snapshot_digest is None


def test_a_dependency_audit_that_ran_without_an_advisory_snapshot_says_absent() -> None:
    """An absent advisory database is recorded, so 'clean' can never be claimed by accident."""
    view = frozen(plugin, dependency_inventory=("requests",))
    plans = {
        p.analyzer_id: p
        for p in plugin.analysis_plans(
            AnalysisContext(
                task=view, candidate_digest="sha256:" + "4" * 64, candidate_paths=("solution.py",)
            )
        )
    }
    audit = plans["dependency"]
    assert audit.tool.advisory_snapshot_state == "absent"
    assert audit.tool.advisory_snapshot_digest is None
    # the check still runs and is still parsed fail-closed rather than as a clean audit
    assert audit.plan_id == "python.analysis.dependency"


def test_rust_dependency_audit_records_its_pinned_snapshot_digest() -> None:
    from polycodebench_lang_rust import plans as rust_plans

    view = rust_frozen(rust, dependency_inventory=("serde",))
    plans = {
        p.analyzer_id: p
        for p in rust.analysis_plans(
            AnalysisContext(
                task=view, candidate_digest="sha256:" + "4" * 64, candidate_paths=("src/lib.rs",)
            )
        )
    }
    audit = plans["dependency"]
    assert audit.tool.advisory_snapshot_state == "pinned"
    assert audit.tool.advisory_snapshot_digest == sha256_bytes(rust_plans.advisory_snapshot())
    assert plans["clippy"].tool.advisory_snapshot_state == "not_applicable"
    assert plans["miri"].tool.lock_digest  # the task's Cargo.lock is part of its identity


def test_identity_validation_rejects_inconsistent_advisory_state() -> None:
    base = plugin.identities.tool("ruff").model_dump(mode="json")
    with pytest.raises(ValidationError, match="pinned advisory snapshot"):
        ToolIdentity.model_validate({**base, "advisory_snapshot_state": "pinned"})
    with pytest.raises(ValidationError, match="no advisory data"):
        ToolIdentity.model_validate(
            {
                **base,
                "advisory_snapshot_state": "not_applicable",
                "advisory_snapshot_digest": "sha256:" + "3" * 64,
            }
        )


def test_an_analysis_plan_must_declare_a_required_report_output() -> None:
    plan = Recording("clean", "ruff").plan
    document = {
        **plan.model_dump(mode="json"),
        "outputs": [
            {
                "kind": "plan_output",
                "path": "out/ruff.json",
                "format": "json",
                "required": False,
                "max_bytes": 4194304,
                "empty_is_clean": False,
            }
        ],
    }
    # PluginModel is strict, so the document goes through the JSON path the contract uses.
    with pytest.raises(ValidationError, match="required report output"):
        type(plan).model_validate_json(json.dumps(document))
    # and the declared-empty variant is a valid contract in its own right
    PlanOutput(path="out/ruff.json", format="json", required=True, empty_is_clean=True)


# ================================== the supervisor never reads the tool's own claims


@pytest.mark.parametrize("tool", ["ruff", "context", "bandit", "semgrep"])
def test_a_clean_exit_that_still_reports_findings_is_contradictory(tool: str) -> None:
    rec = Recording("defects", tool)
    assert incomplete(parse(rec, record=_record(rec, exit_code=0)), tool)


def test_a_findings_exit_with_no_findings_is_contradictory() -> None:
    rec = Recording("defects", "bandit")
    emptied = json.loads(rec.files["out/bandit.json"]) | {"results": []}
    files = rec.files | {"out/bandit.json": json.dumps(emptied).encode()}
    assert incomplete(parse(rec, files=files), "bandit")
