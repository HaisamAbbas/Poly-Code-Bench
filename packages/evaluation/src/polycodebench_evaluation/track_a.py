"""Track A detection, adjudication, repair and cohort scoring (Spec 11.3, 16).

The module deliberately separates a submitted finding from a reviewed match and from a
repair grade. Nothing here infers a true positive from a file name, and a failed repair
never erases a valid detection. Artifacts produced from local authored examples remain
internal development evidence; this code does not label them model benchmark results.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar, Literal

from polycodebench_core.canonical import (
    canonical_digest,
    canonical_document_digest,
    canonical_envelope,
)
from polycodebench_core.identity import new_entity_id
from polycodebench_core.models import Candidate, ContractModel
from polycodebench_core.solve_contracts import PathForbidden, normalize_workspace_path
from polycodebench_core.solve_extraction import Finding
from polycodebench_plugins_api import FrozenTask
from polycodebench_runner.guest_helper import ToolFailure, plan_patch
from pydantic import Field, ValidationError, model_validator

from polycodebench_evaluation.evaluator import Evaluation, Evaluator
from polycodebench_evaluation.plan_runner import digest_files

Severity = Literal["low", "medium", "high", "critical"]
_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
_SOURCE_FAMILIES = ("historical", "disclosed_security", "authored_history", "injected", "mutation")


class TrackAModel(ContractModel):
    """A canonical, content-addressable Track A record."""

    kind: str = "track_a_record"
    schema_version: Literal[1] = 1

    def content_digest(self) -> str:
        return canonical_document_digest(self)


class SourceAsset(TrackAModel):
    kind: Literal["source_asset"] = "source_asset"
    asset_id: str
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    visibility: Literal["public", "private", "hidden"]
    role: Literal["visible_source", "hidden_oracle", "reproducer", "injection_log", "repair"]


class TaskSourceProvenance(TrackAModel):
    kind: Literal["task_source_provenance"] = "task_source_provenance"
    """Provenance requirements for historical, disclosed, authored and mutated tasks."""

    source_family: Literal[
        "historical", "disclosed_security", "authored_history", "injected", "mutation"
    ]
    language: Literal["python", "rust"]
    repository: str | None = None
    issue_or_advisory: str | None = None
    pre_fix_revision: str | None = None
    fix_revision: str | None = None
    rights_record_id: str | None = None
    rights_verified: bool = False
    evidence_verified: bool = False
    first_public_at: str | None = None
    reproduced: bool = False
    isolated_reproducer: bool = False
    authorship_record_id: str | None = None
    mutation_operator: str | None = None
    mutation_version: str | None = None
    mutation_seed: int | None = None
    changed_span: tuple[str, int, int] | None = None
    behavior_change_proof: str | None = None
    reference_repair_proof: str | None = None
    build_proof: str | None = None
    clean_control_id: str | None = None
    equivalent_to_existing: bool = False
    duplicate_mutation: bool = False
    unintended_or_nonbuilding: bool = False
    assets: tuple[SourceAsset, ...]
    evidence_tier: Literal["verified_external", "authored_internal", "synthetic_internal"]

    @model_validator(mode="after")
    def private_injection_log(self) -> TaskSourceProvenance:
        if self.changed_span is not None:
            path, start, end = self.changed_span
            try:
                normalized = normalize_workspace_path(path)
            except PathForbidden as exc:
                raise ValueError("mutation changed span path is unsafe") from exc
            if normalized != path or start < 1 or end < start:
                raise ValueError("mutation changed span must be canonical and ordered")
        for asset in self.assets:
            if asset.role == "injection_log" and asset.visibility != "hidden":
                raise ValueError("injection logs must stay hidden")
            if asset.role == "hidden_oracle" and asset.visibility != "hidden":
                raise ValueError("hidden oracles must stay hidden")
            if asset.role == "visible_source" and asset.visibility == "hidden":
                raise ValueError("visible task source cannot be hidden")
        return self


class SourceAdmission(TrackAModel):
    kind: Literal["source_admission"] = "source_admission"
    admitted: bool
    blockers: tuple[str, ...]
    evidence_tier: str
    source_family: str


def validate_source_admission(source: TaskSourceProvenance) -> SourceAdmission:
    """Fail closed when source, rights, reproducibility or mutation evidence is incomplete."""
    required: dict[str, tuple[str, ...]] = {
        "historical": (
            "repository",
            "issue_or_advisory",
            "pre_fix_revision",
            "fix_revision",
            "rights_record_id",
        ),
        "disclosed_security": (
            "repository",
            "issue_or_advisory",
            "pre_fix_revision",
            "fix_revision",
            "rights_record_id",
            "first_public_at",
        ),
        "authored_history": (
            "authorship_record_id",
            "pre_fix_revision",
            "fix_revision",
            "rights_record_id",
            "clean_control_id",
        ),
        "injected": (
            "authorship_record_id",
            "behavior_change_proof",
            "build_proof",
            "clean_control_id",
        ),
        "mutation": (
            "mutation_operator",
            "mutation_version",
            "mutation_seed",
            "changed_span",
            "behavior_change_proof",
            "reference_repair_proof",
            "build_proof",
            "clean_control_id",
        ),
    }
    blockers = [
        f"missing_{field}"
        for field in required[source.source_family]
        if getattr(source, field) is None
    ]
    if not source.rights_record_id:
        blockers.append("missing_rights_record_id")
    if not source.rights_verified:
        blockers.append("rights_evidence_not_verified")
    if not source.evidence_verified:
        blockers.append("source_and_behavior_evidence_not_verified")
    if source.source_family in {"historical", "disclosed_security"} and not any(
        asset.role == "reproducer" for asset in source.assets
    ):
        blockers.append("reproducer_asset_missing")
    if not any(asset.role == "visible_source" for asset in source.assets):
        blockers.append("visible_source_asset_missing")
    if not any(asset.role == "hidden_oracle" for asset in source.assets):
        blockers.append("hidden_oracle_asset_missing")
    if source.source_family in {"historical", "disclosed_security"} and not source.reproduced:
        blockers.append("pre_fix_reproducer_not_verified")
    if source.source_family == "disclosed_security" and not source.isolated_reproducer:
        blockers.append("security_reproducer_not_isolated")
    if source.source_family == "mutation":
        if source.equivalent_to_existing:
            blockers.append("equivalent_mutation_rejected")
        if source.duplicate_mutation:
            blockers.append("duplicate_mutation_rejected")
        if source.unintended_or_nonbuilding:
            blockers.append("unintended_or_nonbuilding_mutation_rejected")
    if source.evidence_tier == "verified_external" and not source.rights_record_id:
        blockers.append("external_rights_not_verified")
    if source.source_family == "disclosed_security" and source.evidence_tier != "verified_external":
        blockers.append("public_security_source_requires_verified_external_evidence")
    return SourceAdmission(
        admitted=not blockers,
        blockers=tuple(sorted(set(blockers))),
        evidence_tier=source.evidence_tier,
        source_family=source.source_family,
    )


def public_source_projection(
    source: TaskSourceProvenance, asset_bytes: Mapping[str, bytes]
) -> dict[str, bytes]:
    """Return only explicitly public, non-oracle assets for a reviewed publication builder."""
    admission = validate_source_admission(source)
    if not admission.admitted:
        raise ValueError("source provenance is not admitted: " + ",".join(admission.blockers))
    declared = {asset.asset_id: asset for asset in source.assets}
    if not set(asset_bytes) <= set(declared):
        raise ValueError("projection input references an undeclared asset")
    return {
        asset_id: data
        for asset_id, data in asset_bytes.items()
        if declared[asset_id].visibility == "public"
        and declared[asset_id].role in {"visible_source", "reproducer"}
    }


class MutationRecord(TrackAModel):
    kind: Literal["mutation_record"] = "mutation_record"
    operator: str
    operator_version: str
    seed: int
    source_digest: str
    mutated_digest: str
    changed_path: str
    changed_start_line: int = Field(ge=1)
    changed_end_line: int = Field(ge=1)
    behavior_change_proof: str
    reference_repair_proof: str
    build_proof: str


def build_authored_history_source(
    *,
    language: Literal["python", "rust"],
    repository_id: str,
    issue_id: str,
    pre_fix: Mapping[str, bytes],
    reference_fix: Mapping[str, bytes],
    reproducer: Mapping[str, bytes],
    clean_control_id: str,
    rights_record_id: str,
    authorship_record_id: str,
    rights_verified: bool,
    evidence_verified: bool,
) -> TaskSourceProvenance:
    """Build an authored local pre-fix/fix source record from immutable byte bundles."""
    pre_digest = digest_files(pre_fix)
    fix_digest = digest_files(reference_fix)
    return TaskSourceProvenance(
        source_family="authored_history",
        language=language,
        repository=f"internal://{repository_id}",
        issue_or_advisory=issue_id,
        pre_fix_revision=pre_digest,
        fix_revision=fix_digest,
        rights_record_id=rights_record_id,
        rights_verified=rights_verified,
        evidence_verified=evidence_verified,
        reproduced=True,
        authorship_record_id=authorship_record_id,
        clean_control_id=clean_control_id,
        assets=(
            SourceAsset(
                asset_id=f"{issue_id}-visible-pre-fix",
                digest=pre_digest,
                visibility="private",
                role="visible_source",
            ),
            SourceAsset(
                asset_id=f"{issue_id}-reproducer",
                digest=digest_files(reproducer),
                visibility="private",
                role="reproducer",
            ),
            SourceAsset(
                asset_id=f"{issue_id}-reference-fix",
                digest=fix_digest,
                visibility="hidden",
                role="hidden_oracle",
            ),
        ),
        evidence_tier="authored_internal",
    )


def build_injected_source(
    *,
    language: Literal["python", "rust"],
    task_id: str,
    injected_source: Mapping[str, bytes],
    reference_fix: Mapping[str, bytes],
    reproducer: Mapping[str, bytes],
    private_injection_log: bytes,
    clean_control_id: str,
    rights_record_id: str,
    authorship_record_id: str,
    behavior_change_proof: str,
    build_proof: str,
    rights_verified: bool,
    evidence_verified: bool,
) -> TaskSourceProvenance:
    """Build an injection task while keeping the operator log private by construction."""
    return TaskSourceProvenance(
        source_family="injected",
        language=language,
        rights_record_id=rights_record_id,
        rights_verified=rights_verified,
        evidence_verified=evidence_verified,
        authorship_record_id=authorship_record_id,
        behavior_change_proof=behavior_change_proof,
        build_proof=build_proof,
        clean_control_id=clean_control_id,
        assets=(
            SourceAsset(
                asset_id=f"{task_id}-visible-injected-source",
                digest=digest_files(injected_source),
                visibility="private",
                role="visible_source",
            ),
            SourceAsset(
                asset_id=f"{task_id}-reproducer",
                digest=digest_files(reproducer),
                visibility="private",
                role="reproducer",
            ),
            SourceAsset(
                asset_id=f"{task_id}-hidden-reference",
                digest=digest_files(reference_fix),
                visibility="hidden",
                role="hidden_oracle",
            ),
            SourceAsset(
                asset_id=f"{task_id}-operator-log",
                digest="sha256:" + hashlib.sha256(private_injection_log).hexdigest(),
                visibility="hidden",
                role="injection_log",
            ),
        ),
        evidence_tier="synthetic_internal",
    )


def build_mutation_source(
    *,
    language: Literal["python", "rust"],
    task_id: str,
    mutated_source: Mapping[str, bytes],
    reference_fix: Mapping[str, bytes],
    reproducer: Mapping[str, bytes],
    private_mutation_log: bytes,
    clean_control_id: str,
    rights_record_id: str,
    authorship_record_id: str,
    mutation: MutationRecord,
    rights_verified: bool,
    evidence_verified: bool,
) -> TaskSourceProvenance:
    """Build a mutation-derived task from an operator record and independently run proofs."""
    return TaskSourceProvenance(
        source_family="mutation",
        language=language,
        rights_record_id=rights_record_id,
        rights_verified=rights_verified,
        evidence_verified=evidence_verified,
        authorship_record_id=authorship_record_id,
        mutation_operator=mutation.operator,
        mutation_version=mutation.operator_version,
        mutation_seed=mutation.seed,
        changed_span=(
            mutation.changed_path,
            mutation.changed_start_line,
            mutation.changed_end_line,
        ),
        behavior_change_proof=mutation.behavior_change_proof,
        reference_repair_proof=mutation.reference_repair_proof,
        build_proof=mutation.build_proof,
        clean_control_id=clean_control_id,
        equivalent_to_existing=False,
        duplicate_mutation=False,
        unintended_or_nonbuilding=False,
        assets=(
            SourceAsset(
                asset_id=f"{task_id}-visible-mutant",
                digest=digest_files(mutated_source),
                visibility="private",
                role="visible_source",
            ),
            SourceAsset(
                asset_id=f"{task_id}-reproducer",
                digest=digest_files(reproducer),
                visibility="private",
                role="reproducer",
            ),
            SourceAsset(
                asset_id=f"{task_id}-reference-repair",
                digest=digest_files(reference_fix),
                visibility="hidden",
                role="hidden_oracle",
            ),
            SourceAsset(
                asset_id=f"{task_id}-mutation-log",
                digest="sha256:" + hashlib.sha256(private_mutation_log).hexdigest(),
                visibility="hidden",
                role="injection_log",
            ),
        ),
        evidence_tier="synthetic_internal",
    )


def mutate_source(
    source: bytes,
    *,
    path: str,
    old: bytes,
    new: bytes,
    operator: str,
    operator_version: str,
    seed: int,
    behavior_change_proof: str,
    reference_repair_proof: str,
    build_proof: str,
    equivalent_to_existing: bool = False,
    duplicate_mutation: bool = False,
    unintended_or_nonbuilding: bool = False,
) -> tuple[bytes, MutationRecord]:
    """Apply one proven byte-level mutation, rejecting equivalent/duplicate/no-op mutants."""
    if not all(
        (operator, operator_version, behavior_change_proof, reference_repair_proof, build_proof)
    ):
        raise ValueError("mutation evidence and pinned operator identity are required")
    if equivalent_to_existing:
        raise ValueError("equivalent mutation rejected")
    if duplicate_mutation:
        raise ValueError("duplicate mutation rejected")
    if unintended_or_nonbuilding:
        raise ValueError("unintended or nonbuilding mutation rejected")
    if not old or source.count(old) != 1:
        raise ValueError("mutation anchor must occur exactly once")
    mutated = source.replace(old, new, 1)
    if mutated == source:
        raise ValueError("equivalent no-op mutation rejected")
    line = source[: source.index(old)].count(b"\n") + 1
    record = MutationRecord(
        operator=operator,
        operator_version=operator_version,
        seed=seed,
        source_digest="sha256:" + hashlib.sha256(source).hexdigest(),
        mutated_digest="sha256:" + hashlib.sha256(mutated).hexdigest(),
        changed_path=path,
        changed_start_line=line,
        changed_end_line=line + new.count(b"\n"),
        behavior_change_proof=behavior_change_proof,
        reference_repair_proof=reference_repair_proof,
        build_proof=build_proof,
    )
    return mutated, record


class FindingEnvelope(TrackAModel):
    kind: Literal["finding_envelope"] = "finding_envelope"
    findings: tuple[Finding, ...]
    patch: str | None = None
    associations: tuple[dict[str, Any], ...] = ()


class ParsedFindings(TrackAModel):
    kind: Literal["parsed_findings"] = "parsed_findings"
    valid: bool
    findings: tuple[Finding, ...]
    errors: tuple[str, ...]
    envelope_digest: str
    base_digest: str
    patch: str | None = None
    duplicate_of: tuple[tuple[str, str], ...] = ()


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _semantic_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in normalized).split())


def canonicalize_findings(
    findings: Sequence[Finding],
) -> tuple[tuple[Finding, ...], tuple[tuple[str, str], ...]]:
    """Mark exact semantic/location duplicates; retain every report in the audit view."""
    seen: dict[tuple[str, str, int, int, str], str] = {}
    duplicates: list[tuple[str, str]] = []
    accepted: list[Finding] = []
    for finding in findings:
        key = (
            finding.path,
            _semantic_text(finding.root_cause),
            finding.start_line,
            finding.end_line,
            finding.severity,
        )
        primary = seen.get(key)
        if primary is None:
            seen[key] = finding.local_id
            accepted.append(finding)
        else:
            duplicates.append((finding.local_id, primary))
    return tuple(accepted), tuple(duplicates)


def parse_findings(
    payload: bytes | str | Mapping[str, Any],
    *,
    base_files: Mapping[str, bytes],
    max_findings: int = 20,
    max_span_lines: int = 50,
) -> ParsedFindings:
    """Validate one complete findings envelope against immutable base bytes.

    Any schema/span failure discards all findings and records the failure. Patch extraction is
    retained separately so invalid detection cannot poison an independently valid repair.
    """
    base_digest = digest_files(base_files)
    envelope_digest = (
        "sha256:"
        + hashlib.sha256(
            payload
            if isinstance(payload, bytes)
            else payload.encode("utf-8")
            if isinstance(payload, str)
            else json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    errors: list[str] = []
    raw: Any
    if isinstance(payload, bytes | str):
        try:
            raw = json.loads(
                payload,
                object_pairs_hook=_reject_duplicate_json_keys,
            )
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError) as exc:
            return ParsedFindings(
                valid=False,
                findings=(),
                errors=(f"invalid_json:{type(exc).__name__}",),
                envelope_digest=envelope_digest,
                base_digest=base_digest,
            )
    else:
        raw = dict(payload)
    if (
        not isinstance(raw, dict)
        or set(raw) - {"findings", "patch", "associations"}
        or not isinstance(raw.get("findings"), list)
    ):
        return ParsedFindings(
            valid=False,
            findings=(),
            errors=("invalid_findings_envelope",),
            envelope_digest=envelope_digest,
            base_digest=base_digest,
            patch=raw.get("patch")
            if isinstance(raw, dict) and isinstance(raw.get("patch"), str)
            else None,
        )
    patch = raw.get("patch")
    if patch is not None and not isinstance(patch, str):
        errors.append("patch_not_string")
        patch = None
    if len(raw["findings"]) > max_findings:
        errors.append("findings_over_limit")
    findings: list[Finding] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw["findings"]):
        try:
            finding = Finding.model_validate(item, strict=True)
        except ValidationError:
            errors.append(f"finding_{index}_invalid_schema")
            continue
        if finding.local_id in seen_ids:
            errors.append(f"finding_{index}_duplicate_id")
        seen_ids.add(finding.local_id)
        path = finding.path.replace("\\", "/")
        if (
            path.startswith("/")
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or path != finding.path
        ):
            errors.append(f"finding_{index}_invalid_path")
            continue
        data = base_files.get(path)
        if data is None:
            errors.append(f"finding_{index}_path_not_in_base")
            continue
        try:
            lines = data.decode("utf-8").splitlines()
        except UnicodeDecodeError:
            errors.append(f"finding_{index}_base_file_not_utf8")
            continue
        if finding.end_line < finding.start_line or finding.end_line > len(lines):
            errors.append(f"finding_{index}_span_outside_base")
            continue
        if finding.end_line - finding.start_line + 1 > max_span_lines:
            errors.append(f"finding_{index}_span_too_wide")
            continue
        findings.append(finding.model_copy(update={"path": path}))
    if errors:
        return ParsedFindings(
            valid=False,
            findings=(),
            errors=tuple(sorted(set(errors))),
            envelope_digest=envelope_digest,
            base_digest=base_digest,
            patch=patch,
        )
    _, duplicate_of = canonicalize_findings(findings)
    return ParsedFindings(
        valid=True,
        findings=tuple(findings),
        errors=(),
        envelope_digest=envelope_digest,
        base_digest=base_digest,
        patch=patch,
        duplicate_of=duplicate_of,
    )


class OracleBug(TrackAModel):
    kind: Literal["oracle_bug"] = "oracle_bug"
    bug_id: str
    path: str
    causal_start_line: int = Field(ge=1)
    causal_end_line: int = Field(ge=1)
    function_start_line: int | None = Field(default=None, ge=1)
    function_end_line: int | None = Field(default=None, ge=1)
    taxonomy: str
    trigger: str
    incorrect_behavior: str
    mechanism: str
    consequence: str
    accepted_severities: tuple[Severity, ...]

    @model_validator(mode="after")
    def valid_regions(self) -> OracleBug:
        if self.causal_end_line < self.causal_start_line:
            raise ValueError("causal span is inverted")
        if self.function_start_line is not None and self.function_end_line is not None:
            if self.function_end_line < self.function_start_line:
                raise ValueError("function span is inverted")
            if not (
                self.function_start_line
                <= self.causal_start_line
                <= self.causal_end_line
                <= self.function_end_line
            ):
                raise ValueError("causal span must be inside its function span")
        if not self.accepted_severities or len(set(self.accepted_severities)) != len(
            self.accepted_severities
        ):
            raise ValueError("accepted severities must be unique and nonempty")
        return self


class MatchEdge(TrackAModel):
    kind: Literal["match_edge"] = "match_edge"
    finding_id: str
    bug_id: str
    decision: Literal["accepted", "rejected", "unverified"]
    causal_equivalent: bool
    independent_evidence_ref: str | None = None
    reviewer_id: str | None = None
    review_rationale: str | None = None
    # Each reviewer grade is a fixed-point encoding of 0, 0.5 or 1.
    explanation_facts: tuple[int, int, int, int] | None = None
    evidence_id: str

    @model_validator(mode="after")
    def reviewed_acceptance(self) -> MatchEdge:
        if self.decision == "accepted" and (
            not self.causal_equivalent
            or not self.independent_evidence_ref
            or not self.reviewer_id
            or not self.review_rationale
            or not self.evidence_id
            or self.explanation_facts is None
        ):
            raise ValueError(
                "accepted edge requires causal equivalence, independent evidence, "
                "and reviewer evidence"
            )
        if self.explanation_facts is not None and any(
            v not in (0, 5000, 10000) for v in self.explanation_facts
        ):
            raise ValueError("explanation rubric facts must be 0, 5000 or 10000 basis points")
        return self


class MatchProposal(TrackAModel):
    kind: Literal["match_proposal"] = "match_proposal"
    finding_id: str
    bug_id: str
    shared_scope: bool
    shared_taxonomy_terms: tuple[str, ...]
    proposal_evidence_id: str
    decision: Literal["unverified"] = "unverified"


def propose_match_edges(
    findings: Sequence[Finding], bugs: Sequence[OracleBug]
) -> tuple[MatchProposal, ...]:
    """Create review-only candidates from shared location and semantic taxonomy terms.

    These are never accepted automatically. A same-file coincidence with no span or semantic
    overlap does not produce a proposal, and every accepted edge still needs independent evidence
    and reviewer approval through :class:`MatchEdge`.
    """
    proposals: list[MatchProposal] = []
    for finding in findings:
        finding_terms = set(_semantic_text(finding.root_cause).split())
        for bug in bugs:
            if finding.path != bug.path:
                continue
            scope_overlap = not (
                finding.end_line < bug.causal_start_line or finding.start_line > bug.causal_end_line
            )
            taxonomy_terms = set(_semantic_text(bug.taxonomy).split())
            shared = tuple(sorted(finding_terms & taxonomy_terms))
            if not scope_overlap and not shared:
                continue
            evidence = canonical_digest(
                {
                    "finding_id": finding.local_id,
                    "bug_id": bug.bug_id,
                    "scope_overlap": scope_overlap,
                    "shared_taxonomy_terms": list(shared),
                }
            )
            proposals.append(
                MatchProposal(
                    finding_id=finding.local_id,
                    bug_id=bug.bug_id,
                    shared_scope=scope_overlap,
                    shared_taxonomy_terms=shared,
                    proposal_evidence_id=evidence,
                )
            )
    return tuple(sorted(proposals, key=lambda item: (item.finding_id, item.bug_id)))


class FindingDisposition(TrackAModel):
    kind: Literal["finding_disposition"] = "finding_disposition"
    finding_id: str
    disposition: Literal["false_positive", "unverified", "novel_accepted", "duplicate"]
    reviewer_id: str | None = None
    evidence_id: str | None = None
    rationale: str | None = None
    duplicate_of: str | None = None

    @model_validator(mode="after")
    def require_review_evidence(self) -> FindingDisposition:
        if self.disposition in {"false_positive", "novel_accepted", "duplicate"} and not all(
            (self.reviewer_id, self.evidence_id, self.rationale)
        ):
            raise ValueError("adjudicated dispositions require reviewer, evidence and rationale")
        if self.disposition == "duplicate" and not self.duplicate_of:
            raise ValueError("reviewed semantic duplicate requires its canonical finding ID")
        return self


class DetectionResult(TrackAModel):
    kind: Literal["detection_result"] = "detection_result"
    status: Literal["complete", "pending_review", "invalid_submission"]
    true_positive: int = Field(ge=0)
    false_positive: int = Field(ge=0)
    false_negative: int = Field(ge=0)
    unresolved_findings: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    precision: Decimal | None
    recall: Decimal | None
    f1: Decimal | None
    localization: Decimal | None
    explanation: Decimal | None
    severity: Decimal | None
    matched_pairs: tuple[tuple[str, str], ...]
    edge_digests: tuple[str, ...]
    reasons: tuple[str, ...]


def _localization(finding: Finding, bug: OracleBug) -> int:
    if finding.path != bug.path:
        return 0
    exact = finding.start_line == bug.causal_start_line and finding.end_line == bug.causal_end_line
    if exact:
        return 100
    if (
        bug.function_start_line is not None
        and bug.function_end_line is not None
        and (
            bug.function_start_line
            <= finding.start_line
            <= finding.end_line
            <= bug.function_end_line
        )
    ):
        return 70
    return 30


def _severity_score(finding: Finding, bug: OracleBug) -> int:
    distance = min(
        abs(_SEVERITY_RANK[finding.severity] - _SEVERITY_RANK[value])
        for value in bug.accepted_severities
    )
    return 100 if distance == 0 else 50 if distance == 1 else 0


def _maximum_matching(
    findings: Sequence[Finding], bugs: Sequence[OracleBug], edges: Sequence[MatchEdge]
) -> tuple[tuple[str, str], ...]:
    """Maximum-weight bipartite match with stable ID order for exact ties."""
    finding_ids = sorted(f.local_id for f in findings)
    bug_ids = sorted(b.bug_id for b in bugs)
    if not finding_ids or not bug_ids:
        return ()
    by_finding = {f.local_id: f for f in findings}
    by_bug = {b.bug_id: b for b in bugs}
    accepted = {
        (e.finding_id, e.bug_id) for e in edges if e.decision == "accepted" and e.causal_equivalent
    }
    row_count = len(finding_ids)
    # One dummy column per finding allows every report to remain unmatched. Location dominates
    # cardinality, with cardinality as the secondary objective. Sorted row/column order and
    # strict comparisons make stable IDs the deterministic final tie-break.
    column_ids = bug_ids + [f"~unmatched-{index:08d}" for index in range(row_count)]
    score_base = row_count + 1
    maximum_weight = 100 * score_base + 1
    costs: list[list[int]] = []
    for finding_id in finding_ids:
        row: list[int] = []
        for bug_id in bug_ids:
            if (finding_id, bug_id) not in accepted:
                weight = 0
            else:
                weight = _localization(by_finding[finding_id], by_bug[bug_id]) * score_base + 1
            row.append(maximum_weight - weight)
        row.extend([maximum_weight] * row_count)
        costs.append(row)

    # Rectangular Hungarian algorithm for n rows and m>=n columns.
    row_potential = [0] * (row_count + 1)
    column_potential = [0] * (len(column_ids) + 1)
    assigned_row = [0] * (len(column_ids) + 1)
    predecessor = [0] * (len(column_ids) + 1)
    for row_index in range(1, row_count + 1):
        assigned_row[0] = row_index
        column_index = 0
        minimum = [10**12] * (len(column_ids) + 1)
        used = [False] * (len(column_ids) + 1)
        while True:
            used[column_index] = True
            active_row = assigned_row[column_index]
            delta = 10**12
            next_column = 0
            for candidate_column in range(1, len(column_ids) + 1):
                if used[candidate_column]:
                    continue
                reduced = (
                    costs[active_row - 1][candidate_column - 1]
                    - row_potential[active_row]
                    - column_potential[candidate_column]
                )
                if reduced < minimum[candidate_column]:
                    minimum[candidate_column] = reduced
                    predecessor[candidate_column] = column_index
                if minimum[candidate_column] < delta:
                    delta = minimum[candidate_column]
                    next_column = candidate_column
            for candidate_column in range(len(column_ids) + 1):
                if used[candidate_column]:
                    row_potential[assigned_row[candidate_column]] += int(delta)
                    column_potential[candidate_column] -= int(delta)
                elif candidate_column:
                    minimum[candidate_column] -= delta
            column_index = next_column
            if assigned_row[column_index] == 0:
                break
        while True:
            previous_column = predecessor[column_index]
            assigned_row[column_index] = assigned_row[previous_column]
            column_index = previous_column
            if column_index == 0:
                break
    chosen = {
        finding_ids[assigned_row[column_index] - 1]: column_ids[column_index - 1]
        for column_index in range(1, len(column_ids) + 1)
        if assigned_row[column_index]
    }
    return tuple(
        sorted(
            (finding_id, bug_id)
            for finding_id, bug_id in chosen.items()
            if bug_id in bug_ids and (finding_id, bug_id) in accepted
        )
    )


def score_detection(
    *,
    findings: Sequence[Finding],
    bugs: Sequence[OracleBug],
    edges: Sequence[MatchEdge],
    dispositions: Sequence[FindingDisposition] = (),
    schema_valid: bool = True,
    duplicate_of: Sequence[tuple[str, str]] = (),
) -> DetectionResult:
    if not schema_valid:
        return DetectionResult(
            status="invalid_submission",
            true_positive=0,
            false_positive=0,
            false_negative=len(bugs),
            unresolved_findings=0,
            duplicate_count=len(duplicate_of),
            precision=None,
            recall=Decimal(0) if bugs else None,
            f1=Decimal(0) if bugs else None,
            localization=Decimal(0) if bugs else None,
            explanation=Decimal(0) if bugs else None,
            severity=Decimal(0) if bugs else None,
            matched_pairs=(),
            edge_digests=tuple(e.content_digest() for e in edges),
            reasons=("invalid_findings_schema",),
        )
    duplicates = {duplicate for duplicate, _ in duplicate_of}
    duplicates.update(d.finding_id for d in dispositions if d.disposition == "duplicate")
    all_finding_ids = {finding.local_id for finding in findings}
    if not duplicates <= all_finding_ids:
        raise ValueError("duplicate mapping references an unknown finding")
    active_findings = [f for f in findings if f.local_id not in duplicates]
    finding_ids = {f.local_id for f in active_findings}
    bug_ids = {b.bug_id for b in bugs}
    if len(finding_ids) != len(active_findings) or len(bug_ids) != len(bugs):
        raise ValueError("finding and bug identifiers must be unique")
    for edge in edges:
        if edge.finding_id not in finding_ids or edge.bug_id not in bug_ids:
            raise ValueError(
                "match edge references a finding or bug outside the immutable task sample"
            )
    accepted_edges = [e for e in edges if e.decision == "accepted" and e.causal_equivalent]
    pairs = _maximum_matching(active_findings, bugs, accepted_edges)
    matched_findings = {f for f, _ in pairs}
    matched_bugs = {b for _, b in pairs}
    dispositions_by_id = {d.finding_id: d for d in dispositions}
    if len(dispositions_by_id) != len(dispositions):
        raise ValueError("duplicate finding disposition")
    unresolved = sum(
        finding.local_id not in matched_findings
        and (
            finding.local_id not in dispositions_by_id
            or dispositions_by_id[finding.local_id].disposition in {"unverified", "novel_accepted"}
        )
        for finding in active_findings
    )
    if not set(dispositions_by_id) <= all_finding_ids:
        raise ValueError("finding disposition references an unknown finding")
    if any(
        dispositions_by_id[fid].disposition == "false_positive" and fid in matched_findings
        for fid in dispositions_by_id
    ):
        raise ValueError("a finding cannot be both a true positive and a false positive")
    false_positive = sum(
        d.disposition == "false_positive" and fid not in matched_findings
        for fid, d in dispositions_by_id.items()
    )
    tp = len(pairs)
    fn = len(bug_ids - matched_bugs)
    pending = unresolved > 0
    precision = Decimal(tp) / Decimal(tp + false_positive) if tp + false_positive else None
    recall = Decimal(tp) / Decimal(tp + fn) if tp + fn else None
    f1 = (
        Decimal(2 * tp) / Decimal(2 * tp + false_positive + fn)
        if 2 * tp + false_positive + fn
        else None
    )
    matched_edge = {(edge.finding_id, edge.bug_id): edge for edge in accepted_edges}
    finding_by_id = {f.local_id: f for f in active_findings}
    bug_by_id = {b.bug_id: b for b in bugs}
    loc_total = severity_total = 0
    explain_total = Decimal(0)
    for fid, bid in pairs:
        finding, bug = finding_by_id[fid], bug_by_id[bid]
        loc_total += _localization(finding, bug)
        edge = matched_edge[(fid, bid)]
        assert edge.explanation_facts is not None
        explain_total += Decimal(sum(edge.explanation_facts)) / 400
        severity_total += _severity_score(finding, bug)
    denominator = len(bugs)
    return DetectionResult(
        status="pending_review" if pending else "complete",
        true_positive=tp,
        false_positive=false_positive,
        false_negative=fn,
        unresolved_findings=unresolved,
        duplicate_count=len(duplicates),
        precision=None if pending else precision,
        recall=recall,
        f1=None if pending else f1,
        localization=Decimal(loc_total) / denominator if denominator else None,
        explanation=explain_total / denominator if denominator else None,
        severity=Decimal(severity_total) / denominator if denominator else None,
        matched_pairs=pairs,
        edge_digests=tuple(e.content_digest() for e in edges),
        reasons=("unresolved_findings_block_strict_precision_and_f1",) if pending else (),
    )


class ReviewEvent(TrackAModel):
    kind: Literal["review_event"] = "review_event"
    sequence: int = Field(ge=1)
    event_id: str
    actor_id: str
    action: Literal["edge_review", "finding_disposition", "oracle_revision"]
    subject_digest: str
    payload_digest: str
    previous_event_digest: str | None
    created_at: str
    event_digest: str

    @classmethod
    def create(
        cls,
        *,
        sequence: int,
        actor_id: str,
        action: Literal["edge_review", "finding_disposition", "oracle_revision"],
        subject_digest: str,
        payload_digest: str,
        previous_event_digest: str | None,
        created_at: str,
    ) -> ReviewEvent:
        event_id = new_entity_id()
        body = {
            "kind": "review_event",
            "schema_version": 1,
            "sequence": sequence,
            "event_id": event_id,
            "actor_id": actor_id,
            "action": action,
            "subject_digest": subject_digest,
            "payload_digest": payload_digest,
            "previous_event_digest": previous_event_digest,
            "created_at": created_at,
        }
        digest = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        )
        return cls(
            kind="review_event",
            schema_version=1,
            sequence=sequence,
            event_id=event_id,
            actor_id=actor_id,
            action=action,
            subject_digest=subject_digest,
            payload_digest=payload_digest,
            previous_event_digest=previous_event_digest,
            created_at=created_at,
            event_digest=digest,
        )


@dataclass(frozen=True, slots=True)
class GroundTruthRevision:
    version: int
    parent_digest: str | None
    bugs: tuple[OracleBug, ...]
    digest: str

    @classmethod
    def create(
        cls, version: int, parent: GroundTruthRevision | None, bugs: Sequence[OracleBug]
    ) -> GroundTruthRevision:
        if version != (parent.version + 1 if parent else 1):
            raise ValueError("oracle versions must be consecutive and immutable")
        if len({bug.bug_id for bug in bugs}) != len(bugs):
            raise ValueError("bug IDs must be unique in a ground-truth revision")
        parent_digest = parent.digest if parent else None
        digest = canonical_digest(
            canonical_envelope(
                "ground_truth_revision",
                {
                    "version": version,
                    "parent_digest": parent_digest,
                    "bugs": [bug.model_dump(mode="json") for bug in bugs],
                },
            )
        )
        return cls(version, parent_digest, tuple(bugs), digest)


@dataclass(frozen=True, slots=True)
class RematchPlan:
    old_oracle_digest: str
    new_oracle_digest: str
    evaluation_ids: tuple[str, ...]
    required_rematches: tuple[str, ...]


class AdjudicatedFindingSample(TrackAModel):
    kind: Literal["adjudicated_finding_sample"] = "adjudicated_finding_sample"
    evaluation_id: str
    findings: tuple[Finding, ...]
    edges: tuple[MatchEdge, ...]
    dispositions: tuple[FindingDisposition, ...]
    duplicate_of: tuple[tuple[str, str], ...] = ()
    schema_valid: bool = True


class CohortRematch(TrackAModel):
    kind: Literal["cohort_rematch"] = "cohort_rematch"
    oracle_digest: str
    evaluation_ids: tuple[str, ...]
    result_digests: tuple[tuple[str, str], ...]
    results: tuple[tuple[str, DetectionResult], ...]


def rematch_cohort(
    revision: GroundTruthRevision,
    reports: Sequence[AdjudicatedFindingSample],
    *,
    expected_evaluation_ids: Sequence[str],
) -> CohortRematch:
    """Re-score every declared sample against one new immutable oracle or fail closed."""
    expected = tuple(sorted(set(expected_evaluation_ids)))
    by_id = {report.evaluation_id: report for report in reports}
    if len(by_id) != len(reports) or set(by_id) != set(expected):
        raise ValueError("cohort rematch must include each affected evaluation exactly once")
    results = tuple(
        (
            evaluation_id,
            score_detection(
                findings=by_id[evaluation_id].findings,
                bugs=revision.bugs,
                edges=by_id[evaluation_id].edges,
                dispositions=by_id[evaluation_id].dispositions,
                schema_valid=by_id[evaluation_id].schema_valid,
                duplicate_of=by_id[evaluation_id].duplicate_of,
            ),
        )
        for evaluation_id in expected
    )
    return CohortRematch(
        oracle_digest=revision.digest,
        evaluation_ids=expected,
        result_digests=tuple(
            (evaluation_id, result.content_digest()) for evaluation_id, result in results
        ),
        results=results,
    )


def revise_ground_truth(
    previous: GroundTruthRevision,
    *,
    new_bugs: Sequence[OracleBug],
    affected_evaluation_ids: Sequence[str],
) -> tuple[GroundTruthRevision, RematchPlan]:
    revision = GroundTruthRevision.create(previous.version + 1, previous, new_bugs)
    ids = tuple(sorted(set(affected_evaluation_ids)))
    return revision, RematchPlan(previous.digest, revision.digest, ids, ids)


@dataclass(frozen=True, slots=True)
class ReviewLedger:
    events: tuple[ReviewEvent, ...] = ()

    def append(
        self,
        *,
        actor_id: str,
        action: Literal["edge_review", "finding_disposition", "oracle_revision"],
        subject_digest: str,
        payload_digest: str,
        created_at: str,
    ) -> ReviewLedger:
        previous = self.events[-1].event_digest if self.events else None
        event = ReviewEvent.create(
            sequence=len(self.events) + 1,
            actor_id=actor_id,
            action=action,
            subject_digest=subject_digest,
            payload_digest=payload_digest,
            previous_event_digest=previous,
            created_at=created_at,
        )
        return ReviewLedger(self.events + (event,))

    def verify(self) -> bool:
        previous = None
        for index, event in enumerate(self.events, 1):
            if event.sequence != index or event.previous_event_digest != previous:
                return False
            # Recompute against the stored ID: event IDs are intentionally not regenerated here.
            body = event.model_dump(exclude={"event_digest"})
            actual = (
                "sha256:"
                + hashlib.sha256(
                    json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
            )
            if actual != event.event_digest:
                return False
            previous = event.event_digest
        return True


class RepairResult(TrackAModel):
    kind: Literal["repair_result"] = "repair_result"
    status: Literal["not_required", "evaluated", "missing_patch", "invalid_patch", "incomplete"]
    repair_required: bool
    composite_score_bp: int | None = Field(ge=0, le=10000)
    evaluation_id: str | None = None
    evaluation_digest: str | None = None
    gate: Literal["pass", "fail", "incomplete"] | None = None
    patch_digest: str | None = None
    patched_source_digest: str | None = None
    reasons: tuple[str, ...]


def apply_combined_patch(
    base_files: Mapping[str, bytes],
    patch: str,
    *,
    allowed_paths: Sequence[str],
    protected_paths: Sequence[str] = (),
    max_file_bytes: int = 2_000_000,
) -> dict[str, bytes]:
    """Use the existing guest patch parser/validator against a fresh temporary base."""
    with tempfile.TemporaryDirectory(prefix="pcb-track-a-") as tmp:
        root = Path(tmp)
        for rel, data in base_files.items():
            path = Path(rel)
            if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
                raise ValueError(f"unsafe base path: {rel}")
            target = root.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        try:
            plan = plan_patch(  # type: ignore[no-untyped-call]  # Plain-stdlib shared guest helper.
                str(root), patch, list(protected_paths), max_file_bytes
            )
        except ToolFailure as exc:
            raise ValueError(f"patch_rejected:{exc.code}") from exc
        result = dict(base_files)
        for item in plan:
            rel = item["path"]
            if not any(
                rel == allowed or rel.startswith(allowed.rstrip("/") + "/")
                for allowed in allowed_paths
            ):
                raise ValueError(f"patch_path_outside_allowlist:{rel}")
            if item["action"] == "delete":
                result.pop(rel, None)
            else:
                result[rel] = item["updated"].encode("utf-8")
        return result


async def evaluate_repair(
    *,
    evaluator: Evaluator,
    view: FrozenTask,
    candidate: Candidate,
    base_files: Mapping[str, bytes],
    patch: str | None,
    overlay: Mapping[str, bytes],
    config: Mapping[str, bytes],
    allowed_paths: Sequence[str],
    protected_paths: Sequence[str],
    repair_required: bool,
    code_score_bp: int | None,
    label: str = "track-a-repair",
) -> tuple[RepairResult, Evaluation | None]:
    """Apply the final combined patch to a fresh base and independently run the evaluator.

    ``code_score_bp`` is the composite from the existing scoring service. A passing functional
    gate with no score evidence stays incomplete; a failed or absent positive-task patch earns 0.
    """
    if not repair_required:
        return RepairResult(
            status="not_required",
            repair_required=False,
            composite_score_bp=None,
            reasons=("clean_control_has_no_repair_score",),
        ), None
    if patch is None:
        return RepairResult(
            status="missing_patch",
            repair_required=True,
            composite_score_bp=0,
            reasons=("positive_task_missing_patch",),
        ), None
    try:
        repaired = apply_combined_patch(
            base_files, patch, allowed_paths=allowed_paths, protected_paths=protected_paths
        )
    except (ValueError, OSError) as exc:
        return RepairResult(
            status="invalid_patch",
            repair_required=True,
            composite_score_bp=0,
            patch_digest="sha256:" + hashlib.sha256(patch.encode()).hexdigest(),
            reasons=(str(exc),),
        ), None
    repair_candidate = Candidate.model_validate(
        {
            **candidate.model_dump(),
            "candidate_id": new_entity_id(),
            "submission_kind": "source_bundle",
            "payload_digest": digest_files(repaired),
        }
    )
    evaluation = await evaluator.evaluate(
        view=view,
        candidate=repair_candidate,
        candidate_files=repaired,
        overlay=overlay,
        config=config,
        allowed_paths=tuple(allowed_paths),
        baseline_files=base_files,
        label=label,
    )
    evidence = evaluation.evidence
    if evidence.gate == "fail":
        score = 0
    elif evidence.gate == "incomplete":
        score = None
    elif code_score_bp is None:
        score = None
    else:
        score = code_score_bp
    return RepairResult(
        status="evaluated" if score is not None else "incomplete",
        repair_required=True,
        composite_score_bp=score,
        evaluation_id=evidence.evaluation_id,
        evaluation_digest=canonical_document_digest(evidence),
        gate=evidence.gate,
        patch_digest="sha256:" + hashlib.sha256(patch.encode()).hexdigest(),
        patched_source_digest=digest_files(repaired),
        reasons=(
            ()
            if score is not None
            else ("independent_evaluation_incomplete",)
            if evidence.gate == "incomplete"
            else ("code_score_evidence_missing",)
        ),
    ), evaluation


class TrackATaskCell(TrackAModel):
    kind: Literal["track_a_task_cell"] = "track_a_task_cell"
    task_id: str
    task_version: int = Field(gt=0)
    language: Literal["python", "rust"]
    source_family: Literal[
        "historical", "disclosed_security", "authored_history", "injected", "mutation"
    ]
    cluster_id: str
    stratum_id: str
    repair_required: bool
    required_bug_count: int = Field(ge=0)

    @model_validator(mode="after")
    def positive_task_repair(self) -> TrackATaskCell:
        if self.repair_required != (self.required_bug_count > 0):
            raise ValueError(
                "repair_required must reflect whether the frozen oracle has positive bugs"
            )
        return self


class TrackASample(TrackAModel):
    kind: Literal["track_a_sample"] = "track_a_sample"
    entry_id: str
    task_id: str
    task_version: int = Field(gt=0)
    sample_index: int = Field(ge=0)
    attempt_outcome: Literal["completed", "model_failed"] = "completed"
    result: DetectionResult
    repair: RepairResult
    scorecard_digest: str
    evidence_tier: Literal["fixture", "authored_internal", "verified_external", "production"]

    @model_validator(mode="after")
    def failed_attempt_is_zero_credit(self) -> TrackASample:
        if (self.attempt_outcome == "model_failed") != (self.result.status == "invalid_submission"):
            raise ValueError("model failures must use the invalid-submission outcome")
        return self


class TrackACohort(TrackAModel):
    kind: Literal["track_a_cohort"] = "track_a_cohort"
    canonical_excluded_fields: ClassVar[frozenset[str]] = frozenset({"cohort_digest"})
    tasks: tuple[TrackATaskCell, ...]
    required_languages: tuple[str, ...]
    planned_samples: int = Field(ge=1)
    source_weights_bps: tuple[tuple[str, str, int], ...]
    repair_source_weights_bps: tuple[tuple[str, str, int], ...]
    language_weights_bps: tuple[tuple[str, int], ...]
    cohort_digest: str

    @model_validator(mode="after")
    def fixed_balanced_cohort(self) -> TrackACohort:
        if not self.tasks or len({t.task_id for t in self.tasks}) != len(self.tasks):
            raise ValueError("cohort tasks must be nonempty and unique")
        if not self.required_languages or len(set(self.required_languages)) != len(
            self.required_languages
        ):
            raise ValueError("required languages must be unique")
        language_weights = dict(self.language_weights_bps)
        if (
            len(language_weights) != len(self.language_weights_bps)
            or set(language_weights) != set(self.required_languages)
            or sum(language_weights.values()) != 10000
        ):
            raise ValueError("language weights must sum to 10000 bps")
        if len(self.required_languages) == 2 and set(language_weights.values()) != {5000}:
            raise ValueError("Python/Rust headline language weights must be equal")
        source_pairs = {
            (language, source): weight for language, source, weight in self.source_weights_bps
        }
        repair_pairs = {
            (language, source): weight
            for language, source, weight in self.repair_source_weights_bps
        }
        if len(source_pairs) != len(self.source_weights_bps):
            raise ValueError("duplicate detection source weight")
        if len(repair_pairs) != len(self.repair_source_weights_bps):
            raise ValueError("duplicate repair source weight")
        for language in self.required_languages:
            weights = [w for (lang, _), w in source_pairs.items() if lang == language]
            if not weights or sum(weights) != 10000:
                raise ValueError(
                    "source weights must sum to 10000 bps within every required language"
                )
            repair_weights = [w for (lang, _), w in repair_pairs.items() if lang == language]
            if not repair_weights or sum(repair_weights) != 10000:
                raise ValueError("repair source weights must sum to 10000 bps per language")
        if any(task.language not in self.required_languages for task in self.tasks):
            raise ValueError("task language is outside frozen cohort")
        for language, source in source_pairs:
            if not any(t.language == language and t.stratum_id == source for t in self.tasks):
                raise ValueError("detection source weight names a missing cohort stratum")
        for language, source in repair_pairs:
            if not any(
                t.language == language and t.stratum_id == source and t.repair_required
                for t in self.tasks
            ):
                raise ValueError("repair source weight requires a positive-bug task stratum")
        if canonical_document_digest(self) != self.cohort_digest:
            raise ValueError("cohort digest does not bind membership and weights")
        return self


class TrackAAggregate(TrackAModel):
    kind: Literal["track_a_aggregate"] = "track_a_aggregate"
    cohort_digest: str
    entry_id: str
    expected_attempts: int
    completed_attempts: int
    missing_attempts: int
    model_failed_attempts: int
    unresolved_findings: int
    strict_status: Literal["complete", "pending_review", "partial", "unavailable"]
    precision: Decimal | None
    recall: Decimal | None
    f1: Decimal | None
    localization: Decimal | None
    explanation: Decimal | None
    severity: Decimal | None
    repair_score_bp: Decimal | None
    a_score: Decimal | None
    language_scores: tuple[tuple[str, Decimal | None], ...]
    source_language_scores: tuple[tuple[str, str, Decimal | None], ...]
    source_detection: tuple[tuple[str, str, Decimal | None, Decimal | None, Decimal | None], ...]
    duplicate_count: int
    repair_failure_count: int
    coverage: Decimal
    label: Literal["exploratory_internal", "unavailable"]
    reasons: tuple[str, ...]


def _bug_weighted_task_mean(
    task_stats: Mapping[str, Mapping[str, Decimal | int | None]],
    tasks: Sequence[TrackATaskCell],
    metric_name: str,
) -> Decimal:
    bug_count = sum(task.required_bug_count for task in tasks)
    if not bug_count:
        return Decimal(0)
    numerator = sum(
        (
            Decimal(task_stats[task.task_id][metric_name] or 0) * task.required_bug_count
            for task in tasks
        ),
        Decimal(0),
    )
    return numerator / bug_count


def aggregate_track_a(
    cohort: TrackACohort, samples: Sequence[TrackASample], entry_id: str
) -> TrackAAggregate:
    tasks = {task.task_id: task for task in cohort.tasks}
    selected_samples = tuple(sample for sample in samples if sample.entry_id == entry_id)
    rows: dict[str, list[TrackASample]] = {}
    seen: set[tuple[str, int]] = set()
    for sample in selected_samples:
        if sample.task_id not in tasks or sample.task_version != tasks[sample.task_id].task_version:
            raise ValueError("sample is outside immutable Track A cohort")
        if sample.attempt_outcome == "model_failed":
            task = tasks[sample.task_id]
            if (
                sample.result.true_positive != 0
                or sample.result.false_positive != 0
                or sample.result.false_negative != task.required_bug_count
                or sample.result.localization not in (None, Decimal(0))
                or sample.result.explanation not in (None, Decimal(0))
                or sample.result.severity not in (None, Decimal(0))
            ):
                raise ValueError("model-failed samples must receive zero detection credit")
            if task.repair_required:
                if (
                    not sample.repair.repair_required
                    or sample.repair.composite_score_bp != 0
                    or sample.repair.status not in {"missing_patch", "invalid_patch"}
                ):
                    raise ValueError("model-failed positive tasks must receive repair zero")
            elif sample.repair.status != "not_required":
                raise ValueError("model-failed clean controls have no repair score")
        key = (sample.task_id, sample.sample_index)
        if key in seen or sample.sample_index >= cohort.planned_samples:
            raise ValueError("duplicate or out-of-range planned Track A sample")
        seen.add(key)
        rows.setdefault(sample.task_id, []).append(sample)
    expected = len(cohort.tasks) * cohort.planned_samples
    completed = sum(len(rows.get(task.task_id, [])) for task in cohort.tasks)
    all_complete = all(
        len(rows.get(task.task_id, [])) == cohort.planned_samples for task in cohort.tasks
    )
    pending = any(sample.result.status == "pending_review" for sample in selected_samples)
    model_failed = sum(sample.attempt_outcome == "model_failed" for sample in selected_samples)
    invalid_schema = any(
        "invalid_findings_schema" in sample.result.reasons for sample in selected_samples
    )
    reasons: list[str] = []
    if not all_complete:
        reasons.append("incomplete_planned_sample_coverage")
    if pending:
        reasons.append("unresolved_findings_block_strict_summary")
    if model_failed:
        reasons.append("model_failed_attempts_included_with_zero_credit")
    if invalid_schema:
        reasons.append("invalid_finding_schema_in_cohort")
    if any(s.evidence_tier in {"fixture", "authored_internal"} for s in selected_samples):
        reasons.append("internal_synthetic_or_authored_evidence_only")
    # First average planned samples within each task. Then apply the independently frozen
    # detection-source and positive-task repair-source weights within each language.
    task_stats: dict[str, dict[str, Decimal | int | None]] = {}
    for task in cohort.tasks:
        task_rows = rows.get(task.task_id, [])
        if not task_rows:
            task_stats[task.task_id] = {
                "tp": Decimal(0),
                "fp": Decimal(0),
                "fn": Decimal(0),
                "l": None,
                "x": None,
                "v": None,
                "repair": None,
            }
            continue
        n = Decimal(len(task_rows))
        task_stats[task.task_id] = {
            "tp": sum((Decimal(s.result.true_positive) for s in task_rows), Decimal(0)) / n,
            "fp": sum((Decimal(s.result.false_positive) for s in task_rows), Decimal(0)) / n,
            "fn": sum((Decimal(s.result.false_negative) for s in task_rows), Decimal(0)) / n,
            "l": sum((s.result.localization or Decimal(0) for s in task_rows), Decimal(0)) / n,
            "x": sum((s.result.explanation or Decimal(0) for s in task_rows), Decimal(0)) / n,
            "v": sum((s.result.severity or Decimal(0) for s in task_rows), Decimal(0)) / n,
            "repair": None
            if not task.repair_required
            else sum((Decimal(s.repair.composite_score_bp or 0) for s in task_rows), Decimal(0))
            / n,
        }
    source_language_scores: list[tuple[str, str, Decimal | None]] = []
    source_detection: list[tuple[str, str, Decimal | None, Decimal | None, Decimal | None]] = []
    language_scores: list[tuple[str, Decimal | None]] = []
    overall_counts = {name: Decimal(0) for name in ("tp", "fp", "fn")}
    repair_language: dict[str, Decimal | None] = {}
    localization_language: dict[str, Decimal] = {}
    explanation_language: dict[str, Decimal] = {}
    severity_language: dict[str, Decimal] = {}
    detection_weights = {
        (language, source): weight for language, source, weight in cohort.source_weights_bps
    }
    repair_weights = {
        (language, source): weight for language, source, weight in cohort.repair_source_weights_bps
    }
    for language in cohort.required_languages:
        language_counts = {name: Decimal(0) for name in ("tp", "fp", "fn")}
        repair_total = Decimal(0)
        language_complete = True
        sources = sorted(source for lang, source in detection_weights if lang == language)
        for source in sources:
            task_group = [
                t for t in cohort.tasks if t.language == language and t.stratum_id == source
            ]
            if not task_group or any(
                len(rows.get(task.task_id, [])) != cohort.planned_samples for task in task_group
            ):
                language_complete = False
                source_language_scores.append((language, source, None))
                source_detection.append((language, source, None, None, None))
                continue
            counts = {
                name: sum(
                    (Decimal(task_stats[task.task_id][name] or 0) for task in task_group),
                    Decimal(0),
                )
                for name in language_counts
            }
            weight = Decimal(detection_weights[(language, source)]) / 10000
            for name in language_counts:
                language_counts[name] += counts[name] * weight
        repair_sources = sorted(source for lang, source in repair_weights if lang == language)
        for source in repair_sources:
            repair_tasks = [
                task
                for task in cohort.tasks
                if task.language == language and task.stratum_id == source and task.repair_required
            ]
            if not repair_tasks or any(
                len(rows.get(task.task_id, [])) != cohort.planned_samples for task in repair_tasks
            ):
                language_complete = False
                continue
            repair_source = sum(
                (Decimal(task_stats[task.task_id]["repair"] or 0) for task in repair_tasks),
                Decimal(0),
            ) / len(repair_tasks)
            repair_total += repair_source * Decimal(repair_weights[(language, source)]) / 10000
        language_complete = language_complete and all(
            len(rows.get(task.task_id, [])) == cohort.planned_samples
            for task in cohort.tasks
            if task.language == language
        )
        if not language_complete:
            language_scores.append((language, None))
            repair_language[language] = None
            continue
        for name in overall_counts:
            overall_counts[name] += sum(
                (
                    Decimal(task_stats[task.task_id][name] or 0)
                    for task in cohort.tasks
                    if task.language == language
                ),
                Decimal(0),
            )
        denominator = 2 * language_counts["tp"] + language_counts["fp"] + language_counts["fn"]
        language_f1 = 2 * language_counts["tp"] / denominator if denominator else Decimal(0)
        language_tasks = [task for task in cohort.tasks if task.language == language]
        language_l = _bug_weighted_task_mean(task_stats, language_tasks, "l")
        language_x = _bug_weighted_task_mean(task_stats, language_tasks, "x")
        language_v = _bug_weighted_task_mean(task_stats, language_tasks, "v")
        language_a = (
            Decimal(".35") * language_f1 * 100
            + Decimal(".10") * language_l
            + Decimal(".10") * language_x
            + Decimal(".05") * language_v
            + Decimal(".40") * repair_total / 100
        )
        language_scores.append((language, language_a))
        repair_language[language] = repair_total
        localization_language[language] = language_l
        explanation_language[language] = language_x
        severity_language[language] = language_v
        for source in sources:
            group = [
                task
                for task in cohort.tasks
                if task.language == language and task.stratum_id == source
            ]
            counts = {
                name: sum(
                    (Decimal(task_stats[task.task_id][name] or 0) for task in group),
                    Decimal(0),
                )
                for name in ("tp", "fp", "fn")
            }
            source_precision = (
                counts["tp"] / (counts["tp"] + counts["fp"])
                if counts["tp"] + counts["fp"]
                else None
            )
            source_recall = (
                counts["tp"] / (counts["tp"] + counts["fn"])
                if counts["tp"] + counts["fn"]
                else None
            )
            denominator = 2 * counts["tp"] + counts["fp"] + counts["fn"]
            source_f1 = 2 * counts["tp"] / denominator if denominator else None
            source_detection.append((language, source, source_precision, source_recall, source_f1))
            source_repairs = [task for task in group if task.repair_required]
            if not source_repairs:
                source_language_scores.append((language, source, None))
                continue
            repair_value = sum(
                (Decimal(task_stats[task.task_id]["repair"] or 0) for task in source_repairs),
                Decimal(0),
            ) / len(source_repairs)
            source_bug_count = sum(task.required_bug_count for task in group)
            loc = (
                sum(
                    (
                        Decimal(task_stats[task.task_id]["l"] or 0) * task.required_bug_count
                        for task in group
                    ),
                    Decimal(0),
                )
                / source_bug_count
                if source_bug_count
                else Decimal(0)
            )
            exp = (
                sum(
                    (
                        Decimal(task_stats[task.task_id]["x"] or 0) * task.required_bug_count
                        for task in group
                    ),
                    Decimal(0),
                )
                / source_bug_count
                if source_bug_count
                else Decimal(0)
            )
            sev = (
                sum(
                    (
                        Decimal(task_stats[task.task_id]["v"] or 0) * task.required_bug_count
                        for task in group
                    ),
                    Decimal(0),
                )
                / source_bug_count
                if source_bug_count
                else Decimal(0)
            )
            source_score = (
                Decimal(".35") * (source_f1 or Decimal(0)) * 100
                + Decimal(".10") * loc
                + Decimal(".10") * exp
                + Decimal(".05") * sev
                + Decimal(".40") * repair_value / 100
            )
            source_language_scores.append((language, source, source_score))
    tp, fp, fn = (overall_counts[key] for key in ("tp", "fp", "fn"))
    count_denom = 2 * tp + fp + fn
    precision = tp / (tp + fp) if tp + fp else (Decimal(0) if tp + fn else None)
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * tp / count_denom if count_denom else None
    complete = all_complete and not pending
    available_scores = [score for _, score in language_scores]
    language_weights = dict(cohort.language_weights_bps)
    a_score = (
        sum(
            (
                score * Decimal(language_weights[language]) / 10000
                for language, score in language_scores
                if score is not None
            ),
            Decimal(0),
        )
        if complete and all(score is not None for score in available_scores)
        else None
    )
    repair_mean = (
        sum(
            (
                value * Decimal(language_weights[language]) / 10000
                for language, value in repair_language.items()
                if value is not None
            ),
            Decimal(0),
        )
        if len(repair_language) == len(cohort.required_languages)
        and all(value is not None for value in repair_language.values())
        else None
    )
    return TrackAAggregate(
        cohort_digest=cohort.cohort_digest,
        entry_id=entry_id,
        expected_attempts=expected,
        completed_attempts=completed,
        missing_attempts=expected - completed,
        model_failed_attempts=model_failed,
        unresolved_findings=sum(sample.result.unresolved_findings for sample in selected_samples),
        strict_status="pending_review"
        if pending
        else "complete"
        if complete
        else "partial"
        if completed
        else "unavailable",
        precision=None if pending else precision,
        recall=recall,
        f1=None if pending else f1,
        localization=(
            sum(
                (
                    value * Decimal(language_weights[language]) / 10000
                    for language, value in localization_language.items()
                ),
                Decimal(0),
            )
            if len(localization_language) == len(cohort.required_languages)
            else None
        ),
        explanation=(
            sum(
                (
                    value * Decimal(language_weights[language]) / 10000
                    for language, value in explanation_language.items()
                ),
                Decimal(0),
            )
            if len(explanation_language) == len(cohort.required_languages)
            else None
        ),
        severity=(
            sum(
                (
                    value * Decimal(language_weights[language]) / 10000
                    for language, value in severity_language.items()
                ),
                Decimal(0),
            )
            if len(severity_language) == len(cohort.required_languages)
            else None
        ),
        repair_score_bp=repair_mean,
        a_score=a_score,
        language_scores=tuple(language_scores),
        source_language_scores=tuple(source_language_scores),
        source_detection=tuple(source_detection),
        duplicate_count=sum(sample.result.duplicate_count for sample in selected_samples),
        repair_failure_count=sum(
            sample.repair.gate == "fail"
            or sample.repair.status in {"missing_patch", "invalid_patch"}
            for sample in selected_samples
        ),
        coverage=Decimal(completed) / expected if expected else Decimal(0),
        label="exploratory_internal" if a_score is not None or selected_samples else "unavailable",
        reasons=tuple(
            sorted(
                set(
                    reasons
                    + (
                        ["independent_cluster_count_below_ranked_threshold"]
                        if len({t.cluster_id for t in cohort.tasks}) < 20
                        else []
                    )
                )
            )
        ),
    )


def source_requirement_report() -> dict[str, Any]:
    """Current public-security input gate; never infer or fabricate external examples."""
    return {
        "kind": "track_a_source_requirement",
        "source_family": "disclosed_security",
        "status": "blocked",
        "blockers": [
            "owner-approved rights record and permitted redistribution/transmission scope",
            "public advisory identity, disclosure timestamp and immutable pre-fix/fix revisions",
            "isolated, reproducible vulnerable behavior plus clean controls for Python and Rust",
            "curator-reviewed hidden oracle, reproducer and source exposure policy",
        ],
        "verified_external_examples": 0,
        "evidence_tier": "none",
    }
