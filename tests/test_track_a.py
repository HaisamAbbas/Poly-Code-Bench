"""Track A acceptance semantics; all samples here are explicitly synthetic/internal."""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from polycodebench_core.canonical import canonical_digest, canonical_envelope
from polycodebench_core.solve_extraction import Finding
from polycodebench_evaluation.track_a import (
    AdjudicatedFindingSample,
    DetectionResult,
    FindingDisposition,
    GroundTruthRevision,
    MatchEdge,
    OracleBug,
    RepairResult,
    ReviewLedger,
    SourceAsset,
    TaskSourceProvenance,
    TrackACohort,
    TrackASample,
    TrackATaskCell,
    aggregate_track_a,
    apply_combined_patch,
    mutate_source,
    parse_findings,
    propose_match_edges,
    public_source_projection,
    rematch_cohort,
    revise_ground_truth,
    score_detection,
    source_requirement_report,
    track_a_review_subject_digest,
    validate_source_admission,
)
from polycodebench_evaluation.track_a_cli import main as track_a_main
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]


def write_evidence(name: str, body: dict[str, object]) -> None:
    if os.environ.get("PCB_WRITE_EVIDENCE") != "1":
        return
    target = ROOT / "docs" / "implementation" / "evidence" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(body, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def finding(
    local_id: str,
    *,
    path: str = "main.py",
    start: int = 2,
    end: int = 2,
    root: str = "path traversal uses unsafe string prefix containment",
    severity: str = "high",
) -> Finding:
    return Finding.model_validate(
        {
            "local_id": local_id,
            "path": path,
            "start_line": start,
            "end_line": end,
            "root_cause": root,
            "evidence": "reproducer demonstrates an escaped sibling path",
            "severity": severity,
        }
    )


def bug(
    bug_id: str = "B-1",
    *,
    path: str = "main.py",
    start: int = 2,
    end: int = 2,
    taxonomy: str = "path traversal",
    severity: tuple[str, ...] = ("high",),
) -> OracleBug:
    return OracleBug(
        bug_id=bug_id,
        path=path,
        causal_start_line=start,
        causal_end_line=end,
        function_start_line=1,
        function_end_line=max(5, end + 2),
        taxonomy=taxonomy,
        trigger="attacker controls a sibling path prefix",
        incorrect_behavior="the sibling is accepted as a child",
        mechanism="text prefix matching ignores path components",
        consequence="a caller can access data outside the configured root",
        accepted_severities=severity,
    )


def edge(fid: str, bid: str = "B-1") -> MatchEdge:
    return MatchEdge(
        finding_id=fid,
        bug_id=bid,
        decision="accepted",
        causal_equivalent=True,
        independent_evidence_ref="artifact:independent-reproducer",
        reviewer_id="reviewer-1",
        review_rationale="The independent reproducer proves the stated causal mechanism.",
        explanation_facts=(10000, 10000, 10000, 10000),
        evidence_id="artifact:review-packet",
    )


def disposition(fid: str, kind: str = "false_positive") -> FindingDisposition:
    return FindingDisposition(
        finding_id=fid,
        disposition=kind,  # type: ignore[arg-type]
        reviewer_id="reviewer-1",
        evidence_id="artifact:review-packet",
        rationale="Independent review confirmed this is outside the frozen bug scope.",
    )


class _FixtureReviewVerifier:
    """Test-only allowlist representing an already authenticated review service."""

    def __init__(
        self,
        *,
        edge_approvals: set[tuple[str, str]] | None = None,
        disposition_approvals: set[tuple[str, str]] | None = None,
    ) -> None:
        self.edge_approvals = edge_approvals or set()
        self.disposition_approvals = disposition_approvals or set()

    def verify_edge(self, review_edge: MatchEdge, subject_digest: str) -> bool:
        return (subject_digest, review_edge.content_digest()) in self.edge_approvals

    def verify_disposition(
        self, review_disposition: FindingDisposition, subject_digest: str
    ) -> bool:
        return (subject_digest, review_disposition.content_digest()) in self.disposition_approvals


def _trusted_fixture_reviewer(
    *,
    findings: tuple[Finding, ...],
    bugs: tuple[OracleBug, ...],
    review_context: str,
    edges: tuple[MatchEdge, ...] = (),
    dispositions: tuple[FindingDisposition, ...] = (),
) -> _FixtureReviewVerifier:
    subject_digest = track_a_review_subject_digest(findings, bugs, review_context=review_context)
    return _FixtureReviewVerifier(
        edge_approvals={(subject_digest, item.content_digest()) for item in edges},
        disposition_approvals={(subject_digest, item.content_digest()) for item in dispositions},
    )


def test_e2e_32_duplicate_tp_does_not_change_tp_fp_fn_or_micro_scores() -> None:
    submitted = [
        finding("tp"),
        finding("duplicate"),
        finding("false", start=8, end=8, root="unrelated concurrency cache leak"),
    ]
    base = {
        "main.py": b"def check(root, path):\n    return str(path).startswith(str(root))\n"
        + b"# line\n" * 8
    }
    parsed = parse_findings(
        {
            "findings": [item.model_dump() for item in submitted],
            "patch": (
                "--- a/main.py\n+++ b/main.py\n@@ -2 +2 @@\n"
                "-    return str(path).startswith(str(root))\n"
                "+    return path_is_within(root, path)\n"
            ),
        },
        base_files=base,
    )
    assert parsed.valid and parsed.patch is not None
    assert parsed.duplicate_of == (("duplicate", "tp"),)
    selected_bugs = (bug(), bug("B-2", start=6, end=6, taxonomy="resource exhaustion"))
    selected_edges = (edge("tp"),)
    selected_dispositions = (disposition("false"),)
    context = "fixture:e2e-32/model-a/sample-0"
    result = score_detection(
        findings=parsed.findings,
        bugs=selected_bugs,
        edges=selected_edges,
        dispositions=selected_dispositions,
        duplicate_of=parsed.duplicate_of,
        review_verifier=_trusted_fixture_reviewer(
            findings=parsed.findings,
            bugs=selected_bugs,
            review_context=context,
            edges=selected_edges,
            dispositions=selected_dispositions,
        ),
        review_context=context,
    )
    assert (result.true_positive, result.false_positive, result.false_negative) == (1, 1, 1)
    assert result.precision == result.recall == result.f1 == Decimal("0.5")
    assert result.duplicate_count == 1
    assert result.localization == Decimal("50")
    assert result.explanation == Decimal("50")
    assert result.severity == Decimal("50")
    write_evidence(
        "prompt-18-e2e-32.json",
        {
            "schema_version": 1,
            "kind": "prompt18_e2e32_internal_fixture_evidence",
            "evidence_tier": "synthetic_internal_fixture",
            "claims_model_benchmark_results": False,
            "task_id": "synthetic-track-a-known-fp-fn-duplicate-v1",
            "patch": parsed.patch,
            "base_digest": parsed.base_digest,
            "oracle_bug_ids": ["B-1", "B-2"],
            "reported_finding_ids": [finding.local_id for finding in parsed.findings],
            "duplicate_of": [list(pair) for pair in parsed.duplicate_of],
            "reviewed_false_positive_ids": ["false"],
            "result": result.model_dump(mode="json"),
        },
    )


def test_file_coincidence_is_not_a_match_and_novel_claim_stays_pending() -> None:
    submitted = finding("new", start=9, end=9, root="credential cache contention")
    known = bug(taxonomy="path traversal")
    assert propose_match_edges((submitted,), (known,)) == ()
    pending = score_detection(
        findings=(submitted,),
        bugs=(known,),
        edges=(),
        dispositions=(disposition("new", "novel_accepted"),),
    )
    assert pending.status == "pending_review"
    assert pending.true_positive == 0 and pending.false_negative == 1
    assert pending.precision is None and pending.f1 is None


def test_e2e_33_oracle_revision_requires_cohort_wide_rematching() -> None:
    previous = GroundTruthRevision.create(1, None, (bug(),))
    updated_bug = bug("B-2", start=9, end=9, taxonomy="credential cache contention")
    revision, rematch = revise_ground_truth(
        previous,
        new_bugs=(bug(), updated_bug),
        affected_evaluation_ids=("model-a/sample-0", "model-b/sample-0"),
    )
    assert revision.version == 2 and revision.parent_digest == previous.digest
    assert rematch.required_rematches == ("model-a/sample-0", "model-b/sample-0")
    source_reports = tuple(
        AdjudicatedFindingSample(
            evaluation_id=evaluation_id,
            findings=(finding("new", start=9, end=9, root="credential cache contention"),),
            edges=(edge("new", "B-2"),),
            dispositions=(),
        )
        for evaluation_id in rematch.required_rematches
    )
    edge_approvals: set[tuple[str, str]] = set()
    for report in source_reports:
        subject_digest = track_a_review_subject_digest(
            report.findings, revision.bugs, review_context=report.evaluation_id
        )
        edge_approvals.update(
            (subject_digest, reviewed_edge.content_digest()) for reviewed_edge in report.edges
        )
    rematched = rematch_cohort(
        revision,
        source_reports,
        expected_evaluation_ids=rematch.required_rematches,
        review_verifier=_FixtureReviewVerifier(edge_approvals=edge_approvals),
    )
    reports = [
        (evaluation_id.split("/")[0], outcome.true_positive, outcome.false_negative, outcome.f1)
        for evaluation_id, outcome in rematched.results
    ]
    assert reports[0][1:] == reports[1][1:]
    assert reports[0][1:] == (1, 1, Decimal(2) / Decimal(3))
    with pytest.raises(ValueError, match="each affected evaluation"):
        rematch_cohort(
            revision, source_reports[:1], expected_evaluation_ids=rematch.required_rematches
        )
    write_evidence(
        "prompt-18-e2e-33.json",
        {
            "schema_version": 1,
            "kind": "prompt18_e2e33_internal_fixture_evidence",
            "evidence_tier": "synthetic_internal_fixture",
            "claims_model_benchmark_results": False,
            "old_oracle_digest": previous.digest,
            "new_oracle_digest": revision.digest,
            "rematch_result_digest": rematched.content_digest(),
            "rematch_plan": {
                "evaluation_ids": list(rematch.evaluation_ids),
                "required_rematches": list(rematch.required_rematches),
            },
            "equally_rematched_entries": [
                {"entry_id": entry, "true_positive": tp, "false_negative": fn, "f1": str(f1)}
                for entry, tp, fn, f1 in reports
            ],
        },
    )


def test_e2e_34_failed_repair_is_separate_and_clean_control_has_no_repair_score() -> None:
    detection_findings = (finding("tp"),)
    detection_bugs = (bug(),)
    detection_edges = (edge("tp"),)
    detection_context = "fixture:e2e-34/model-a/sample-0"
    detection = score_detection(
        findings=detection_findings,
        bugs=detection_bugs,
        edges=detection_edges,
        review_verifier=_trusted_fixture_reviewer(
            findings=detection_findings,
            bugs=detection_bugs,
            review_context=detection_context,
            edges=detection_edges,
        ),
        review_context=detection_context,
    )
    failed_repair = RepairResult(
        status="evaluated",
        repair_required=True,
        composite_score_bp=0,
        evaluation_id="evaluation-1",
        evaluation_digest="sha256:" + "a" * 64,
        gate="fail",
        patch_digest="sha256:" + "b" * 64,
        patched_source_digest="sha256:" + "c" * 64,
        reasons=("required_regression_failed",),
    )
    clean_control = RepairResult(
        status="not_required",
        repair_required=False,
        composite_score_bp=None,
        reasons=("clean_control_has_no_repair_score",),
    )
    assert detection.true_positive == 1 and detection.f1 == 1
    assert failed_repair.composite_score_bp == 0
    assert clean_control.composite_score_bp is None


def test_model_failure_counts_as_zero_credit_while_other_entry_pending_is_ignored() -> None:
    cohort = _cohort()
    failed_detection = score_detection(findings=(), bugs=(bug(),), edges=(), schema_valid=False)
    assert failed_detection.precision is None
    failed_sample = TrackASample.model_validate(
        {
            **_sample("model-a", "py-history").model_dump(),
            "attempt_outcome": "model_failed",
            "result": failed_detection,
            "repair": RepairResult(
                status="missing_patch",
                repair_required=True,
                composite_score_bp=0,
                reasons=("model_failed_no_patch",),
            ),
        }
    )
    other_pending = TrackASample.model_validate(
        {
            **_sample("model-b", "py-history").model_dump(),
            "result": DetectionResult(
                status="pending_review",
                true_positive=0,
                false_positive=0,
                false_negative=1,
                unresolved_findings=1,
                duplicate_count=0,
                precision=None,
                recall=Decimal(0),
                f1=None,
                localization=Decimal(0),
                explanation=Decimal(0),
                severity=Decimal(0),
                matched_pairs=(),
                edge_digests=(),
                reasons=("unresolved_findings_block_strict_precision_and_f1",),
            ),
        }
    )
    samples = (
        failed_sample,
        _sample("model-a", "py-control", clean=True, fp=1),
        _sample("model-a", "rs-history"),
        _sample("model-a", "rs-control", clean=True),
        other_pending,
    )

    result = aggregate_track_a(cohort, samples, "model-a")

    assert result.strict_status == "complete"
    assert result.expected_attempts == result.completed_attempts == 4
    assert result.missing_attempts == 0 and result.model_failed_attempts == 1
    assert result.repair_failure_count == 1
    assert result.f1 == Decimal("0.5") and result.recall == Decimal("0.5")
    assert result.localization == result.explanation == result.severity == Decimal("50")


def test_finding_schema_failure_discards_every_finding_and_records_reason() -> None:
    parsed = parse_findings(
        {
            "findings": [
                finding("valid").model_dump(),
                {"local_id": "bad", "path": "../secret.py", "start_line": 1},
            ]
        },
        base_files={"main.py": b"x\n" * 10},
    )
    assert not parsed.valid and parsed.findings == ()
    result = score_detection(findings=(), bugs=(bug(),), edges=(), schema_valid=False)
    assert result.status == "invalid_submission"
    assert result.true_positive == 0 and result.false_negative == 1
    assert result.recall == 0 and result.f1 == 0


def test_overbroad_span_is_not_exact_but_same_function_gets_70() -> None:
    findings = (finding("wide", start=1, end=3),)
    bugs = (bug(),)
    edges = (edge("wide"),)
    context = "fixture:overbroad-span/model-a/sample-0"
    result = score_detection(
        findings=findings,
        bugs=bugs,
        edges=edges,
        review_verifier=_trusted_fixture_reviewer(
            findings=findings,
            bugs=bugs,
            review_context=context,
            edges=edges,
        ),
        review_context=context,
    )
    assert result.localization == 70


def test_untrusted_review_edges_and_dispositions_fail_closed() -> None:
    finding_value = finding("claim")
    bug_value = bug()
    edge_value = edge("claim")
    result = score_detection(
        findings=(finding_value,),
        bugs=(bug_value,),
        edges=(edge_value,),
        dispositions=(disposition("claim", "false_positive"),),
    )
    assert result.status == "pending_review"
    assert result.true_positive == result.false_positive == 0
    assert result.false_negative == result.unresolved_findings == 1
    assert "review_authority_or_evidence_not_verified" in result.reasons


def test_review_approval_is_bound_to_its_evaluation_and_verifier_failure_is_closed() -> None:
    findings = (finding("claim"),)
    bugs = (bug(),)
    edges = (edge("claim"),)
    reviewer = _trusted_fixture_reviewer(
        findings=findings,
        bugs=bugs,
        review_context="evaluation-original",
        edges=edges,
    )

    replayed = score_detection(
        findings=findings,
        bugs=bugs,
        edges=edges,
        review_verifier=reviewer,
        review_context="evaluation-replayed",
    )
    assert replayed.status == "pending_review" and replayed.true_positive == 0

    class BrokenVerifier:
        def verify_edge(self, edge: MatchEdge, subject_digest: str) -> bool:
            raise RuntimeError("review store unavailable")

        def verify_disposition(self, disposition: FindingDisposition, subject_digest: str) -> bool:
            raise RuntimeError("review store unavailable")

    unavailable = score_detection(
        findings=findings,
        bugs=bugs,
        edges=edges,
        review_verifier=BrokenVerifier(),
        review_context="evaluation-original",
    )
    assert unavailable.status == "pending_review" and unavailable.true_positive == 0
    assert "review_authority_or_evidence_not_verified" in unavailable.reasons

    class MalformedVerifier:
        def verify_edge(self, edge: MatchEdge, subject_digest: str) -> Any:
            return object()

        def verify_disposition(self, disposition: FindingDisposition, subject_digest: str) -> bool:
            return False

    malformed = score_detection(
        findings=findings,
        bugs=bugs,
        edges=edges,
        review_verifier=MalformedVerifier(),
        review_context="evaluation-original",
    )
    assert malformed.status == "pending_review" and malformed.true_positive == 0


def test_score_detection_cli_does_not_trust_reviewer_ids_from_raw_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    finding_value = finding("claim")
    record: dict[str, Any] = {
        "findings": [finding_value.model_dump(mode="json")],
        "bugs": [bug().model_dump(mode="json")],
        "edges": [edge("claim").model_dump(mode="json")],
        "dispositions": [],
    }
    input_path = tmp_path / "raw-review.json"
    input_path.write_text(json.dumps(record), encoding="utf-8")

    assert track_a_main(["score-detection", "--input", str(input_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "pending_review"
    assert result["true_positive"] == 0 and result["false_negative"] == 1
    assert "review_authority_or_evidence_not_verified" in result["reasons"]

    record["duplicate_of"] = [["claim", "invented-canonical-finding"]]
    input_path.write_text(json.dumps(record), encoding="utf-8")
    assert track_a_main(["score-detection", "--input", str(input_path)]) == 2
    assert "duplicate mapping must match" in capsys.readouterr().err


def test_patch_application_is_fresh_allowlisted_and_protected() -> None:
    base = {"src/main.py": b"def f():\n    return 1\n", "tests/test_main.py": b"assert True\n"}
    patch = "--- a/src/main.py\n+++ b/src/main.py\n@@ -2 +2 @@\n-    return 1\n+    return 2\n"
    applied = apply_combined_patch(base, patch, allowed_paths=("src/",))
    assert applied["src/main.py"] == b"def f():\n    return 2\n"
    assert applied["tests/test_main.py"] == base["tests/test_main.py"]
    disallowed = (
        "--- a/tests/test_main.py\n+++ b/tests/test_main.py\n"
        "@@ -1 +1 @@\n-assert True\n+assert False\n"
    )
    with pytest.raises(ValueError, match="allowlist"):
        apply_combined_patch(base, disallowed, allowed_paths=("src/",))


def test_source_gates_reject_public_injection_logs_and_keep_cve_requirement_blocked() -> None:
    with pytest.raises(ValidationError, match="injection logs must stay hidden"):
        TaskSourceProvenance(
            source_family="injected",
            language="python",
            authorship_record_id="authored-internal",
            behavior_change_proof="proof",
            build_proof="build",
            clean_control_id="control",
            assets=(
                SourceAsset(
                    asset_id="log",
                    digest="sha256:" + "1" * 64,
                    visibility="public",
                    role="injection_log",
                ),
                SourceAsset(
                    asset_id="src",
                    digest="sha256:" + "2" * 64,
                    visibility="private",
                    role="visible_source",
                ),
                SourceAsset(
                    asset_id="oracle",
                    digest="sha256:" + "3" * 64,
                    visibility="hidden",
                    role="hidden_oracle",
                ),
            ),
            evidence_tier="synthetic_internal",
        )
    blocked = source_requirement_report()
    assert blocked["status"] == "blocked" and blocked["verified_external_examples"] == 0


def test_source_validation_cli_returns_failure_for_unadmitted_public_security_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = TaskSourceProvenance(
        source_family="disclosed_security",
        language="python",
        assets=(),
        evidence_tier="synthetic_internal",
    )
    path = tmp_path / "source.json"
    path.write_text(source.model_dump_json(), encoding="utf-8")

    assert track_a_main(["validate-source", "--input", str(path)]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["admitted"] is False
    assert "public_security_source_requires_verified_external_evidence" in output["blockers"]


def test_injection_and_mutation_builders_keep_private_proofs_out_of_public_projection() -> None:
    source = TaskSourceProvenance(
        source_family="injected",
        language="python",
        authorship_record_id="internal-source-record",
        rights_record_id="internal-rights-record",
        rights_verified=True,
        evidence_verified=True,
        behavior_change_proof="sha256:" + "4" * 64,
        build_proof="sha256:" + "5" * 64,
        clean_control_id="python-clean-1",
        assets=(
            SourceAsset(
                asset_id="visible",
                digest="sha256:" + "6" * 64,
                visibility="public",
                role="visible_source",
            ),
            SourceAsset(
                asset_id="oracle",
                digest="sha256:" + "7" * 64,
                visibility="hidden",
                role="hidden_oracle",
            ),
            SourceAsset(
                asset_id="injection-log",
                digest="sha256:" + "8" * 64,
                visibility="hidden",
                role="injection_log",
            ),
        ),
        evidence_tier="synthetic_internal",
    )
    assert validate_source_admission(source).admitted
    projection = public_source_projection(
        source,
        {
            "visible": b"base source",
            "oracle": b"expected defect",
            "injection-log": b"private steps",
        },
    )
    assert projection == {"visible": b"base source"}
    mutated, record = mutate_source(
        b"return candidate.startswith(root)\n",
        path="src/path.rs",
        old=b"startswith",
        new=b"contains",
        operator="replace-path-predicate",
        operator_version="1",
        seed=42,
        behavior_change_proof="proof:behavior",
        reference_repair_proof="proof:reference",
        build_proof="proof:build",
    )
    assert mutated == b"return candidate.contains(root)\n"
    assert (record.changed_start_line, record.changed_end_line, record.seed) == (1, 1, 42)
    with pytest.raises(ValueError, match="equivalent.*mutation"):
        mutate_source(
            b"x = 1",
            path="x.py",
            old=b"x = 1",
            new=b"x = 1",
            operator="noop",
            operator_version="1",
            seed=1,
            behavior_change_proof="proof",
            reference_repair_proof="repair",
            build_proof="build",
        )


def test_review_ledger_is_append_only_hash_chained_and_detects_tampering() -> None:
    ledger = ReviewLedger().append(
        actor_id="reviewer-1",
        action="edge_review",
        subject_digest="sha256:" + "a" * 64,
        payload_digest="sha256:" + "b" * 64,
        created_at="2026-10-02T00:00:00Z",
    )
    next_ledger = ledger.append(
        actor_id="reviewer-2",
        action="edge_review",
        subject_digest="sha256:" + "c" * 64,
        payload_digest="sha256:" + "d" * 64,
        created_at="2026-10-02T00:01:00Z",
    )
    assert ledger.verify() and next_ledger.verify()
    assert (
        len(ledger.events) == 1
        and next_ledger.events[1].previous_event_digest == ledger.events[0].event_digest
    )
    tampered = ReviewLedger(
        (next_ledger.events[0].model_copy(update={"payload_digest": "sha256:" + "f" * 64}),)
    )
    assert not tampered.verify()


def _cohort() -> TrackACohort:
    tasks = (
        TrackATaskCell(
            task_id="py-history",
            task_version=1,
            language="python",
            source_family="historical",
            cluster_id="py-c1",
            stratum_id="history",
            repair_required=True,
            required_bug_count=1,
        ),
        TrackATaskCell(
            task_id="py-control",
            task_version=1,
            language="python",
            source_family="injected",
            cluster_id="py-c2",
            stratum_id="clean",
            repair_required=False,
            required_bug_count=0,
        ),
        TrackATaskCell(
            task_id="rs-history",
            task_version=1,
            language="rust",
            source_family="historical",
            cluster_id="rs-c1",
            stratum_id="history",
            repair_required=True,
            required_bug_count=1,
        ),
        TrackATaskCell(
            task_id="rs-control",
            task_version=1,
            language="rust",
            source_family="injected",
            cluster_id="rs-c2",
            stratum_id="clean",
            repair_required=False,
            required_bug_count=0,
        ),
    )
    body = {
        "schema_version": 1,
        "tasks": tasks,
        "required_languages": ("python", "rust"),
        "planned_samples": 1,
        "source_weights_bps": (
            ("python", "history", 5000),
            ("python", "clean", 5000),
            ("rust", "history", 5000),
            ("rust", "clean", 5000),
        ),
        "repair_source_weights_bps": (("python", "history", 10000), ("rust", "history", 10000)),
        "language_weights_bps": (("python", 5000), ("rust", 5000)),
    }
    payload = {
        "tasks": [task.model_dump(mode="json") for task in tasks],
        "required_languages": list(body["required_languages"]),
        "planned_samples": body["planned_samples"],
        "source_weights_bps": [list(item) for item in body["source_weights_bps"]],
        "repair_source_weights_bps": [list(item) for item in body["repair_source_weights_bps"]],
        "language_weights_bps": [list(item) for item in body["language_weights_bps"]],
    }
    cohort_digest = canonical_digest(canonical_envelope("track_a_cohort", payload))
    return TrackACohort(**body, cohort_digest=cohort_digest)


def _sample(entry: str, task_id: str, *, clean: bool = False, fp: int = 0) -> TrackASample:
    result = (
        DetectionResult(
            status="complete",
            true_positive=0,
            false_positive=fp,
            false_negative=0,
            unresolved_findings=0,
            duplicate_count=0,
            precision=Decimal(0) if fp else None,
            recall=None,
            f1=None,
            localization=None,
            explanation=None,
            severity=None,
            matched_pairs=(),
            edge_digests=(),
            reasons=(),
        )
        if clean
        else DetectionResult(
            status="complete",
            true_positive=1,
            false_positive=fp,
            false_negative=0,
            unresolved_findings=0,
            duplicate_count=0,
            precision=Decimal(1) / Decimal(1 + fp),
            recall=Decimal(1),
            f1=Decimal(2) / Decimal(2 + fp),
            localization=Decimal(100),
            explanation=Decimal(100),
            severity=Decimal(100),
            matched_pairs=(("f", "b"),),
            edge_digests=(),
            reasons=(),
        )
    )
    repair = (
        RepairResult(
            status="not_required",
            repair_required=False,
            composite_score_bp=None,
            reasons=("clean_control_has_no_repair_score",),
        )
        if clean
        else RepairResult(
            status="evaluated",
            repair_required=True,
            composite_score_bp=10000,
            evaluation_id=f"{entry}/{task_id}",
            evaluation_digest="sha256:" + "e" * 64,
            gate="pass",
            patch_digest="sha256:" + "f" * 64,
            patched_source_digest="sha256:" + "1" * 64,
            reasons=(),
        )
    )
    return TrackASample(
        entry_id=entry,
        task_id=task_id,
        task_version=1,
        sample_index=0,
        result=result,
        repair=repair,
        scorecard_digest="sha256:" + "2" * 64,
        evidence_tier="authored_internal",
    )


def test_track_a_aggregate_balances_languages_and_does_not_renormalize_missing_control() -> None:
    cohort = _cohort()
    samples = (
        _sample("m", "py-history"),
        _sample("m", "py-control", clean=True, fp=1),
        _sample("m", "rs-history"),
        _sample("m", "rs-control", clean=True),
    )
    result = aggregate_track_a(cohort, samples, "m")
    assert result.expected_attempts == result.completed_attempts == 4
    assert result.precision == Decimal(2) / Decimal(3)
    assert result.recall == Decimal(1)
    assert (
        dict((lang, score) for lang, score in result.language_scores)["python"]
        < dict((lang, score) for lang, score in result.language_scores)["rust"]
    )
    assert result.a_score is not None and result.repair_score_bp == 10000
    assert result.source_detection
    partial = aggregate_track_a(cohort, samples[:-1], "m")
    assert partial.completed_attempts == 3 and partial.coverage == Decimal("0.75")
    assert partial.missing_attempts == 1
    assert partial.a_score is None and partial.strict_status == "partial"
