"""Repo-task authoring and import contracts: frozen acceptance, labels, bounds (Prompt 25).

PCB-25-1 and PCB-25-4: multiple valid implementations must be able to succeed, hidden
requirements must be frozen before any candidate exists, and the methodology boundary must stay
honest (independently curated, CursorBench-inspired; never native, never a reproduction claim).
"""

from __future__ import annotations

import copy
import json

import pytest
import yaml
from polycodebench_services.repo_tasks import (
    REPRODUCTION_CLAIM_MARKERS,
    AcceptanceContractDrift,
    AcceptanceCriterion,
    AllowedChanges,
    Inspiration,
    RepoTaskAuthoring,
    seal_acceptance_contract,
    validate_repo_task_package,
    verify_acceptance_contract,
)
from polycodebench_services.task_packages import TaskPackageManifest
from repo_task_support import PACKS, pack, rubric


def authoring_document(name: str) -> dict:
    return yaml.safe_load((PACKS / name / "repo-task.yaml").read_text(encoding="utf-8"))


def manifest_document(name: str) -> dict:
    return yaml.safe_load((PACKS / name / "manifest.yaml").read_text(encoding="utf-8"))


def parse_authoring(document: dict) -> RepoTaskAuthoring:
    """Strict models accept JSON documents the way the real loader feeds them."""
    return RepoTaskAuthoring.model_validate_json(json.dumps(document))


def parse_manifest(document: dict) -> TaskPackageManifest:
    return TaskPackageManifest.model_validate_json(json.dumps(document))


@pytest.mark.parametrize("name", ["ini-interpolate", "history-group"])
def test_packs_import_and_seal_the_acceptance_contract(name: str) -> None:
    imported = pack(name)
    criteria_cases = {
        case_id
        for criterion in imported.authoring.acceptance
        if criterion.evidence_method == "executable"
        for case_id in criterion.case_ids
    }
    assert sorted(criteria_cases) == sorted(imported.binding.hidden_case_ids)
    assert imported.binding.contract_digest.startswith("sha256:")
    verify_acceptance_contract(
        imported.authoring,
        rubric=rubric(),
        hidden_case_ids=imported.binding.hidden_case_ids,
        frozen_contract_digest=imported.binding.contract_digest,
    )


@pytest.mark.parametrize("name", ["ini-interpolate", "history-group"])
def test_every_criterion_freezes_its_evidence_method_and_gate_status(name: str) -> None:
    imported = pack(name)
    for criterion in imported.authoring.acceptance:
        assert criterion.evidence_method in {"executable", "judge_rubric"}
        assert isinstance(criterion.required_gate, bool)
        if criterion.evidence_method == "executable":
            assert criterion.case_ids and not criterion.rubric_item_ids
        else:
            assert criterion.rubric_item_ids and not criterion.case_ids
            assert criterion.residual_reason and len(criterion.residual_reason) >= 16
    assert any(
        criterion.required_gate and criterion.evidence_method == "executable"
        for criterion in imported.authoring.acceptance
    )


def test_hidden_requirements_cannot_be_improvised_after_sealing() -> None:
    imported = pack("ini-interpolate")
    base = authoring_document("ini-interpolate")
    verify_acceptance_contract(
        imported.authoring,
        rubric=rubric(),
        hidden_case_ids=imported.binding.hidden_case_ids,
        frozen_contract_digest=imported.binding.contract_digest,
    )

    edited = copy.deepcopy(base)
    edited["acceptance"].append(
        {
            "criterion_id": "late-requirement",
            "summary": "A requirement invented after a candidate was already graded.",
            "evidence_method": "judge_rubric",
            "required_gate": False,
            "rubric_item_ids": ["decomposition"],
            "residual_reason": (
                "A residual reason invented after sealing is still contract drift."
            ),
        }
    )
    mutated_authoring = parse_authoring(edited)
    with pytest.raises(AcceptanceContractDrift):
        verify_acceptance_contract(
            mutated_authoring,
            rubric=rubric(),
            hidden_case_ids=imported.binding.hidden_case_ids,
            frozen_contract_digest=imported.binding.contract_digest,
        )


def test_a_re_gated_criterion_is_contract_drift_too() -> None:
    imported = pack("ini-interpolate")
    base = authoring_document("ini-interpolate")
    edited = copy.deepcopy(base)
    edited["acceptance"][0]["required_gate"] = False
    mutated_authoring = parse_authoring(edited)
    with pytest.raises(AcceptanceContractDrift):
        verify_acceptance_contract(
            mutated_authoring,
            rubric=rubric(),
            hidden_case_ids=imported.binding.hidden_case_ids,
            frozen_contract_digest=imported.binding.contract_digest,
        )


def test_judge_criteria_are_bounded_and_residual_reasons_are_required() -> None:
    with pytest.raises(ValueError, match="records why"):
        AcceptanceCriterion(
            criterion_id="vague",
            summary="Something the reviewer just knows.",
            evidence_method="judge_rubric",
            required_gate=False,
            rubric_item_ids=("duplication",),
        )
    edited = copy.deepcopy(authoring_document("ini-interpolate"))
    edited["acceptance"][-1]["rubric_item_ids"] = ["not-a-rubric-item"]
    authoring = parse_authoring(edited)
    with pytest.raises(ValueError, match="unknown rubric item"):
        seal_acceptance_contract(
            authoring, rubric=rubric(), hidden_case_ids=authoring.hidden_case_ids
        )


def test_missing_alternative_variant_blocks_admission_authoring() -> None:
    imported = pack("ini-interpolate")
    document = manifest_document("ini-interpolate")
    document["fixtures"] = [
        fixture for fixture in document["fixtures"] if fixture["variant"] != "alternative"
    ]
    edited_manifest = parse_manifest(document)
    authoring = parse_authoring(authoring_document("ini-interpolate"))
    with pytest.raises(ValueError, match="variant matrix"):
        validate_repo_task_package(
            authoring,
            edited_manifest,
            visible_files=imported.visible_files,
            snapshot_files=imported.snapshot_files,
            rubric=rubric(),
        )


def test_inspired_label_is_enforced_and_reproduction_claims_are_refused() -> None:
    with pytest.raises(ValueError, match="independent"):
        Inspiration(
            source_family="cursorbench",
            source_url="https://cursor.com/blog/cursorbench",
            statement="Copy of the benchmark's own task set in every detail.",
        )
    for marker in REPRODUCTION_CLAIM_MARKERS:
        with pytest.raises(ValueError, match="claims"):
            Inspiration(
                source_family="cursorbench",
                source_url="https://cursor.com/blog/cursorbench",
                statement=f"An independently curated task that is an {marker}.",
            )


def test_native_label_is_refused_for_independently_curated_tasks() -> None:
    imported = pack("ini-interpolate")
    document = manifest_document("ini-interpolate")
    document["task"]["methodology_label"] = "native"
    edited_manifest = parse_manifest(document)
    authoring = parse_authoring(authoring_document("ini-interpolate"))
    with pytest.raises(ValueError, match="labelled"):
        validate_repo_task_package(
            authoring,
            edited_manifest,
            visible_files=imported.visible_files,
            snapshot_files=imported.snapshot_files,
            rubric=rubric(),
        )


def test_allowed_changes_must_match_the_output_contract_exactly() -> None:
    imported = pack("ini-interpolate")
    base = authoring_document("ini-interpolate")
    base["allowed_changes"]["allowed_paths"] = ["cfgkit/loader.py"]
    base["request"]["scope_paths"] = ["cfgkit/loader.py"]
    authoring = parse_authoring(base)
    with pytest.raises(ValueError, match="allowlist"):
        validate_repo_task_package(
            authoring,
            parse_manifest(manifest_document("ini-interpolate")),
            visible_files=imported.visible_files,
            snapshot_files=imported.snapshot_files,
            rubric=rubric(),
        )
    with pytest.raises(ValueError, match="overlaps"):
        AllowedChanges(
            allowed_paths=("cfgkit/loader.py",),
            protected_paths=("cfgkit/loader.py",),
        )
