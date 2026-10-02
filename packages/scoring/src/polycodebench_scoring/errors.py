"""Scoring refusals.

Every refusal is a *code*, not a number. Nothing in the scorer is allowed to turn absent, ambiguous
or contradictory evidence into a score: it either produces one number from evidence it can explain,
or it refuses with a code a reader can act on.
"""

from __future__ import annotations

from enum import StrEnum


class ScoringRefusalCode(StrEnum):
    """Why the scorer declined to produce a composite."""

    # Frozen inputs the scorer will not guess about.
    POLICY_INVALID = "policy_invalid"
    APPLICABILITY_DISAGREEMENT = "applicability_disagreement"
    UNKNOWN_RUBRIC_ITEM = "unknown_rubric_item"
    WEIGHT_MISMATCH = "weight_mismatch"
    IDENTITY_MISMATCH = "identity_mismatch"
    # Evidence that must exist before a number may be published.
    MISSING_REQUIRED_EVIDENCE = "missing_required_evidence"
    UNRESOLVED_ITEM = "unresolved_item"
    UNKNOWN_GATE = "unknown_gate"
    # Evidence that must not contradict itself.
    UNMAPPED_ISSUE_FAMILY = "unmapped_issue_family"
    DUPLICATE_ISSUE_OWNERSHIP = "duplicate_issue_ownership"
    UNJUSTIFIED_DISTINCT_CONSEQUENCE = "unjustified_distinct_consequence"
    INCONSISTENT_ISSUE_RECORD = "inconsistent_issue_record"
    EFFICIENCY_INPUT_INVALID = "efficiency_input_invalid"
    EFFICIENCY_SCORE_MISMATCH = "efficiency_score_mismatch"
    REVIEW_ROUTES_TO_DIMENSION = "review_routes_to_dimension"
    # Replay and version handling.
    SCORE_SCHEMA_UNSUPPORTED = "score_schema_unsupported"
    REPLAY_DIGEST_MISMATCH = "replay_digest_mismatch"


class ScoringRefused(ValueError):
    """A refusal that must never be converted into zero or into a perfect score."""

    def __init__(self, code: ScoringRefusalCode, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class ScoringConfigError(ValueError):
    """A policy or profile document that cannot be loaded into a frozen contract."""
