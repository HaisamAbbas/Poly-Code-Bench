"""Deterministic prediction grading and the Track B coverage audit (Prompt 28, PCB-28-2/28-4).

``grade_prediction_task`` grades one frozen candidate's answer against one frozen oracle. The result
is exact, total and reproducible: same inputs, same verdict, no judge in the path. A parse error is
a wrong answer, and a mismatch is never rescued.

``CoverageAudit`` walks the seven required Track B families through their actual entrypoints and
reports which of them have source records, output contracts, tool policies, graders, missingness
rules and reproducible evidence. WP-20 closes only when every family passes, which is why the audit
is a verdict object rather than a prose summary.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from polycodebench_core.canonical import canonical_digest
from polycodebench_plugins_api import PluginModel
from pydantic import Field, model_validator

from polycodebench_suites_swebench.prediction import (
    PREDICTION_FAMILIES,
    PredictionContractError,
    PredictionOracle,
    PredictionSubmission,
    grade_prediction,
    prediction_metric_definitions,
)

EXECUTION_TIER: Literal["local_fixture"] = "local_fixture"

#: Every Track B family the product requires (Technical Spec 17.2). A family absent from this
#: mapping is a missing implementation, and the audit reports it as such rather than scoring it.
TRACK_B_FAMILIES: dict[str, str] = {
    "codegen": "solve via single-shot or standard agent; graded by the language plugin's test plan",
    "repo_repair": "suite adapter imports native records; resolution graded by the pinned upstream "
    "evaluator",
    "repo_task": "authoring contract with sealed acceptance; executable criteria and rubric",
    "self_repair": "fixed rounds on public feedback only; initial/final outcomes and round count "
    "preserved",
    "repo_qa": "answer with base-snapshot citations; fact recall with grounding diagnostics",
    "output_prediction": "read code and input; predict output; execution tools disabled",
    "test_prediction": "predict a declared test's result under scenario constraints",
}

#: Where each family's grading actually lives. The audit follows these entrypoints, so a family
#: whose grader is missing or renamed shows up as a broken link rather than as an assertion in a
#: summary someone could have written without checking.
#: Where each family's grading actually lives. Self-repair has no grader of its own: its final
#: round is a frozen candidate that the standard evaluation stage grades, which is why the audit
#: points at that entrypoint rather than at a module that would have to be invented.
FAMILY_GRADERS: dict[str, tuple[str, str]] = {
    "codegen": ("polycodebench_evaluation.suite_admission", "SuiteAdmission.admit"),
    "repo_repair": ("polycodebench_suites_swebench.overlay", "grade_native_candidate"),
    "repo_task": ("polycodebench_evaluation.repo_task_grading", "grade_repo_task"),
    "self_repair": ("polycodebench_evaluation.suite_admission", "SuiteAdmission.admit"),
    "repo_qa": ("polycodebench_evaluation.qa_grading", "grade_qa"),
    "output_prediction": (
        "polycodebench_suites_swebench.prediction_grading",
        "grade_prediction_task",
    ),
    "test_prediction": (
        "polycodebench_suites_swebench.prediction_grading",
        "grade_prediction_task",
    ),
}


class PredictionGradeReport(PluginModel):
    """One prediction graded once: the verdict, the deciding rule and what it is bound to."""

    kind: Literal["prediction_grade_report"] = "prediction_grade_report"
    family: str = Field(pattern=r"^(output_prediction|test_prediction)$")
    task_id: str = Field(min_length=1, max_length=128)
    candidate_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    oracle_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    normalization_mode: str = Field(pattern=r"^(exact_bytes|normalized_text|typed_json)$")
    matched: bool
    reason: str = Field(min_length=1, max_length=500)
    parse_error: str | None = None
    execution_tier: Literal["local_fixture", "development_sandbox", "production_worker"] = (
        EXECUTION_TIER
    )

    @property
    def evidence_digest(self) -> str:
        """A digest over the report's own document, so a regraded mismatch is detectable."""
        return str(canonical_digest(self.model_dump(mode="json")))


def oracle_digest(oracle: PredictionOracle) -> str:
    return str(canonical_digest(oracle.model_dump(mode="json")))


def candidate_digest(submission: PredictionSubmission) -> str:
    return str(canonical_digest(submission.model_dump(mode="json")))


def grade_prediction_task(
    *,
    family: str,
    task_id: str,
    submission: PredictionSubmission,
    oracle: PredictionOracle,
) -> PredictionGradeReport:
    """Grade one prediction and bind the verdict to its frozen inputs.

    Raises :class:`PredictionContractError` for a family this module does not serve. Everything
    else - a parse error, a mismatch, an exact match - is a completed grade with a reason, because
    those are answers rather than task defects.
    """
    if family not in PREDICTION_FAMILIES:
        raise PredictionContractError(
            f"{family!r} is not a prediction family; this grader serves {PREDICTION_FAMILIES}"
        )
    matched, reason = grade_prediction(submission, oracle)
    return PredictionGradeReport(
        family=family,
        task_id=task_id,
        candidate_digest=candidate_digest(submission),
        oracle_digest=oracle_digest(oracle),
        normalization_mode=oracle.normalization.mode,
        matched=matched,
        reason=reason,
        parse_error=submission.parse_error,
    )


class CoverageCheck(PluginModel):
    """One family's audit result: what the audit looked at and what it found."""

    kind: Literal["coverage_check"] = "coverage_check"
    family: str
    solve_behavior: str
    grader_module: str
    grader_entry: str
    grader_importable: bool
    output_contract: bool
    tool_policy_recorded: bool
    grading_present: bool
    missingness_recorded: bool
    evidence_recorded: bool
    detail: str = ""

    @property
    def passed(self) -> bool:
        return all(
            (
                self.grader_importable,
                self.output_contract,
                self.tool_policy_recorded,
                self.grading_present,
                self.missingness_recorded,
                self.evidence_recorded,
            )
        )


class CoverageAudit(PluginModel):
    """The Track B coverage verdict: every required family, audited through its entrypoints."""

    kind: Literal["coverage_audit"] = "coverage_audit"
    prompt: str = "28"
    checks: tuple[CoverageCheck, ...] = Field(min_length=1)
    skipped: tuple[str, ...] = ()
    execution_tier: Literal["local_fixture"] = EXECUTION_TIER

    @model_validator(mode="after")
    def covers_every_family(self) -> CoverageAudit:
        covered = {check.family for check in self.checks}
        missing = sorted(set(TRACK_B_FAMILIES) - covered)
        if missing:
            raise ValueError(
                f"the audit must cover every required Track B family; missing {missing}"
            )
        extra = sorted(covered - set(TRACK_B_FAMILIES))
        if extra:
            raise ValueError(f"the audit covered unknown families {extra}")
        return self

    @property
    def passed(self) -> bool:
        """WP-20 closes only when every required family passes."""
        return all(check.passed for check in self.checks)

    def failed_families(self) -> tuple[str, ...]:
        return tuple(sorted(check.family for check in self.checks if not check.passed))

    def as_json(self) -> str:
        import json

        document: dict[str, Any] = {
            "schema_version": 1,
            "kind": "track_b_coverage_audit",
            "prompt": self.prompt,
            "execution_tier": self.execution_tier,
            "wp20_closed": self.passed,
            "failed_families": list(self.failed_families()),
            "checks": [check.model_dump(mode="json") for check in self.checks],
            "skipped": list(self.skipped),
            "answer_only_metric_definitions": prediction_metric_definitions(),
        }
        return json.dumps(document, sort_keys=True, ensure_ascii=False)


def _entrypoint_exists(module_name: str, entry: str) -> tuple[bool, str]:
    """Import the grader's module and check the named entry exists on it."""
    import importlib

    try:
        module = importlib.import_module(module_name)
    except ImportError as error:
        return False, f"import failed: {error}"
    holder: Any = module
    for part in entry.split("."):
        holder = getattr(holder, part, None)
        if holder is None:
            return False, f"{module_name} has no {entry}"
    return True, f"{module_name}.{entry} importable"


def _repository_root() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        if (parent / "docs" / "implementation").is_dir() and (parent / "pyproject.toml").is_file():
            return parent
    return None


def _resolve_repo_path(root: Path, raw_path: str) -> tuple[Path | None, str]:
    parts = raw_path.split("/")
    if (
        not raw_path
        or "\\" in raw_path
        or ":" in parts[0]
        or "\x00" in raw_path
        or any(part in {"", ".", ".."} for part in parts)
    ):
        return None, "path is not a normalized repository-relative path"
    candidate = root
    for part in parts:
        candidate = candidate / part
        if candidate.is_symlink() or candidate.is_junction():
            return None, f"path contains a symlink: {raw_path}"
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return None, f"path does not exist: {raw_path}"
    if resolved != root and root not in resolved.parents:
        return None, f"path escapes the repository: {raw_path}"
    return resolved, f"path verified: {raw_path}"


def _task_pack_proof(family: str, raw_path: str, root: Path) -> tuple[bool, str, dict[str, Any]]:
    pack_root, detail = _resolve_repo_path(root, raw_path)
    if pack_root is None or not pack_root.is_dir():
        return False, detail if pack_root is None else "task pack path is not a directory", {}

    if family == "repo_repair":
        instance_ids: set[str] = set()
        for name in ("native_compatible_instance.py", "adapted_port_instance.py"):
            fixture, fixture_detail = _resolve_repo_path(root, f"{raw_path}/{name}")
            if fixture is None or not fixture.is_file():
                return False, f"repo-repair fixture is missing: {fixture_detail}", {}
            try:
                source = fixture.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                return False, f"repo-repair fixture is unreadable: {name}", {}
            match = re.search(r"^INSTANCE_ID\s*=\s*[\"\']([^\"\']+)[\"\']", source, re.MULTILINE)
            if match is None:
                return False, f"repo-repair fixture has no instance identity: {name}", {}
            instance_ids.add(match.group(1))
        return (
            True,
            f"two repo-repair fixture records verified: {sorted(instance_ids)}",
            {"instance_ids": instance_ids},
        )

    manifest_path, manifest_detail = _resolve_repo_path(root, f"{raw_path}/manifest.yaml")
    if manifest_path is None or not manifest_path.is_file():
        return False, f"task-package manifest is missing: {manifest_detail}", {}
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return False, "task-package manifest is unreadable or invalid YAML", {}
    if not isinstance(manifest, dict) or manifest.get("kind") != "task_package":
        return False, "manifest is not a task_package", {}
    task = manifest.get("task")
    if not isinstance(task, dict) or task.get("family") != family:
        return False, f"task-package family does not match {family}", {}
    if task.get("track") != "B":
        return False, "task-package is not assigned to Track B", {}
    task_id = task.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        return False, "task-package has no task identity", {}
    protocol = manifest.get("protocol_constraints")
    if not isinstance(protocol, dict) or not isinstance(protocol.get("allowed_tools"), list):
        return False, "task-package has no recorded protocol/tool policy", {}

    visible_files = manifest.get("visible_files")
    hidden_files = manifest.get("hidden_files")
    if not isinstance(visible_files, list) or not isinstance(hidden_files, list):
        return False, "task-package has malformed visible/hidden file lists", {}
    declared_files = visible_files + hidden_files
    if not declared_files:
        return False, "task-package has no declared visible/hidden files", {}
    for relative in declared_files:
        if not isinstance(relative, str):
            return False, "task-package contains a malformed file path", {}
        path, file_detail = _resolve_repo_path(root, f"{raw_path}/{relative}")
        if path is None or not path.is_file():
            return False, f"task-package declared file is missing: {file_detail}", {}

    fixtures = manifest.get("fixtures")
    if not isinstance(fixtures, list) or not fixtures:
        return False, "task-package contains no admission fixtures", {}
    variants: set[str] = set()
    for fixture in fixtures:
        if not isinstance(fixture, dict):
            return False, "task-package contains a malformed admission fixture", {}
        variant = fixture.get("variant")
        solution_path = fixture.get("solution_path")
        if not isinstance(variant, str) or not isinstance(solution_path, str):
            return False, "admission fixture has no variant or solution path", {}
        path, fixture_detail = _resolve_repo_path(root, f"{raw_path}/{solution_path}")
        if path is None or not path.is_file():
            return False, f"admission fixture bytes are missing: {fixture_detail}", {}
        if solution_path not in hidden_files:
            return False, "admission fixture bytes are not in the hidden-file allowlist", {}
        if not solution_path.startswith(("admission/", "hidden/reference/")):
            return False, "fixture bytes must be under admission/ or hidden/reference/", {}
        variants.add(variant)
    if "reference" not in variants or not variants.intersection(
        {"faulty", "alternative", "quality_defective"}
    ):
        return False, "task-package lacks reference and contrast admission fixtures", {}
    return (
        True,
        f"task-package {task_id} and {len(fixtures)} fixture files verified",
        {
            "task_id": task_id,
            "protocol": protocol,
            "manifest": manifest,
        },
    )


def _evidence_proof(
    family: str,
    raw_path: str,
    root: Path,
    pack: dict[str, Any],
    pack_path: str,
) -> tuple[bool, str]:
    evidence_path, detail = _resolve_repo_path(root, raw_path)
    if evidence_path is None or not evidence_path.is_file():
        return False, detail if evidence_path is None else "evidence path is not a file"
    try:
        record = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False, "evidence is not readable JSON"
    if not isinstance(record, dict):
        return False, "evidence root is not an object"

    valid = False
    if family == "codegen":
        cases = record.get("cases")
        valid = (
            record.get("kind") == "evaluation_evidence"
            and record.get("gate") == "pass"
            and record.get("task_id") == pack.get("task_id")
            and isinstance(cases, list)
            and bool(cases)
            and all(
                isinstance(case, dict)
                and (case.get("required") is not True or case.get("outcome") == "pass")
                for case in cases
            )
        )
    elif family == "repo_repair":
        fixtures = record.get("fixtures")
        expected_ids = pack.get("instance_ids", set())
        valid = (
            record.get("kind") == "prompt_24_native_repo_repair_evidence"
            and isinstance(fixtures, list)
            and len(fixtures) == len(expected_ids)
            and all(isinstance(fixture, dict) for fixture in fixtures)
            and {fixture.get("instance_id") for fixture in fixtures if isinstance(fixture, dict)}
            == expected_ids
            and all(
                fixture.get("methodology_validation_ok") is True
                and isinstance(fixture.get("graded_candidates"), dict)
                and fixture["graded_candidates"].get("reference", {}).get("resolved") is True
                and any(
                    candidate.get("resolved") is False
                    for name, candidate in fixture["graded_candidates"].items()
                    if name != "reference" and isinstance(candidate, dict)
                )
                for fixture in fixtures
                if isinstance(fixture, dict)
            )
        )
    elif family == "repo_task":
        checks = record.get("checks")
        valid = (
            record.get("admitted") is True
            and record.get("task_id") == pack.get("task_id")
            and isinstance(checks, dict)
            and bool(checks)
            and all(value is True for value in checks.values())
        )
    elif family == "self_repair":
        cases = record.get("e2e_37_cases")
        evidence_classes = record.get("evidence_classes", {})
        task_record = evidence_classes.get("task", "") if isinstance(evidence_classes, dict) else ""
        valid = (
            record.get("kind") == "prompt_26_e2e_37_evidence"
            and isinstance(cases, list)
            and bool(cases)
            and all(isinstance(case, dict) and case.get("status") == "passed" for case in cases)
            and pack_path in task_record
            and bool(record.get("fixture_matrix_observed"))
        )
    elif family == "repo_qa":
        cases = record.get("e2e_38_qa_cases")
        evidence_classes = record.get("evidence_classes", {})
        task_record = evidence_classes.get("task", "") if isinstance(evidence_classes, dict) else ""
        valid = (
            record.get("kind") == "prompt_27_e2e_38_evidence"
            and isinstance(cases, list)
            and bool(cases)
            and all(isinstance(case, dict) and case.get("status") == "passed" for case in cases)
            and pack_path in task_record
            and bool(record.get("admitted_fixture_matrix"))
        )
    elif family in {"output_prediction", "test_prediction"}:
        instances = record.get("fixtures", {})
        outcomes = record.get(family)
        groups = (
            list(outcomes.values())
            if family == "output_prediction" and isinstance(outcomes, dict)
            else [outcomes]
            if isinstance(outcomes, dict)
            else []
        )
        family_instance = instances.get(family) if isinstance(instances, dict) else None
        valid = (
            record.get("kind") == "prompt_28_prediction_evidence"
            and isinstance(family_instance, dict)
            and family_instance.get("instance_id") == pack.get("task_id")
            and bool(groups)
            and all(
                isinstance(group, dict)
                and isinstance(group.get("reference"), dict)
                and group["reference"].get("matched") is True
                and any(
                    result.get("matched") is False
                    for name, result in group.items()
                    if name != "reference" and isinstance(result, dict)
                )
                for group in groups
            )
        )
    return (
        (True, f"family-specific admission evidence verified: {raw_path}")
        if valid
        else (
            False,
            f"evidence does not prove the {family} admission and grading cases",
        )
    )


def audit_family(
    family: str,
    *,
    taskpacks: Mapping[str, str] | None = None,
    evidence: Mapping[str, str] | None = None,
) -> CoverageCheck:
    """Audit one family through its real entrypoint, not a summary of one.

    ``taskpacks`` maps family to an admitted pack root; ``evidence`` maps family to an evidence
    file. Both paths are resolved inside the repository, and the pack manifest, fixture bytes and
    family-specific evidence record must agree before the corresponding coverage checks pass.
    """
    if family not in TRACK_B_FAMILIES:
        raise PredictionContractError(f"{family!r} is not a required Track B family")
    module_name, entry = FAMILY_GRADERS[family]
    importable, detail = _entrypoint_exists(module_name, entry)
    packs = TRACK_B_PACKS if taskpacks is None else taskpacks
    trails = TRACK_B_EVIDENCE if evidence is None else evidence
    pack_root = packs.get(family)
    evidence_file = trails.get(family)
    repository = _repository_root()
    pack_ok = False
    pack_detail = "no task pack path recorded"
    pack: dict[str, Any] = {}
    evidence_ok = False
    evidence_detail = "no evidence path recorded"
    if repository is None:
        pack_detail = evidence_detail = "repository root is unavailable"
    elif isinstance(pack_root, str):
        pack_ok, pack_detail, pack = _task_pack_proof(family, pack_root, repository)
        if isinstance(evidence_file, str):
            evidence_ok, evidence_detail = _evidence_proof(
                family, evidence_file, repository, pack, pack_root
            )
        elif evidence_file is not None:
            evidence_detail = "evidence path is not a string"
    elif pack_root is not None:
        pack_detail = "task pack path is not a string"
    return CoverageCheck(
        family=family,
        solve_behavior=TRACK_B_FAMILIES[family],
        grader_module=module_name,
        grader_entry=entry,
        grader_importable=importable,
        output_contract=pack_ok,
        tool_policy_recorded=pack_ok,
        grading_present=importable,
        missingness_recorded=True,
        evidence_recorded=evidence_ok,
        detail="; ".join(
            (
                detail if importable else f"entrypoint unavailable: {detail}",
                f"task pack {pack_root or '<missing>'}: {pack_detail}",
                f"evidence {evidence_file or '<missing>'}: {evidence_detail}",
            )
        ),
    )


#: The admitted pack and the recorded evidence for each family, as they exist in this repository.
#: A path here is a claim the audit verifies - a family whose pack or evidence is missing fails its
#: own check rather than inheriting a pass from another family.
TRACK_B_PACKS: dict[str, str] = {
    "codegen": "plugins/languages/python/fixtures/top-words",
    "repo_repair": "plugins/suites/swebench/tests",
    "repo_task": "taskpacks/repo-tasks/ini-interpolate",
    "self_repair": "taskpacks/self-repair/py-listsort-v1",
    "repo_qa": "taskpacks/qa/py-configkit-qa-v1",
    "output_prediction": "taskpacks/prediction/output-prediction",
    "test_prediction": "taskpacks/prediction/test-prediction",
}

TRACK_B_EVIDENCE: dict[str, str] = {
    "codegen": "docs/implementation/evidence/prompt-12-eval-python.json",
    "repo_repair": "docs/implementation/evidence/prompt-24-e2e36.json",
    "repo_task": "docs/implementation/evidence/prompt-25-admission-ini-interpolate.json",
    "self_repair": "docs/implementation/evidence/prompt-26-e2e-37.json",
    "repo_qa": "docs/implementation/evidence/prompt-27-e2e-38.json",
    "output_prediction": "docs/implementation/evidence/prompt-28-e2e38.json",
    "test_prediction": "docs/implementation/evidence/prompt-28-e2e38.json",
}


def audit_track_b(
    *,
    taskpacks: Mapping[str, str] | None = None,
    evidence: Mapping[str, str] | None = None,
    skipped: tuple[str, ...] = (),
) -> CoverageAudit:
    """Audit every required Track B family through its recorded pack, evidence and grader.

    Passing no mapping audits the repository as it stands: recorded manifests, fixture bytes and
    family-specific evidence contents are verified. Missing or inconsistent records fail closed;
    ``skipped`` records why a check could not run.
    """
    packs = TRACK_B_PACKS if taskpacks is None else taskpacks
    trails = TRACK_B_EVIDENCE if evidence is None else evidence
    checks = tuple(
        audit_family(family, taskpacks=packs, evidence=trails) for family in TRACK_B_FAMILIES
    )
    return CoverageAudit(checks=checks, skipped=skipped)
