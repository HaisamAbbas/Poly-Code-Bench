"""Executable admission for independently curated repository tasks (Prompt 25, PCB-25-4).

Every admission claim is made by running the authored variant workspaces through the task's own
hidden acceptance inventory and the deterministic convention scan - the same machinery grading
uses, so admission and grading can never disagree about what a variant demonstrates:

* the **reference** passes the full hidden inventory five times with identical outcomes;
* the **alternative-valid** variant passes too, and its workspace differs from the reference -
  together these prove multiple valid implementations can succeed;
* the **functionally failing** variant fails at least the hidden cases it was authored to break;
* the **quality-defective** variant passes every hidden case (the weakness survives functional
  tests) while the convention scan reports its intended defect families in its changed files.

The methodology label is checked here as well: a CursorBench-shaped pack is ``inspired`` with an
independent-curation statement, never ``native``, and the sealed acceptance contract must verify
before a single variant runs.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from polycodebench_core.canonical import canonical_digest
from polycodebench_core.judge_contracts import JudgeRubric
from polycodebench_services.repo_tasks import (
    ImportedRepoTask,
    verify_acceptance_contract,
)

from polycodebench_evaluation.evaluator import baseline_from_package, baseline_relations
from polycodebench_evaluation.repo_task_conventions import (
    ConventionAnalyzer,
    findings_as_observations,
)
from polycodebench_evaluation.repo_task_grading import (
    AcceptanceRun,
    CaseOutcome,
    assemble_workspace,
    run_hidden_acceptance,
)

REFERENCE_REPETITIONS = 5
EXECUTION_TIER: Literal["local_fixture"] = "local_fixture"


@dataclass(frozen=True)
class VariantAdmission:
    """What one authored variant demonstrated during admission."""

    name: str
    variant: str
    submission_kind: str
    repetitions: int
    identical_outcomes: bool
    passed_cases: tuple[str, ...]
    failed_cases: tuple[str, ...]
    expected_failing_cases: tuple[str, ...]
    functional_pass: bool
    observed_issue_families: tuple[str, ...]
    expected_issue_families: tuple[str, ...]
    workspace_digest: str
    notes: tuple[str, ...] = ()


@dataclass
class RepoTaskAdmissionReport:
    """The admission verdict for one repo-task pack."""

    task_id: str
    package_digest: str
    contract_digest: str
    rubric_digest: str
    methodology_label: str
    inspiration: dict[str, Any]
    execution_tier: str
    reference_stable: bool
    alternative_passes: bool
    alternative_differs: bool
    faulty_rejected: bool
    quality_defective_functional_pass: bool
    quality_defective_detected: bool
    label_check: bool
    contract_frozen: bool
    variants: tuple[VariantAdmission, ...] = ()
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def admitted(self) -> bool:
        return all(self.checks.values())

    def report(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "package_digest": self.package_digest,
            "contract_digest": self.contract_digest,
            "rubric_digest": self.rubric_digest,
            "methodology_label": self.methodology_label,
            "inspiration": self.inspiration,
            "execution_tier": self.execution_tier,
            "admitted": self.admitted,
            "checks": dict(self.checks),
            "reference_stable": self.reference_stable,
            "alternative_passes": self.alternative_passes,
            "alternative_differs": self.alternative_differs,
            "faulty_rejected": self.faulty_rejected,
            "quality_defective_functional_pass": self.quality_defective_functional_pass,
            "quality_defective_detected": self.quality_defective_detected,
            "label_check": self.label_check,
            "contract_frozen": self.contract_frozen,
            "variants": [
                {
                    "name": entry.name,
                    "variant": entry.variant,
                    "submission_kind": entry.submission_kind,
                    "repetitions": entry.repetitions,
                    "identical_outcomes": entry.identical_outcomes,
                    "passed_cases": list(entry.passed_cases),
                    "failed_cases": list(entry.failed_cases),
                    "expected_failing_cases": list(entry.expected_failing_cases),
                    "functional_pass": entry.functional_pass,
                    "observed_issue_families": list(entry.observed_issue_families),
                    "expected_issue_families": list(entry.expected_issue_families),
                    "workspace_digest": entry.workspace_digest,
                    "notes": list(entry.notes),
                }
                for entry in self.variants
            ],
        }


def variant_submission(
    package_files: Mapping[str, bytes], fixture_name: str
) -> tuple[Literal["source_bundle", "unified_diff"], dict[str, bytes] | str]:
    """One variant's submission: its overlay tree, or a single patch when it ships one."""
    prefix = f"admission/{fixture_name}/"
    overlay = {
        path[len(prefix) :]: data for path, data in package_files.items() if path.startswith(prefix)
    }
    patches = {path: data for path, data in overlay.items() if path.endswith(".patch")}
    if patches:
        if len(patches) != 1:
            raise ValueError(f"{fixture_name}: a variant ships at most one patch")
        (data,) = patches.values()
        return "unified_diff", data.decode("utf-8")
    if not overlay:
        raise ValueError(f"{fixture_name}: variant directory admission/{fixture_name}/ is empty")
    return "source_bundle", overlay


def _outcome_digest(cases: tuple[CaseOutcome, ...]) -> str:
    return canonical_digest([{"case_id": case.case_id, "outcome": case.outcome} for case in cases])


def admit_repo_task(
    *,
    pack: ImportedRepoTask,
    rubric: JudgeRubric,
    repetitions: int = REFERENCE_REPETITIONS,
) -> RepoTaskAdmissionReport:
    """Run the full admission matrix for one imported repo-task pack."""
    authoring = pack.authoring
    binding = pack.binding
    package = pack.imported.manifest
    package_files = {
        **{path: data for path, data in pack.visible_files.items()},
        **{path: data for path, data in pack.hidden_files.items()},
    }
    verify_acceptance_contract(
        authoring,
        rubric=rubric,
        hidden_case_ids=binding.hidden_case_ids,
        frozen_contract_digest=binding.contract_digest,
    )
    baseline = baseline_from_package(package_files)
    runner_bytes = pack.hidden_files["hidden/acceptance_runner.py"]
    analyzer = ConventionAnalyzer(authoring.convention_rules)
    baseline_findings = analyzer.analyze(baseline)

    variants: list[VariantAdmission] = []
    runs_by_name: dict[str, AcceptanceRun] = {}
    workspaces: dict[str, dict[str, bytes]] = {}
    for fixture in package.fixtures:
        submission_kind, payload = variant_submission(package_files, fixture.name)
        workspace = assemble_workspace(
            baseline,
            submission_kind=submission_kind,
            payload=payload,
            allowed_paths=authoring.allowed_changes.allowed_paths,
            protected_paths=authoring.allowed_changes.protected_paths,
        )
        workspaces[fixture.name] = workspace.files
        repetitions_for = repetitions if fixture.variant == "reference" else 1
        outcomes = [
            run_hidden_acceptance(
                runner_bytes=runner_bytes,
                workspace=workspace.files,
                expected_case_ids=binding.hidden_case_ids,
            )
            for _ in range(repetitions_for)
        ]
        runs_by_name[fixture.name] = outcomes[0]
        digests = {_outcome_digest(run.cases) for run in outcomes}
        expectation = fixture.expectation
        expected_failing = tuple(expectation.failing_cases) if expectation else ()
        observed_failed = tuple(
            sorted(case.case_id for case in outcomes[0].cases if case.outcome != "pass")
        )
        observed_passed = tuple(
            sorted(case.case_id for case in outcomes[0].cases if case.outcome == "pass")
        )
        findings = analyzer.analyze(workspace.files)
        observations = findings_as_observations(
            findings,
            tool_digest="sha256:" + "0" * 64,
            candidate_digest="sha256:" + "1" * 64,
        )
        baseline_observations = findings_as_observations(
            baseline_findings,
            tool_digest="sha256:" + "0" * 64,
            candidate_digest="sha256:" + "2" * 64,
        )
        relations, _ = baseline_relations(
            observations, baseline_observations, workspace.files, baseline
        )
        families = {
            finding.family
            for finding in findings
            if relations.get(finding.issue_key) == "introduced"
        }
        expected_families = tuple(expectation.expected_issue_families) if expectation else ()
        variants.append(
            VariantAdmission(
                name=fixture.name,
                variant=fixture.variant,
                submission_kind=submission_kind,
                repetitions=repetitions_for,
                identical_outcomes=len(digests) == 1,
                passed_cases=observed_passed,
                failed_cases=observed_failed,
                expected_failing_cases=expected_failing,
                functional_pass=all(case.outcome == "pass" for case in outcomes[0].cases)
                and outcomes[0].harness_ok,
                observed_issue_families=tuple(sorted(families)),
                expected_issue_families=expected_families,
                workspace_digest=canonical_digest(_digest_rows(workspace.files)),
                notes=() if outcomes[0].harness_ok else (outcomes[0].harness_detail,),
            )
        )

    by_variant = {entry.variant: entry for entry in variants}
    reference = by_variant.get("reference")
    alternative = by_variant.get("alternative")
    faulty = by_variant.get("faulty")
    quality = by_variant.get("quality_defective")

    reference_stable = bool(
        reference and reference.identical_outcomes and reference.functional_pass
    )
    alternative_passes = bool(alternative and alternative.functional_pass)
    alternative_differs = bool(
        reference
        and alternative
        and workspaces.get(reference.name) != workspaces.get(alternative.name)
    )
    faulty_rejected = bool(
        faulty
        and not faulty.functional_pass
        and set(faulty.expected_failing_cases) <= set(faulty.failed_cases)
    )
    quality_functional = bool(quality and quality.functional_pass)
    quality_detected = bool(
        quality and set(quality.expected_issue_families) <= set(quality.observed_issue_families)
    )
    label_check = (
        package.task.methodology_label == "inspired"
        and authoring.inspiration.source_family == "cursorbench"
    )
    checks = {
        "strict_schema": True,
        "safe_paths_and_snapshot": True,
        "visible_hidden_separation": True,
        "rights_and_provenance": True,
        "disclosure_scan": True,
        "acceptance_contract_frozen": True,
        "reference_acceptance": reference_stable,
        "five_reference_repetitions": bool(reference and reference.repetitions == repetitions),
        "known_fault_rejection": faulty_rejected,
        "alternative_solution_acceptance": alternative_passes and alternative_differs,
        "quality_weakness_detected": quality_functional and quality_detected,
        "methodology_label": label_check,
    }
    return RepoTaskAdmissionReport(
        task_id=authoring.task_id,
        package_digest=pack.imported.package_digest,
        contract_digest=binding.contract_digest,
        rubric_digest=binding.rubric_digest,
        methodology_label=package.task.methodology_label,
        inspiration=authoring.inspiration.model_dump(mode="json"),
        execution_tier=EXECUTION_TIER,
        reference_stable=reference_stable,
        alternative_passes=alternative_passes,
        alternative_differs=alternative_differs,
        faulty_rejected=faulty_rejected,
        quality_defective_functional_pass=quality_functional,
        quality_defective_detected=quality_detected,
        label_check=label_check,
        contract_frozen=True,
        variants=tuple(variants),
        checks=checks,
    )


def _digest_rows(files: Mapping[str, bytes]) -> list[list[str]]:
    from polycodebench_core.canonical import sha256_bytes

    return [[path, sha256_bytes(data)] for path, data in sorted(files.items())]
