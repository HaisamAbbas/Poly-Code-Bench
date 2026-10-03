"""Shared helpers for the repository-task suite tests (Prompt 25).

Packs are imported through the real import pipeline and graded through the real grading entry
points; the only synthetic part is judge votes, which are deterministic fixture votes parsed
through the real judge services (the panel is unprovisioned). ``scripts.repo_task_tool`` owns the
fixture-vote construction, and tests import that real command rather than a copy.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

from polycodebench_core.judge_contracts import JudgementResult, JudgeRubric
from polycodebench_evaluation.evaluator import baseline_from_package
from polycodebench_evaluation.repo_task_admission import (
    RepoTaskAdmissionReport,
    admit_repo_task,
    variant_submission,
)
from polycodebench_evaluation.repo_task_grading import RepoTaskGrade, grade_repo_task
from polycodebench_scoring.loader import load_evidence_ownership, load_scoring_policy
from polycodebench_scoring.manifest import ScoringInvocation
from polycodebench_scoring.scorer import ScoringOutcome
from polycodebench_services.judging import load_rubric
from polycodebench_services.repo_tasks import ImportedRepoTask, import_repo_task_package

from scripts.repo_task_tool import diff_against, fixture_judgement

ROOT = Path(__file__).resolve().parents[1]
PACKS = ROOT / "taskpacks" / "repo-tasks"
CONFIG = ROOT / "config"

ALL_SCORES = "1.000000"
WEAK_SCORES = {"duplication": "0.000000", "repository_style_consistency": "0.000000"}
PERFECT_SCORES = "1.000000"


@functools.cache
def rubric() -> JudgeRubric:
    return load_rubric(CONFIG / "judging" / "rubric-v1.yaml")


@functools.cache
def pack(name: str) -> ImportedRepoTask:
    return import_repo_task_package(PACKS / name, rubric=rubric())


@functools.cache
def admission(name: str) -> RepoTaskAdmissionReport:
    return admit_repo_task(pack=pack(name), rubric=rubric())


def scores_for(variant: str) -> dict[str, str]:
    if variant == "quality_defective":
        return {
            item.item_id: WEAK_SCORES.get(item.item_id, ALL_SCORES)
            for item in rubric().items
            if item.dimension.value == "code_quality"
        }
    return {
        item.item_id: ALL_SCORES
        for item in rubric().items
        if item.dimension.value == "code_quality"
    }


@functools.cache
def graded(name: str, fixture_name: str) -> RepoTaskGrade:
    """Grade one authored variant with its deterministic fixture judgement attached."""
    imported = pack(name)
    package_files = {**imported.visible_files, **imported.hidden_files}
    submission_kind, payload = variant_submission(package_files, fixture_name)
    baseline = baseline_from_package(package_files)
    fixture = next(f for f in imported.imported.manifest.fixtures if f.name == fixture_name)
    first = grade_repo_task(
        authoring=imported.authoring,
        binding=imported.binding,
        rubric=rubric(),
        package=imported.imported.manifest,
        package_digest=imported.imported.package_digest,
        baseline_files=baseline,
        hidden_files=imported.hidden_files,
        submission_kind=submission_kind,
        payload=payload,
    )
    judgement = (
        fixture_judgement(first, rubric=rubric(), scores=scores_for(fixture.variant))
        if first.judge_packet_input is not None
        else None
    )
    return grade_repo_task(
        authoring=imported.authoring,
        binding=imported.binding,
        rubric=rubric(),
        package=imported.imported.manifest,
        package_digest=imported.imported.package_digest,
        baseline_files=baseline,
        hidden_files=imported.hidden_files,
        submission_kind=submission_kind,
        payload=payload,
        judgement=judgement,
    )


def unjudged(name: str, fixture_name: str) -> RepoTaskGrade:
    """Grade one authored variant with no judgment attached at all."""
    imported = pack(name)
    package_files = {**imported.visible_files, **imported.hidden_files}
    submission_kind, payload = variant_submission(package_files, fixture_name)
    baseline = baseline_from_package(package_files)
    return grade_repo_task(
        authoring=imported.authoring,
        binding=imported.binding,
        rubric=rubric(),
        package=imported.imported.manifest,
        package_digest=imported.imported.package_digest,
        baseline_files=baseline,
        hidden_files=imported.hidden_files,
        submission_kind=submission_kind,
        payload=payload,
    )


def judged(
    name: str, fixture_name: str, scores: dict[str, str]
) -> tuple[RepoTaskGrade, JudgementResult]:
    """Grade one variant with an explicit fixture score plan."""
    imported = pack(name)
    package_files = {**imported.visible_files, **imported.hidden_files}
    submission_kind, payload = variant_submission(package_files, fixture_name)
    baseline = baseline_from_package(package_files)
    first = grade_repo_task(
        authoring=imported.authoring,
        binding=imported.binding,
        rubric=rubric(),
        package=imported.imported.manifest,
        package_digest=imported.imported.package_digest,
        baseline_files=baseline,
        hidden_files=imported.hidden_files,
        submission_kind=submission_kind,
        payload=payload,
    )
    judgement = fixture_judgement(first, rubric=rubric(), scores=scores)
    grade = grade_repo_task(
        authoring=imported.authoring,
        binding=imported.binding,
        rubric=rubric(),
        package=imported.imported.manifest,
        package_digest=imported.imported.package_digest,
        baseline_files=baseline,
        hidden_files=imported.hidden_files,
        submission_kind=submission_kind,
        payload=payload,
        judgement=judgement,
    )
    return grade, judgement


def score(grade: RepoTaskGrade, name: str) -> ScoringOutcome:
    from polycodebench_evaluation.repo_task_grading import frozen_task_for

    imported = pack(name)
    invocation = ScoringInvocation(
        run_id="3f1b0c2e-5d4a-4b6c-8e9f-0a1b2c3d4e5f",
        candidate_id="7a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
        recorded_at="2026-10-03T12:00:00Z",
        scorer_digest="sha256:" + "d" * 64,
    )
    task = frozen_task_for(
        imported.imported.manifest,
        task_digest=imported.imported.package_digest,
        inventory_digest=imported.binding.hidden_case_inventory_digest,
    )
    return grade.to_scorecard(
        task=task,
        policy=load_scoring_policy(CONFIG / "scoring" / "pilot-v1.yaml"),
        ownership=load_evidence_ownership(CONFIG / "scoring" / "evidence_ownership.yaml"),
        invocation=invocation,
    )


def reference_patch(name: str) -> str:
    """The reference variant re-submitted as a unified diff over the frozen baseline."""
    imported = pack(name)
    package_files = {**imported.visible_files, **imported.hidden_files}
    fixture = next(f for f in imported.imported.manifest.fixtures if f.variant == "reference")
    _, overlay = variant_submission(package_files, fixture.name)
    assert isinstance(overlay, dict)
    return diff_against(baseline_from_package(package_files), overlay)


def grade_patch(name: str, patch_text: str) -> RepoTaskGrade:
    imported = pack(name)
    baseline = baseline_from_package({**imported.visible_files, **imported.hidden_files})
    return grade_repo_task(
        authoring=imported.authoring,
        binding=imported.binding,
        rubric=rubric(),
        package=imported.imported.manifest,
        package_digest=imported.imported.package_digest,
        baseline_files=baseline,
        hidden_files=imported.hidden_files,
        submission_kind="unified_diff",
        payload=patch_text,
    )


def any_qualities(grade: RepoTaskGrade) -> list[dict[str, Any]]:
    return list(grade.report()["quality_issues"])
