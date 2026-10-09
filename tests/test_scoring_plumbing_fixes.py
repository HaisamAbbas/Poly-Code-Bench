"""Regression tests for the local grading/scoring plumbing fixes of the live campaign."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import scoring_support as s
from polycodebench_core.models import ScoreDimension
from polycodebench_lang_python import PythonLanguagePlugin
from polycodebench_orchestration.grading.scoring import (
    EvaluationScoringRejected,
    _profile_source_digest,
    _scoring_profile,
)
from polycodebench_orchestration.grading.scoring_adapter import evaluation_to_manifest
from polycodebench_scoring.loader import load_evidence_ownership, load_scoring_policy
from test_scoring_adapter import _evaluation

ROOT = Path(__file__).resolve().parents[1]
V1 = ROOT / "config" / "scoring" / "pilot-v1.yaml"
V2 = ROOT / "config" / "scoring" / "pilot-v2.yaml"
PROFILES = ROOT / "config" / "languages" / "profiles-v1.yaml"
ORIGINAL_V1_DIGEST = "sha256:55caf3f909dd063c377255539392e51cfbff2983ca961362fc77909d151842f6"


def test_pilot_v2_digest_is_what_the_scorer_computes_and_v1_is_frozen() -> None:
    v1 = load_scoring_policy(V1)
    v2 = load_scoring_policy(V2)
    computed = _profile_source_digest(PythonLanguagePlugin().language_profile.profile, PROFILES)

    assert v2.idiomatic.profile_source_digest == computed
    assert v1.idiomatic.profile_source_digest == ORIGINAL_V1_DIGEST
    assert v1.idiomatic.profile_source_digest != computed
    assert v1.policy_id == "polycodebench-code-pilot-v1"
    assert v2.policy_id == "polycodebench-code-pilot-v2"
    # v2 differs from v1 only in its identity and the profile source digest.
    first, second = v1.model_dump(mode="json"), v2.model_dump(mode="json")
    second["policy_id"] = first["policy_id"]
    second["idiomatic"]["profile_source_digest"] = first["idiomatic"]["profile_source_digest"]
    assert first == second


def test_scoring_profile_unwraps_plugin_evaluator_and_rejects_foreign_objects() -> None:
    plugin = PythonLanguagePlugin()
    profile = _scoring_profile(plugin)
    assert profile is plugin.language_profile.profile

    class Foreign:
        language_profile = object()

    with pytest.raises(EvaluationScoringRejected, match="invalid scoring profile"):
        _scoring_profile(Foreign())
    assert _scoring_profile(object()) is None


def test_applicability_is_unique_when_the_task_lists_correctness() -> None:
    policy = load_scoring_policy(s.POLICY_PATH)
    ownership = load_evidence_ownership(s.OWNERSHIP_PATH)
    task = s.frozen_task(
        (ScoreDimension.CORRECTNESS, ScoreDimension.CODE_QUALITY, ScoreDimension.IDIOMATIC),
        required_analyzers=("bandit",),
    )
    manifest = evaluation_to_manifest(
        task=task,
        evidence=_evaluation(),
        policy=policy,
        ownership=ownership,
        profile=s.python_profile(),
        profile_source_digest=policy.idiomatic.profile_source_digest,
        invocation=s.invocation(),
        evidence_artifact_id="private-evidence-bundle",
        evidence_artifact_digest=s.DIGEST_C,
    )
    assert list(manifest.applicability).count(ScoreDimension.CORRECTNESS) == 1
    assert manifest.applicability[0] is ScoreDimension.CORRECTNESS


def test_scorer_role_stays_read_only_and_scoring_is_serialised_by_the_advisory_lock() -> None:
    grants = (ROOT / "packages/persistence/sql/grant_permissions.sql").read_text(encoding="utf-8")
    scorer_grants = [line for line in grants.splitlines() if re.search(r"TO pcb_scorer;", line)]
    assert scorer_grants
    for line in scorer_grants:
        match = re.match(r"GRANT (.+?) ON (.+?) TO pcb_scorer;", line.strip())
        assert match is not None
        privileges, tables = match.groups()
        if "evaluation" in [name.strip() for name in tables.split(",")]:
            assert privileges.strip() == "SELECT"
    source = (ROOT / "packages/persistence/src/polycodebench_persistence/scoring.py").read_text(
        encoding="utf-8"
    )
    # No row lock on evaluation (it would need UPDATE); the advisory lock serialises scorers.
    assert "with_for_update" not in source
    assert "pg_advisory_lock(hashtextextended" in source
    assert "def evaluation_lock" in source
