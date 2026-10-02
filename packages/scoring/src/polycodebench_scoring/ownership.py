"""Canonical issue ownership (Technical Spec 14.5; Architecture 8.1, 8.7).

One canonical issue, one composite owner. A diagnostic profile may *reuse* the evidence that
already produced a security or robustness penalty, but it may not deduct for it a second time, and
two tools reporting one defect never add up to two defects.

The ledger returned here is the traceable record of that decision: which family mapped to which
owner, which reports collapsed, and why a consequence that looks like a duplicate was allowed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from polycodebench_core.models import ScoreDimension, Slug
from pydantic import Field, model_validator

from polycodebench_scoring.contracts import ScoringModel
from polycodebench_scoring.errors import ScoringRefusalCode, ScoringRefused
from polycodebench_scoring.manifest import EvidenceRef, SecurityIssueEvidence
from polycodebench_scoring.policy import DIMENSION_ORDER

Relation = Literal[
    "introduced",
    "worsened",
    "unchanged_in_scope",
    "unchanged_out_of_scope",
    "resolved",
    "unknown",
]


class OwnershipRule(ScoringModel):
    kind: Literal["ownership_rule"] = "ownership_rule"
    family: str = Field(min_length=1, max_length=200)
    owner: ScoreDimension
    allow_task_override_before_freeze: bool


class DistinctConsequence(ScoringModel):
    """Two consequences of one underlying issue that the policy has agreed are independent."""

    kind: Literal["distinct_consequence"] = "distinct_consequence"
    issue_key: str = Field(min_length=1, max_length=200)
    penalty_kind: str = Field(min_length=1, max_length=120)
    justification: str = Field(min_length=24, max_length=800)
    separate_evidence: str = Field(min_length=1, max_length=200)


class EvidenceOwnership(ScoringModel):
    """The frozen family-to-owner mapping used to break ties between competing claims."""

    kind: Literal["evidence_ownership"] = "evidence_ownership"
    policy_id: Slug
    status: str
    effective_for_scoring: bool
    invariant: Literal["each_canonical_issue_has_one_composite_owner"]
    rules: tuple[OwnershipRule, ...]
    relations_without_penalty: tuple[Relation, ...]
    relations_penalized: tuple[Relation, ...]
    duplicate_issue_key_policy: Literal["collapse_to_canonical_issue"] = (
        "collapse_to_canonical_issue"
    )
    unreviewed_impact_requires_adjudication: bool
    adjudication_blocking_severities: tuple[str, ...]
    distinct_consequences: tuple[DistinctConsequence, ...] = ()
    required_evidence: tuple[str, ...] = ()

    @model_validator(mode="after")
    def families_are_unambiguous(self) -> EvidenceOwnership:
        families = [rule.family for rule in self.rules]
        if len(set(families)) != len(families):
            raise ValueError("a canonical family maps to exactly one composite owner")
        overlap = set(self.relations_without_penalty) & set(self.relations_penalized)
        if overlap:
            raise ValueError(f"a relation cannot be both penalized and ignored: {sorted(overlap)}")
        if not self.relations_without_penalty or not self.relations_penalized:
            raise ValueError("both penalized and ignored baseline relations must be declared")
        pairs = [(entry.issue_key, entry.penalty_kind) for entry in self.distinct_consequences]
        if len(set(pairs)) != len(pairs):
            raise ValueError("a distinct consequence is declared once per penalty kind")
        return self

    def default_owner(self, family: str) -> ScoreDimension | None:
        for rule in self.rules:
            if rule.family == family:
                return rule.owner
        return None

    def allows_task_override(self, family: str) -> bool:
        for rule in self.rules:
            if rule.family == family:
                return rule.allow_task_override_before_freeze
        return False


class OwnedIssue(ScoringModel):
    """One canonical issue after collapsing duplicates and resolving ownership."""

    kind: Literal["owned_issue"] = "owned_issue"
    issue_key: str
    family: str
    penalty_kind: str
    owner: ScoreDimension
    severity: str
    confidence: str
    relation: Relation
    penalty_bp: int = Field(ge=0)
    counted: bool
    blocks_completion: bool
    tools: tuple[Slug, ...]
    collapsed_reports: int = Field(ge=1)
    evidence_refs: tuple[EvidenceRef, ...] = ()
    reason: str


class OwnershipLedger(ScoringModel):
    """Deterministic record of every ownership decision, in canonical issue-key order."""

    kind: Literal["ownership_ledger"] = "ownership_ledger"
    ownership_policy_id: Slug
    ownership_policy_digest: str
    task_overrides: tuple[str, ...] = ()
    issues: tuple[OwnedIssue, ...]
    collapsed_duplicates: int = Field(ge=0)
    ignored_reports: tuple[str, ...] = ()

    def counted_penalty(self, owner: ScoreDimension) -> int:
        return sum(
            issue.penalty_bp for issue in self.issues if issue.counted and issue.owner is owner
        )

    def counted_by_owner(self, owner: ScoreDimension) -> tuple[OwnedIssue, ...]:
        return tuple(issue for issue in self.issues if issue.counted and issue.owner is owner)

    def blocking(self) -> tuple[OwnedIssue, ...]:
        return tuple(issue for issue in self.issues if issue.blocks_completion)


_SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}


def resolve_owners(
    issues: tuple[SecurityIssueEvidence, ...],
    ownership: EvidenceOwnership,
    *,
    task_overrides: dict[str, ScoreDimension] | None = None,
    penalty_for_severity: Callable[[str], int],
) -> OwnershipLedger:
    """Collapse duplicates, resolve each issue to one owner, and refuse contradictions.

    ``issues`` may arrive in any order and may carry several reports of the same canonical issue;
    the ledger is identical either way.
    """
    overrides = task_overrides or {}
    groups: dict[str, list[SecurityIssueEvidence]] = {}
    for issue in issues:
        groups.setdefault(issue.issue_key, []).append(issue)

    owned: list[OwnedIssue] = []
    collapsed = 0
    ignored: list[str] = []
    ownership_kinds: dict[tuple[str, str], ScoreDimension] = {}

    for issue_key in sorted(groups):
        reports = sorted(groups[issue_key], key=lambda report: report.penalty_kind)
        collapsed += len(reports) - 1
        primary = max(reports, key=lambda report: _SEVERITY_RANK[report.severity])

        families = {report.family for report in reports}
        if len(families) > 1:
            raise ScoringRefused(
                ScoringRefusalCode.INCONSISTENT_ISSUE_RECORD,
                f"canonical issue {issue_key!r} was reported under families {sorted(families)}",
            )

        owner = _resolve_owner(primary, overrides, ownership)
        # Every report of the canonical issue must agree with the resolved owner, not just the one
        # whose severity happens to be highest.
        for report in reports:
            if report is not primary:
                _resolve_owner(report, overrides, ownership)
        penalty_kind = primary.penalty_kind
        key = (issue_key, penalty_kind)
        if key in ownership_kinds and ownership_kinds[key] is not owner:
            raise ScoringRefused(
                ScoringRefusalCode.DUPLICATE_ISSUE_OWNERSHIP,
                f"issue {issue_key!r} penalty {penalty_kind!r} is claimed by both "
                f"{ownership_kinds[key].value} and {owner.value}",
            )
        ownership_kinds[key] = owner

        penalty_kinds = sorted({report.penalty_kind for report in reports})
        if len(penalty_kinds) > 1:
            declared = {
                (entry.issue_key, entry.penalty_kind) for entry in ownership.distinct_consequences
            }
            undeclared = [kind for kind in penalty_kinds if (issue_key, kind) not in declared]
            if undeclared:
                raise ScoringRefused(
                    ScoringRefusalCode.UNJUSTIFIED_DISTINCT_CONSEQUENCE,
                    f"issue {issue_key!r} carries undeclared penalty kinds {undeclared}; "
                    "the policy must explain why these are separate consequences",
                )

        counted, blocking, reason = _verdict(primary, ownership)
        if not counted:
            ignored.append(issue_key)
        owned.append(
            OwnedIssue(
                issue_key=issue_key,
                family=primary.family,
                penalty_kind=penalty_kind,
                owner=owner,
                severity=primary.severity,
                confidence=primary.confidence,
                relation=primary.relation,
                penalty_bp=penalty_for_severity(primary.severity) if counted else 0,
                counted=counted,
                blocks_completion=blocking,
                tools=tuple(sorted({tool for report in reports for tool in report.tools})),
                collapsed_reports=len(reports),
                evidence_refs=tuple(
                    reference for report in reports for reference in report.evidence_refs
                ),
                reason=reason,
            )
        )

    owned.sort(key=lambda entry: (DIMENSION_ORDER[entry.owner], entry.issue_key))
    return OwnershipLedger(
        ownership_policy_id=ownership.policy_id,
        ownership_policy_digest=ownership.content_digest(),
        task_overrides=tuple(f"{key}={value.value}" for key, value in sorted(overrides.items())),
        issues=tuple(owned),
        collapsed_duplicates=collapsed,
        ignored_reports=tuple(sorted(ignored)),
    )


def _resolve_owner(
    issue: SecurityIssueEvidence,
    overrides: dict[str, ScoreDimension],
    ownership: EvidenceOwnership,
) -> ScoreDimension:
    declared = issue.declared_owner
    policy_owner = ownership.default_owner(issue.family)
    if policy_owner is None:
        raise ScoringRefused(
            ScoringRefusalCode.UNMAPPED_ISSUE_FAMILY,
            f"canonical family {issue.family!r} has no composite owner in {ownership.policy_id!r}",
        )
    if issue.family in overrides:
        if not ownership.allows_task_override(issue.family):
            raise ScoringRefused(
                ScoringRefusalCode.DUPLICATE_ISSUE_OWNERSHIP,
                f"task overrides are not permitted for family {issue.family!r}",
            )
        return overrides[issue.family]
    if declared is not None and declared is not policy_owner:
        raise ScoringRefused(
            ScoringRefusalCode.DUPLICATE_ISSUE_OWNERSHIP,
            f"issue {issue.issue_key!r} declares owner {declared.value!r} but family "
            f"{issue.family!r} is owned by {policy_owner.value!r}",
        )
    return policy_owner


def _verdict(issue: SecurityIssueEvidence, ownership: EvidenceOwnership) -> tuple[bool, bool, str]:
    """Return ``(counted, blocks_completion, reason)`` for one canonical issue."""
    if issue.adjudication == "rejected":
        return False, False, "adjudication_rejected"
    if issue.relation in ownership.relations_without_penalty:
        return False, False, f"relation_{issue.relation}_is_not_blamed_on_the_candidate"
    if issue.relation == "unknown":
        return False, True, "relation_unknown_requires_adjudication"
    if issue.adjudication != "confirmed":
        blocking = (
            ownership.unreviewed_impact_requires_adjudication
            and issue.severity in ownership.adjudication_blocking_severities
        )
        return False, blocking, "unconfirmed_claim_is_not_deducted"
    return True, False, "confirmed_owned_issue"
