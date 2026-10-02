"""Score replay (Technical Spec 12.4, E2E-24).

Replay answers one question: *does the archived evidence still produce the archived score?* It
re-runs the pure scorer over the stored manifest and compares canonical digests. It never asks for
another candidate, another judge, another performance run or a clock reading - there is no code
path here that could.

Version handling is explicit rather than implicit:

* a manifest naming a score-schema version this scorer does not implement is refused;
* a policy whose digest no longer matches the archived scorecard is refused as a policy change, not
  silently rescored;
* a scorer-source digest that no longer matches is reported, so a *different implementation* is
  never reported as a faithful reproduction.
"""

from __future__ import annotations

from typing import Literal

from polycodebench_core.canonical import canonical_document_digest
from polycodebench_core.models import Scorecard
from polycodebench_plugins_api import FrozenTask, LanguageProfile

from polycodebench_scoring.contracts import ScoringModel
from polycodebench_scoring.errors import ScoringRefusalCode, ScoringRefused
from polycodebench_scoring.manifest import ValidatedEvidenceManifest
from polycodebench_scoring.ownership import EvidenceOwnership
from polycodebench_scoring.policy import FrozenScoringPolicy
from polycodebench_scoring.scorer import ScoringOutcome, score_evaluation


class ReplayCheck(ScoringModel):
    kind: str = "replay_check"
    name: str
    passed: bool
    expected: str
    actual: str


class ReplayReport(ScoringModel):
    """The complete answer to "does this reproduce?" with every comparison shown."""

    kind: str = "replay_report"
    schema_version: Literal[1] = 1
    matched: bool
    outcome_digest: str
    checks: tuple[ReplayCheck, ...] = ()


def replay_scorecard(
    task: FrozenTask,
    policy: FrozenScoringPolicy,
    evidence: ValidatedEvidenceManifest,
    *,
    ownership: EvidenceOwnership,
    profile: LanguageProfile | None = None,
    archived: Scorecard | None = None,
    scorer_digest: str | None = None,
) -> ReplayReport:
    """Recompute a scorecard from archived evidence and compare it with the archived one."""
    if archived is not None:
        _assert_same_schema(archived, policy)
    outcome = score_evaluation(task, policy, evidence, ownership=ownership, profile=profile)
    report = ReplayReport(
        matched=archived is None or outcome.scorecard == archived,
        outcome_digest=outcome.content_digest(),
        checks=_checks(outcome, archived, evidence, policy, scorer_digest),
    )
    if archived is not None and not report.matched:
        raise ScoringRefused(
            ScoringRefusalCode.REPLAY_DIGEST_MISMATCH,
            "replay did not reproduce the archived scorecard: "
            + "; ".join(
                f"{check.name} expected {check.expected} got {check.actual}"
                for check in report.checks
                if not check.passed
            ),
        )
    return report


def replay_outcome(
    task: FrozenTask,
    policy: FrozenScoringPolicy,
    evidence: ValidatedEvidenceManifest,
    *,
    ownership: EvidenceOwnership,
    profile: LanguageProfile | None = None,
    archived: ScoringOutcome | None = None,
) -> ReplayReport:
    """Recompute an outcome and require byte-identical canonical output."""
    if archived is not None and archived.score_schema_version != policy.score_schema_version:
        raise ScoringRefused(
            ScoringRefusalCode.SCORE_SCHEMA_UNSUPPORTED,
            f"archived outcome uses score schema {archived.score_schema_version}; policy "
            f"{policy.policy_id!r} declares {policy.score_schema_version}",
        )
    outcome = score_evaluation(task, policy, evidence, ownership=ownership, profile=profile)
    checks: list[ReplayCheck] = [
        _check(
            "outcome_digest",
            canonical_document_digest(archived) if archived else outcome.content_digest(),
            outcome.content_digest(),
        )
    ]
    if archived is not None:
        checks.append(
            _check(
                "scorecard_digest",
                canonical_document_digest(archived.scorecard),
                canonical_document_digest(outcome.scorecard),
            )
        )
        checks.append(
            _check(
                "total_score",
                str(archived.scorecard.total_score),
                str(outcome.scorecard.total_score),
            )
        )
        checks.append(
            _check(
                "item_count",
                str(len(archived.scorecard.items)),
                str(len(outcome.scorecard.items)),
            )
        )
    matched = all(check.passed for check in checks)
    return ReplayReport(
        matched=matched,
        outcome_digest=outcome.content_digest(),
        checks=tuple(checks),
    )


def _assert_same_schema(archived: Scorecard, policy: FrozenScoringPolicy) -> None:
    if archived.scoring_policy_digest != policy.content_digest():
        raise ScoringRefused(
            ScoringRefusalCode.REPLAY_DIGEST_MISMATCH,
            "the archived scorecard was produced by a different scoring policy digest; "
            "replaying it under this policy would be a new score, not a replay",
        )


def _checks(
    outcome: ScoringOutcome,
    archived: Scorecard | None,
    evidence: ValidatedEvidenceManifest,
    policy: FrozenScoringPolicy,
    scorer_digest: str | None,
) -> tuple[ReplayCheck, ...]:
    recomputed_scorer = evidence.invocation.scorer_digest
    checks = [
        _check(
            "scoring_policy_digest",
            archived.scoring_policy_digest if archived else policy.content_digest(),
            policy.content_digest(),
        ),
        _check(
            "evidence_manifest_digest",
            archived.evidence_manifest_digest if archived else evidence.content_digest(),
            evidence.content_digest(),
        ),
        _check(
            "scorer_digest",
            scorer_digest or (archived.scorer_digest if archived else recomputed_scorer),
            recomputed_scorer,
        ),
    ]
    if archived is not None:
        checks.extend(
            (
                _check(
                    "total_score", str(archived.total_score), str(outcome.scorecard.total_score)
                ),
                _check("gate", str(archived.gate), str(outcome.scorecard.gate)),
                _check("status", str(archived.status), str(outcome.scorecard.status)),
                _check(
                    "scorecard_digest",
                    canonical_document_digest(archived),
                    canonical_document_digest(outcome.scorecard),
                ),
            )
        )
    return tuple(checks)


def _check(name: str, expected: str, actual: str) -> ReplayCheck:
    return ReplayCheck(name=name, passed=expected == actual, expected=expected, actual=actual)
