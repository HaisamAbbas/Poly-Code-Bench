"""Authoring and import contracts for independently curated realistic repository tasks.

Prompt 25, PCB-25-1 (WP-20). A repository task is a developer request spanning real files and
modules of a frozen repository snapshot, plus the repository's own conventions, the changes the
candidate is allowed to make, and a frozen acceptance contract. This module is the authoring
boundary: it extends the shared :mod:`task_packages` import machinery (strict YAML, disclosure
scan, visible/hidden separation) with the repo-task sections and freezes exactly what grading may
later demand.

Two authoring invariants carry the definition of done:

* **Multiple valid implementations can succeed.** Acceptance criteria are written against
  observable behaviour, never against the reference's structure, and a package that lacks a
  distinct alternative-valid variant is not admitted.
* **Hidden requirements are not improvised after seeing a candidate.** Every criterion declares
  its evidence method and required-gate status at authoring time; :func:`seal_acceptance_contract`
  digests the criteria, the hidden case inventory, the rubric identity and the convention rules
  into one frozen contract digest. Grading verifies that digest before it reads a candidate, so a
  criterion edited after the fact is a refusal, not a new requirement.

Methodology boundary (PCB-25-4): tasks inspired by the public methodology description of a
benchmark family are labelled ``inspired`` and recorded with an independent-curation statement.
Claims of private task access or exact reproduction are rejected mechanically here and documented
in ``docs/implementation/repo-task-method.md``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import yaml  # type: ignore[import-untyped]
from polycodebench_core.canonical import canonical_digest
from polycodebench_core.judge_contracts import JudgeRubric
from pydantic import ConfigDict, Field, model_validator

from polycodebench_services.task_packages import (
    StrictModel,
    TaskPackageManifest,
    _UniqueKeyLoader,
)

#: Claims an inspiration statement or request may never make. Independent curation is the only
#: honest provenance for these tasks: no private task set, grader or score is available here.
REPRODUCTION_CLAIM_MARKERS = (
    "exact reproduction",
    "exact replica",
    "reproduces the benchmark",
    "official benchmark task",
    "official tasks from",
    "private task access",
    "private tasks from",
    "verbatim from cursorbench",
    "verbatim from the benchmark",
)

INSPIRATION_FAMILIES = ("cursorbench", "swebench", "livecodebench", "deepcodebench", "none")


class AcceptanceContractDrift(ValueError):
    """The acceptance contract no longer matches the digest frozen before any candidate."""


class RepoTaskRequest(StrictModel):
    """The developer request as written, and the real repository surface it spans."""

    text: str = Field(min_length=24, max_length=8_000)
    scope_paths: tuple[str, ...] = Field(min_length=1, max_length=64)
    conventions: tuple[str, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def scope_is_unique(self) -> RepoTaskRequest:
        if len(set(self.scope_paths)) != len(self.scope_paths):
            raise ValueError("request scope paths must be unique")
        if len(set(self.conventions)) != len(self.conventions):
            raise ValueError("convention paths must be unique")
        return self


class AllowedChanges(StrictModel):
    """What the candidate may write, and what stays out of bounds."""

    allowed_paths: tuple[str, ...] = Field(min_length=1, max_length=64)
    protected_paths: tuple[str, ...] = Field(max_length=64)

    @model_validator(mode="after")
    def allowed_and_protected_disjoint(self) -> AllowedChanges:
        if len(set(self.allowed_paths)) != len(self.allowed_paths):
            raise ValueError("allowed paths must be unique")
        if len(set(self.protected_paths)) != len(self.protected_paths):
            raise ValueError("protected paths must be unique")
        for path in self.protected_paths:
            if any(
                path == allowed or path.startswith(allowed.rstrip("/") + "/")
                for allowed in self.allowed_paths
            ):
                raise ValueError(f"protected path {path!r} overlaps the allowed change set")
        return self


class AcceptanceCriterion(StrictModel):
    """One frozen acceptance requirement with exactly one evidence method.

    ``executable`` criteria are the mandatory acceptance contract: hidden cases run against the
    candidate workspace. ``judge_rubric`` criteria cover only genuinely non-executable
    requirements, are bounded by the frozen rubric, and carry a residual reason explaining why no
    executable or static check owns them. A judgment can never satisfy or override an executable
    criterion; the grader computes the mandatory gate before it reads any judge result.
    """

    criterion_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    summary: str = Field(min_length=8, max_length=400)
    evidence_method: Literal["executable", "judge_rubric"]
    required_gate: bool
    case_ids: tuple[str, ...] = ()
    rubric_item_ids: tuple[str, ...] = ()
    residual_reason: str | None = Field(default=None, min_length=16, max_length=512)
    minimum_gate_score: str | None = Field(default=None, pattern=r"^[01]\.\d{6}$")

    @model_validator(mode="after")
    def evidence_method_is_frozen(self) -> AcceptanceCriterion:
        if self.evidence_method == "executable":
            if not self.case_ids:
                raise ValueError("an executable criterion names its hidden cases")
            if self.rubric_item_ids or self.residual_reason or self.minimum_gate_score:
                raise ValueError("an executable criterion carries no rubric item or gate score")
        else:
            if not self.rubric_item_ids:
                raise ValueError("a judge criterion names its rubric items")
            if self.case_ids:
                raise ValueError("a judge criterion carries no hidden cases")
            if self.residual_reason is None:
                raise ValueError("a judge criterion records why no executable check owns it")
            if len(set(self.rubric_item_ids)) != len(self.rubric_item_ids):
                raise ValueError("a judge criterion names each rubric item once")
            if self.minimum_gate_score is not None and not self.required_gate:
                raise ValueError("only a required-gate judge criterion declares a minimum score")
            if self.required_gate and self.minimum_gate_score is None:
                raise ValueError("a required-gate judge criterion freezes its minimum score")
        return self


class Inspiration(StrictModel):
    """Methodology provenance: what inspired the task shape, and what is independently curated."""

    source_family: Literal["cursorbench", "swebench", "livecodebench", "deepcodebench", "none"]
    source_url: str | None = Field(default=None, max_length=2_048)
    statement: str = Field(min_length=24, max_length=2_048)

    @model_validator(mode="after")
    def statement_is_honest(self) -> Inspiration:
        lowered = self.statement.lower()
        for marker in REPRODUCTION_CLAIM_MARKERS:
            if marker in lowered:
                raise ValueError(
                    f"inspiration statement claims {marker!r}: these tasks are independently "
                    + "curated and make no claim of private access or exact reproduction"
                )
        if self.source_family != "none" and "independent" not in lowered:
            raise ValueError(
                "an inspired task records that its tasks were curated independently of the "
                + "source family"
            )
        return self


class RepoTaskAuthoring(StrictModel):
    """The repo-task authoring document: request, conventions, allowed changes, acceptance."""

    schema_version: Literal[1]
    kind: Literal["repo_task_authoring"]
    task_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$")
    inspiration: Inspiration
    request: RepoTaskRequest
    allowed_changes: AllowedChanges
    acceptance: tuple[AcceptanceCriterion, ...] = Field(min_length=1, max_length=32)
    convention_rules: tuple[str, ...] = Field(min_length=1, max_length=32)
    baseline_penalty_relations: tuple[str, ...] = Field(
        default_factory=lambda: ("introduced", "worsened")
    )

    @model_validator(mode="after")
    def acceptance_is_coherent(self) -> RepoTaskAuthoring:
        identifiers = [criterion.criterion_id for criterion in self.acceptance]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("criterion ids must be unique")
        if not any(
            criterion.required_gate and criterion.evidence_method == "executable"
            for criterion in self.acceptance
        ):
            raise ValueError("a repository task needs at least one mandatory executable criterion")
        cases = [
            case_id for criterion in self.acceptance for case_id in criterion.case_ids
        ]
        if len(set(cases)) != len(cases):
            raise ValueError("each hidden case is owned by exactly one criterion")
        if len(set(self.convention_rules)) != len(self.convention_rules):
            raise ValueError("convention rule ids must be unique")
        known_relations = {
            "introduced",
            "worsened",
            "unchanged_in_scope",
            "unchanged_out_of_scope",
            "resolved",
            "unknown",
        }
        if not self.baseline_penalty_relations:
            raise ValueError("at least one baseline relation must be penalized")
        if not set(self.baseline_penalty_relations) <= known_relations:
            raise ValueError("unknown baseline relation in baseline_penalty_relations")
        return self

    @property
    def hidden_case_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(case_id for criterion in self.acceptance for case_id in criterion.case_ids)
        )

    @property
    def judge_rubric_item_ids(self) -> tuple[str, ...]:
        ordered: list[str] = []
        for criterion in self.acceptance:
            for item_id in criterion.rubric_item_ids:
                if item_id not in ordered:
                    ordered.append(item_id)
        return tuple(ordered)


class RepoTaskBinding(StrictModel):
    """What the frozen contract binds: inventory, judge items and the sealed digest."""

    kind: Literal["repo_task_binding"] = "repo_task_binding"
    contract_digest: str
    rubric_digest: str
    hidden_case_inventory_digest: str
    hidden_case_ids: tuple[str, ...]
    judge_rubric_item_ids: tuple[str, ...]
    mandatory_criterion_ids: tuple[str, ...]
    quality_only_criterion_ids: tuple[str, ...]


def load_repo_task_authoring(path: Path) -> RepoTaskAuthoring:
    """Strict YAML load: unique keys, no anchors, no unknown fields."""
    raw = path.read_bytes()
    if len(raw) > 1_048_576:
        raise ValueError("repo-task authoring document exceeds 1 MiB")
    try:
        text = raw.decode("utf-8", errors="strict")
        if any(
            isinstance(token, (yaml.tokens.AnchorToken, yaml.tokens.AliasToken))
            for token in yaml.scan(text)
        ):
            raise ValueError("YAML anchors and aliases are not allowed")
        decoded = yaml.load(text, Loader=_UniqueKeyLoader)
    except (UnicodeDecodeError, yaml.YAMLError) as error:
        raise ValueError("repo-task authoring document is invalid") from error
    return RepoTaskAuthoring.model_validate_json(
        json.dumps(decoded, ensure_ascii=False, allow_nan=False)
    )


def case_inventory_digest(case_ids: tuple[str, ...]) -> str:
    return canonical_digest(
        {"kind": "hidden_case_inventory", "case_ids": sorted(case_ids)}
    )


def acceptance_contract_digest(
    authoring: RepoTaskAuthoring,
    *,
    rubric: JudgeRubric,
    hidden_case_ids: tuple[str, ...],
) -> str:
    """The frozen acceptance contract: criteria, rubric identity, case inventory, conventions."""
    return canonical_digest(
        {
            "kind": "repo_task_acceptance_contract",
            "task_id": authoring.task_id,
            "criteria": [
                criterion.model_dump(mode="json") for criterion in authoring.acceptance
            ],
            "rubric_id": rubric.rubric_id,
            "rubric_version": rubric.version,
            "rubric_digest": rubric.digest(),
            "hidden_case_inventory_digest": case_inventory_digest(hidden_case_ids),
            "convention_rules": sorted(authoring.convention_rules),
            "baseline_penalty_relations": sorted(authoring.baseline_penalty_relations),
        }
    )


def seal_acceptance_contract(
    authoring: RepoTaskAuthoring,
    *,
    rubric: JudgeRubric,
    hidden_case_ids: tuple[str, ...],
) -> RepoTaskBinding:
    """Validate the contract against the frozen rubric and seal it before any candidate exists."""
    known_items = {item.item_id: item for item in rubric.items}
    judge_ids = authoring.judge_rubric_item_ids
    for item_id in judge_ids:
        if item_id not in known_items:
            raise ValueError(f"judge criterion references unknown rubric item {item_id!r}")
    if len(judge_ids) > len(known_items):
        raise ValueError("the task's rubric items exceed the frozen rubric")
    if sorted(hidden_case_ids) != sorted(authoring.hidden_case_ids):
        raise ValueError("the sealed hidden case inventory must equal the criteria's case ids")
    for criterion in authoring.acceptance:
        if criterion.required_gate and criterion.minimum_gate_score is not None:
            for item_id in criterion.rubric_item_ids:
                anchors = {anchor.value for anchor in known_items[item_id].anchors}
                if criterion.minimum_gate_score not in anchors:
                    raise ValueError(
                        f"{criterion.criterion_id}: gate minimum must be a declared anchor"
                    )
    return RepoTaskBinding(
        contract_digest=acceptance_contract_digest(
            authoring, rubric=rubric, hidden_case_ids=hidden_case_ids
        ),
        rubric_digest=rubric.digest(),
        hidden_case_inventory_digest=case_inventory_digest(hidden_case_ids),
        hidden_case_ids=tuple(sorted(hidden_case_ids)),
        judge_rubric_item_ids=judge_ids,
        mandatory_criterion_ids=tuple(
            criterion.criterion_id for criterion in authoring.acceptance if criterion.required_gate
        ),
        quality_only_criterion_ids=tuple(
            criterion.criterion_id
            for criterion in authoring.acceptance
            if not criterion.required_gate
        ),
    )


def verify_acceptance_contract(
    authoring: RepoTaskAuthoring,
    *,
    rubric: JudgeRubric,
    hidden_case_ids: tuple[str, ...],
    frozen_contract_digest: str,
) -> None:
    """Grading entry check: the contract is still the one sealed before any candidate was seen."""
    current = acceptance_contract_digest(
        authoring, rubric=rubric, hidden_case_ids=hidden_case_ids
    )
    if current != frozen_contract_digest:
        raise AcceptanceContractDrift(
            "the acceptance contract changed after sealing; hidden requirements cannot be "
            + "added, removed or re-gated after a candidate exists"
        )


def validate_repo_task_package(
    authoring: RepoTaskAuthoring,
    package: TaskPackageManifest,
    *,
    visible_files: dict[str, bytes],
    snapshot_files: dict[str, bytes],
    rubric: JudgeRubric,
) -> RepoTaskBinding:
    """Cross-check the authoring document against the imported package and seal the contract."""
    if authoring.task_id != package.task.task_id:
        raise ValueError("authoring task id does not match the package task")
    if package.task.family != "repo_task":
        raise ValueError("repo-task authoring requires a repo_task family package")
    inspiration = authoring.inspiration
    if inspiration.source_family == "cursorbench" and package.task.methodology_label != "inspired":
        raise ValueError(
            "CursorBench-shaped tasks are curated independently and must be labelled 'inspired'"
        )
    if package.task.methodology_label == "native":
        raise ValueError(
            "independently curated repository tasks are never labelled 'native'; "
            + "'native' is reserved for upstream benchmark records"
        )
    for text in (inspiration.statement, authoring.request.text):
        lowered = text.lower()
        for marker in REPRODUCTION_CLAIM_MARKERS:
            if marker in lowered:
                raise ValueError(f"task text claims {marker!r}, which provenance does not support")

    for path in authoring.request.scope_paths:
        if path not in snapshot_files and path not in authoring.allowed_changes.allowed_paths:
            raise ValueError(
                f"request scope path {path!r} is neither in the frozen snapshot nor a path the "
                "candidate is allowed to create"
            )
    for path in authoring.request.conventions:
        if path not in snapshot_files:
            raise ValueError(f"convention document {path!r} must be in the visible repository")
    if tuple(authoring.allowed_changes.allowed_paths) != tuple(
        package.output_contract.allowed_paths
    ):
        raise ValueError(
            "the authoring allowed-change set must equal the output contract allowlist exactly"
        )
    required_outputs = set(package.acceptance.required_outputs)
    if not required_outputs <= set(authoring.allowed_changes.allowed_paths):
        raise ValueError("required outputs must be inside the allowed change set")

    fixture_variants = {fixture.variant for fixture in package.fixtures}
    missing = {"reference", "alternative", "faulty", "quality_defective"} - fixture_variants
    if missing:
        raise ValueError(
            "a repository task ships the full variant matrix; missing: "
            + ", ".join(sorted(missing))
        )
    expected_cases = set(authoring.hidden_case_ids)
    return seal_acceptance_contract(
        authoring, rubric=rubric, hidden_case_ids=tuple(sorted(expected_cases))
    )


def contract_snapshot(authoring: RepoTaskAuthoring) -> dict[str, object]:
    """The authoring facts worth recording in evidence, with no hidden test bodies."""
    return {
        "task_id": authoring.task_id,
        "inspiration": authoring.inspiration.model_dump(mode="json"),
        "request_scope_paths": list(authoring.request.scope_paths),
        "conventions": list(authoring.request.conventions),
        "allowed_paths": list(authoring.allowed_changes.allowed_paths),
        "protected_paths": list(authoring.allowed_changes.protected_paths),
        "criteria": [criterion.model_dump(mode="json") for criterion in authoring.acceptance],
        "convention_rules": list(authoring.convention_rules),
        "baseline_penalty_relations": list(authoring.baseline_penalty_relations),
    }


def content_sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class ImportedRepoTask(StrictModel):
    """One repo-task pack after import: package, authoring document and sealed binding."""

    model_config = ConfigDict(
        arbitrary_types_allowed=True, extra="forbid", strict=True, frozen=True
    )

    imported: Any
    authoring: RepoTaskAuthoring
    binding: RepoTaskBinding
    visible_files: dict[str, bytes]
    hidden_files: dict[str, bytes]
    snapshot_files: dict[str, bytes]


def import_repo_task_package(root: Path, *, rubric: JudgeRubric) -> ImportedRepoTask:
    """Import one pack through the shared task-package machinery and seal its contract."""
    from polycodebench_services.task_packages import TaskPackageImporter

    importer = TaskPackageImporter()
    imported = importer.import_package(root)
    authoring = load_repo_task_authoring(root / "repo-task.yaml")
    manifest = imported.manifest
    visible_files = {
        path: (root / Path(*PurePosixPath(path).parts)).read_bytes()
        for path in manifest.visible_files
    }
    hidden_files = {
        path: (root / Path(*PurePosixPath(path).parts)).read_bytes()
        for path in manifest.hidden_files
    }
    snapshot_files = {
        path.removeprefix("visible/repo/"): data
        for path, data in visible_files.items()
        if path.startswith("visible/repo/")
    }
    binding = validate_repo_task_package(
        authoring,
        manifest,
        visible_files=visible_files,
        snapshot_files=snapshot_files,
        rubric=rubric,
    )
    return ImportedRepoTask(
        imported=imported,
        authoring=authoring,
        binding=binding,
        visible_files=visible_files,
        hidden_files=hidden_files,
        snapshot_files=snapshot_files,
    )
