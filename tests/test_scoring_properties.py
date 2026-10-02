"""The scoring properties required by Technical Spec 24.1, plus the E2E-23 ownership cases.

These are the invariants that must hold for *every* input, not just the documented examples:

* the composite is bounded in [0, 100];
* at fixed applicability, raising a positive item never lowers the composite;
* an identical duplicate issue cannot change a score;
* reordering equivalent evidence cannot change a score;
* unknown evidence cannot increase a score;
* N/A is preserved as N/A and never silently renormalised into a perfect value.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
import scoring_support as s
from polycodebench_core.canonical import canonical_document_digest
from polycodebench_core.models import EvaluationState, ScoreDimension
from polycodebench_scoring import (
    ScoringRefusalCode,
    ScoringRefused,
    resolve_owners,
    score_evaluation,
)
from polycodebench_scoring.loader import load_evidence_ownership, load_scoring_policy
from polycodebench_scoring.scorer import score_identity


@pytest.fixture(scope="module")
def policy():  # type: ignore[no-untyped-def]
    return load_scoring_policy(s.POLICY_PATH)


@pytest.fixture(scope="module")
def ownership():  # type: ignore[no-untyped-def]
    return load_evidence_ownership(s.OWNERSHIP_PATH)


@pytest.fixture(scope="module")
def profile():  # type: ignore[no-untyped-def]
    return s.python_profile()


def _score(policy, ownership, profile, manifest, applicable=s.ALL_QUALITY):  # type: ignore[no-untyped-def]
    return score_evaluation(
        s.frozen_task(applicable),
        policy,
        manifest,
        ownership=ownership,
        profile=profile,
    )


def _dimension(outcome, dimension: ScoreDimension):  # type: ignore[no-untyped-def]
    return next(row for row in outcome.explanation.dimensions if row.dimension is dimension)


# ------------------------------------------------------------------------------------ boundedness


@pytest.mark.parametrize("level", [0, 2500, 5000, 7500, 10000])
def test_score_is_bounded_for_every_quality_level(policy, ownership, profile, level) -> None:  # type: ignore[no-untyped-def]
    manifest = s.manifest(
        efficiency_value=s.efficiency(f"{Decimal(level) / 100:.6f}"),
        code_quality_bp=level,
        idiomatic_bp=level,
        robustness_bp=level,
    )
    outcome = _score(policy, ownership, profile, manifest)
    total = Decimal(outcome.scorecard.total_score or "0")
    assert Decimal(0) <= total <= Decimal(100)
    assert all(
        Decimal(0) <= Decimal(item.contribution) <= Decimal(100) for item in outcome.scorecard.items
    )


def test_a_clean_security_scan_with_no_quality_credits_thirty_plus_twenty(
    policy, ownership, profile
) -> None:  # type: ignore[no-untyped-def]
    # The floor for a fully clean, fully worthless submission is the 30 correctness points plus a
    # security score with no confirmed issue: 50, never a silent 30.
    manifest = s.manifest(
        efficiency_value=s.efficiency("0.000000"),
        code_quality_bp=0,
        idiomatic_bp=0,
        robustness_bp=0,
    )
    outcome = _score(policy, ownership, profile, manifest)
    assert outcome.scorecard.total_score == "50.000000"
    assert _dimension(outcome, ScoreDimension.CORRECTNESS).contribution == "30.000000"
    assert _dimension(outcome, ScoreDimension.SECURITY).contribution == "20.000000"


# ------------------------------------------------------------------------------------ monotonicity


def test_raising_an_item_never_lowers_the_score_at_fixed_applicability(
    policy, ownership, profile
) -> None:  # type: ignore[no-untyped-def]
    previous = Decimal(0)
    for level in range(0, 10_001, 500):
        manifest = s.manifest(
            efficiency_value=s.efficiency(f"{Decimal(level) / 100:.6f}"),
            code_quality_bp=level,
            idiomatic_bp=level,
            robustness_bp=level,
        )
        total = Decimal(_score(policy, ownership, profile, manifest).scorecard.total_score or "0")
        assert total >= previous
        previous = total


def test_more_penalties_never_raise_the_score(policy, ownership, profile) -> None:  # type: ignore[no-untyped-def]
    light = _score(policy, ownership, profile, s.manifest())
    heavy = _score(
        policy,
        ownership,
        profile,
        s.manifest(
            issues=(
                s.security_issue("a-critical", severity="critical"),
                s.security_issue("a-high", severity="high"),
            )
        ),
    )
    assert Decimal(heavy.scorecard.total_score or "0") < Decimal(light.scorecard.total_score or "0")


# --------------------------------------------------------------- duplicates and reordering


def test_adding_an_identical_duplicate_issue_cannot_change_a_score(
    policy, ownership, profile
) -> None:  # type: ignore[no-untyped-def]
    issue = s.security_issue("shared", severity="high")
    single = _score(policy, ownership, profile, s.manifest(issues=(issue,)))
    duplicated = _score(policy, ownership, profile, s.manifest(issues=(issue, issue, issue, issue)))
    assert single.scorecard.total_score == duplicated.scorecard.total_score
    assert score_identity(single) == score_identity(duplicated)
    assert [item.contribution for item in single.scorecard.items] == [
        item.contribution for item in duplicated.scorecard.items
    ]
    # The extra report is still retained as evidence, so the scorecard's own identity moves even
    # though the score does not.
    assert (
        single.scorecard.evidence_manifest_digest != duplicated.scorecard.evidence_manifest_digest
    )


def test_reordered_equivalent_evidence_yields_identical_output(policy, ownership, profile) -> None:  # type: ignore[no-untyped-def]
    issues = (
        s.security_issue("issue-a", severity="low", tools=("bandit",)),
        s.security_issue("issue-b", severity="critical", tools=("semgrep",)),
        s.security_issue("issue-c", severity="medium", tools=("bandit", "semgrep")),
    )
    forward = _score(policy, ownership, profile, s.manifest(issues=issues))
    backward = _score(policy, ownership, profile, s.manifest(issues=tuple(reversed(issues))))
    assert forward.content_digest() == backward.content_digest()
    assert canonical_document_digest(forward.scorecard) == canonical_document_digest(
        backward.scorecard
    )


def test_reordered_items_within_a_dimension_yield_identical_output(
    policy, ownership, profile
) -> None:  # type: ignore[no-untyped-def]
    items = s.code_quality_items(7000) + s.idiomatic_items(7000) + s.robustness_items(7000)
    forward = _score(policy, ownership, profile, s.manifest(items=list(items)))
    backward = _score(policy, ownership, profile, s.manifest(items=list(reversed(items))))
    assert forward.content_digest() == backward.content_digest()


# -------------------------------------------------------------------------------- unknown evidence


def test_unknown_evidence_cannot_increase_a_score(policy, ownership, profile) -> None:  # type: ignore[no-untyped-def]
    measured = _score(policy, ownership, profile, s.manifest(robustness_bp=10000))
    assert measured.scorecard.total_score is not None
    unknown = s.manifest(
        items=[
            *s.code_quality_items(8500),
            *s.idiomatic_items(9000),
            *[
                item.model_copy(
                    update={"status": "needs_review", "score_bp": None, "evidence_refs": ()}
                )
                for item in s.robustness_items(10000)
            ],
        ]
    )
    outcome = _score(policy, ownership, profile, unknown)
    # An unresolved item is not a zero and not a hundred: there is no publishable composite at all.
    assert outcome.scorecard.total_score is None
    assert outcome.scorecard.status is EvaluationState.NEEDS_REVIEW
    assert Decimal(outcome.scorecard.total_score or "0") < Decimal(measured.scorecard.total_score)


def test_a_missing_item_evidence_blocks_publication(policy, ownership, profile) -> None:  # type: ignore[no-untyped-def]
    items = [
        item.model_copy(update={"status": "missing", "score_bp": None, "evidence_refs": ()})
        if item.item_id == "decomposition"
        else item
        for item in s.code_quality_items(8500)
    ]
    manifest = s.manifest(items=[*items, *s.idiomatic_items(9000), *s.robustness_items(8000)])
    outcome = _score(policy, ownership, profile, manifest)
    assert outcome.scorecard.total_score is None
    assert any(
        reason.reference == "code_quality.decomposition" for reason in outcome.explanation.blocking
    )


def test_an_unreviewed_high_claim_blocks_rather_than_deducts(policy, ownership, profile) -> None:  # type: ignore[no-untyped-def]
    manifest = s.manifest(
        issues=(
            s.security_issue(
                "unreviewed-high", severity="high", confidence="unreviewed", adjudication=None
            ),
        )
    )
    outcome = _score(policy, ownership, profile, manifest)
    # No silent deduction: nothing is publishable until the claim is adjudicated.
    assert outcome.scorecard.total_score is None
    assert outcome.scorecard.status is EvaluationState.NEEDS_REVIEW
    security_row = next(
        row for row in outcome.explanation.dimensions if row.dimension is ScoreDimension.SECURITY
    )
    assert security_row.raw_value is None


# ----------------------------------------------------------------------------------- not applicable


def test_not_applicable_items_are_preserved_and_renormalised(policy, ownership, profile) -> None:  # type: ignore[no-untyped-def]
    # A task with no async opportunity must not be penalised for it: the item is N/A and its weight
    # is redistributed over the items that do apply.
    items = [
        item.model_copy(update={"status": "not_applicable", "score_bp": None, "evidence_refs": ()})
        if item.item_id == "error_handling_clarity"
        else item
        for item in s.idiomatic_items(9000)
    ]
    manifest = s.manifest(items=[*s.code_quality_items(8500), *items, *s.robustness_items(8000)])
    outcome = _score(policy, ownership, profile, manifest)
    row = next(
        item for item in outcome.explanation.items if item.item_id == "error_handling_clarity"
    )
    assert row.status == "not_applicable"
    assert row.applicable is False
    assert row.raw_value is None
    assert row.effective_weight_bps == 0
    assert row.contribution == "0.000000"
    assert outcome.scorecard.status is EvaluationState.READY


def test_applicability_never_changes_per_candidate(policy, ownership, profile) -> None:  # type: ignore[no-untyped-def]
    # The manifest claims efficiency does not apply while the frozen task says it does. Scoring
    # must not quietly renormalise around the disagreement.
    manifest = s.manifest(
        applicable=tuple(d for d in s.ALL_QUALITY if d is not ScoreDimension.EFFICIENCY),
        include_efficiency=False,
    )
    with pytest.raises(ScoringRefused) as raised:
        score_evaluation(s.frozen_task(), policy, manifest, ownership=ownership, profile=profile)
    assert raised.value.code is ScoringRefusalCode.APPLICABILITY_DISAGREEMENT


def test_a_dimension_with_no_applicable_item_is_refused(policy, ownership, profile) -> None:  # type: ignore[no-untyped-def]
    items = [
        item.model_copy(update={"status": "not_applicable", "score_bp": None, "evidence_refs": ()})
        for item in s.robustness_items(8000)
    ]
    manifest = s.manifest(items=[*s.code_quality_items(8500), *s.idiomatic_items(9000), *items])
    with pytest.raises(ScoringRefused) as raised:
        _score(policy, ownership, profile, manifest)
    assert raised.value.code is ScoringRefusalCode.APPLICABILITY_DISAGREEMENT


def test_a_predeclared_na_dimension_rejects_items_supplied_for_it(
    policy, ownership, profile
) -> None:  # type: ignore[no-untyped-def]
    applicable = tuple(d for d in s.ALL_QUALITY if d is not ScoreDimension.EFFICIENCY)
    # The dimension is N/A for this task, yet someone reports items for it anyway. Accepting them
    # would silently reintroduce a dimension the frozen task removed.
    manifest = s.manifest(
        applicable=applicable,
        include_efficiency=False,
        include_items=False,
    )
    efficiency_items = (
        s.measured("runtime", ScoreDimension.EFFICIENCY, 10_000, 9_000, source="measurement"),
    )
    with_items = s.replace(
        manifest, items=[item.model_dump(mode="json") for item in efficiency_items]
    )
    with pytest.raises(ScoringRefused) as raised:
        _score(policy, ownership, profile, with_items, applicable)
    assert raised.value.code is ScoringRefusalCode.APPLICABILITY_DISAGREEMENT


# ------------------------------------------------------------------------------------ ownership


def test_duplicate_issue_ownership_across_dimensions_is_rejected(ownership) -> None:  # type: ignore[no-untyped-def]
    clash = (
        s.security_issue("shared", severity="high", owner=ScoreDimension.SECURITY),
        s.security_issue(
            "shared", severity="high", owner=ScoreDimension.ROBUSTNESS, digest=s.DIGEST_C
        ),
    )
    with pytest.raises(ScoringRefused) as raised:
        resolve_owners(
            clash,
            ownership,
            penalty_for_severity=lambda _severity: 25,
        )
    assert raised.value.code is ScoringRefusalCode.DUPLICATE_ISSUE_OWNERSHIP


def test_a_contradictory_declared_owner_is_rejected(ownership) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ScoringRefused) as raised:
        resolve_owners(
            (s.security_issue("shared", owner=ScoreDimension.IDIOMATIC),),
            ownership,
            penalty_for_severity=lambda _severity: 25,
        )
    assert raised.value.code is ScoringRefusalCode.DUPLICATE_ISSUE_OWNERSHIP


def test_an_unmapped_issue_family_is_rejected(ownership) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ScoringRefused) as raised:
        resolve_owners(
            (s.security_issue("mystery", family="never-declared-family"),),
            ownership,
            penalty_for_severity=lambda _severity: 25,
        )
    assert raised.value.code is ScoringRefusalCode.UNMAPPED_ISSUE_FAMILY


def test_two_penalty_kinds_without_a_policy_justification_are_rejected(ownership) -> None:  # type: ignore[no-untyped-def]
    undeclared = (
        s.security_issue("shared", penalty_kind="security-penalty"),
        s.security_issue("shared", penalty_kind="measured-overhead", digest=s.DIGEST_C),
    )
    with pytest.raises(ScoringRefused) as raised:
        resolve_owners(undeclared, ownership, penalty_for_severity=lambda _severity: 25)
    assert raised.value.code is ScoringRefusalCode.UNJUSTIFIED_DISTINCT_CONSEQUENCE


def test_an_inherited_baseline_issue_is_not_blamed_on_the_candidate(ownership) -> None:  # type: ignore[no-untyped-def]
    ledger = resolve_owners(
        (
            s.security_issue("inherited", relation="unchanged_out_of_scope"),
            s.security_issue("introduced", relation="introduced"),
        ),
        ownership,
        penalty_for_severity=lambda _severity: 25,
    )
    assert ledger.counted_penalty(ScoreDimension.SECURITY) == 25
    assert "inherited" in ledger.ignored_reports


def test_an_issue_owned_by_another_dimension_does_not_lower_security(
    policy, ownership, profile
) -> None:  # type: ignore[no-untyped-def]
    # A cleanup defect measured as a robustness consequence is not also deducted from security.
    manifest = s.manifest(
        issues=(
            s.security_issue(
                "leak",
                severity="high",
                family="resource-cleanup-failure",
                penalty_kind="robustness-consequence",
                tools=("ruff",),
            ),
        )
    )
    outcome = _score(policy, ownership, profile, manifest)
    security_row = next(
        row for row in outcome.explanation.dimensions if row.dimension is ScoreDimension.SECURITY
    )
    assert security_row.raw_value == "100.000000"
    assert outcome.explanation.issues[0].owner is ScoreDimension.ROBUSTNESS
    assert outcome.explanation.issues[0].counted is True


def test_issue_ledger_is_ordered_by_dimension_then_key(ownership) -> None:  # type: ignore[no-untyped-def]
    ledger = resolve_owners(
        (
            s.security_issue("z-key", severity="low", tools=("bandit",)),
            s.security_issue(
                "a-key",
                severity="low",
                family="resource-cleanup-failure",
                penalty_kind="robustness-consequence",
                tools=("ruff",),
            ),
        ),
        ownership,
        penalty_for_severity=lambda _severity: 3,
    )
    assert [issue.owner for issue in ledger.issues] == [
        ScoreDimension.SECURITY,
        ScoreDimension.ROBUSTNESS,
    ]


def test_diagnostic_profile_items_carry_no_composite_weight() -> None:
    from polycodebench_scoring.manifest import DiagnosticItemView

    view = DiagnosticItemView(
        item_id="security", language_id="python", weight_bp=2500, status="measured", score_bp=4000
    )
    assert view.composite_weight_bp == 0
    with pytest.raises(ValueError):
        DiagnosticItemView(
            item_id="security",
            language_id="python",
            weight_bp=2500,
            status="measured",
            score_bp=4000,
            composite_weight_bp=10,
        )


def test_a_required_acceptance_scenario_cannot_be_deducted_twice(
    policy, ownership, profile
) -> None:  # type: ignore[no-untyped-def]
    items = [
        item.model_copy(update={"hard_acceptance": True})
        if item.item_id == "malformed_input"
        else item
        for item in s.robustness_items(8000)
    ]
    manifest = s.manifest(items=[*s.code_quality_items(8500), *s.idiomatic_items(9000), *items])
    with pytest.raises(ScoringRefused) as raised:
        _score(policy, ownership, profile, manifest)
    assert raised.value.code is ScoringRefusalCode.REVIEW_ROUTES_TO_DIMENSION


def test_an_item_weight_the_policy_did_not_freeze_is_rejected(policy, ownership, profile) -> None:  # type: ignore[no-untyped-def]
    items = [
        item.model_copy(update={"weight_bp": 3000}) if item.item_id == "decomposition" else item
        for item in s.code_quality_items(8500)
    ]
    manifest = s.manifest(items=[*items, *s.idiomatic_items(9000), *s.robustness_items(8000)])
    with pytest.raises(ScoringRefused) as raised:
        _score(policy, ownership, profile, manifest)
    assert raised.value.code is ScoringRefusalCode.WEIGHT_MISMATCH
