"""Contamination controls for generated and parametric tasks (packages/taskgen)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from polycodebench_taskgen.canary import (
    CANARY_SENTENCE,
    contains_canary,
    derive_canary,
    embed_canary,
    find_canaries,
)
from polycodebench_taskgen.cli import main as taskgen_main
from polycodebench_taskgen.contracts import (
    CandidateTask,
    ExposureEvent,
    ScreeningDecision,
)
from polycodebench_taskgen.exposure import ExposureLedger
from polycodebench_taskgen.overlap import (
    ReferenceIndex,
    build_reference_index,
    structural_tokens,
    surface_tokens,
)
from polycodebench_taskgen.parametric import (
    ParameterRange,
    ParametricFamily,
    RoundCommitment,
)
from polycodebench_taskgen.screening import CandidateScreener, ScreeningPolicy
from polycodebench_taskgen.splits import (
    SplitRatios,
    assign_split,
    cluster_by_containment,
    rank_agreement_bp,
)

SECRET = b"s" * 32
OTHER_SECRET = b"t" * 32
RATIOS = SplitRatios(public_development_bp=3000, public_validation_bp=1000, private_heldout_bp=6000)

SEEN_SOLUTION = '''def merge_intervals(intervals):
    """Merge overlapping closed intervals and return them sorted."""
    ordered = sorted(intervals, key=lambda pair: pair[0])
    merged = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged
'''

RENAMED_SOLUTION = '''def combine_spans(spans):
    """Combine touching closed spans and return them ordered."""
    arranged = sorted(spans, key=lambda item: item[0])
    out = []
    for lo, hi in arranged:
        if out and lo <= out[-1][1]:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return out
'''

FRESH_SOLUTION = """def busiest_window(events, width):
    counts = {}
    best = 0
    left = 0
    for moment in sorted(events):
        counts[moment] = counts.get(moment, 0) + 1
        while moment - left >= width:
            counts[left] -= 1
            left += 1
        best = max(best, sum(counts.values()))
    return best
"""

TESTS = "assert merge_intervals([[1, 3], [2, 6]]) == [[1, 6]]\n"


def _candidate(
    candidate_id: str,
    solution: str,
    *,
    family: str = "interval-merge",
    statement: str = "Produce the merged view of a set of ranges.",
    external: bool = False,
    generator: str = "local-template-v1",
    extra_tests: str = "",
) -> CandidateTask:
    return CandidateTask(
        candidate_id=candidate_id,
        family_id=family,
        generator_id=generator,
        generator_is_external=external,
        statement=statement,
        reference_solution=solution,
        hidden_tests=TESTS + extra_tests,
    )


def _policy(**overrides: object) -> ScreeningPolicy:
    values: dict[str, object] = {"policy_id": "pilot-test", "split_ratios": RATIOS}
    values.update(overrides)
    return ScreeningPolicy.model_validate(values)


def _screener(
    tmp_path: Path, policy: ScreeningPolicy | None = None, **kwargs: object
) -> CandidateScreener:
    corpus = tmp_path / "seen"
    corpus.mkdir(exist_ok=True)
    (corpus / "merge_intervals.py").write_text(SEEN_SOLUTION, encoding="utf-8")
    index, digest = build_reference_index({"seen": corpus}, ngram=8)
    chosen = policy or _policy()
    return CandidateScreener(
        index=index,
        policy=chosen,
        canary_secret=SECRET,
        split_secret=OTHER_SECRET,
        corpus_digest=digest,
        **kwargs,  # type: ignore[arg-type]
    )


def _gate(report: object, name: str) -> object:
    return next(gate for gate in report.gates if gate.gate == name)  # type: ignore[attr-defined]


# ---- canaries -------------------------------------------------------------------------------


def test_canary_is_stable_per_item_and_separates_items() -> None:
    first = derive_canary(SECRET, family_id="f", candidate_id="a")
    assert first == derive_canary(SECRET, family_id="f", candidate_id="a")
    assert first != derive_canary(SECRET, family_id="f", candidate_id="b")
    assert first != derive_canary(OTHER_SECRET, family_id="f", candidate_id="a")


def test_canary_secret_must_be_long_enough() -> None:
    with pytest.raises(ValueError, match="at least 32 bytes"):
        derive_canary(b"short", family_id="f", candidate_id="a")


def test_embed_canary_is_idempotent_and_detectable() -> None:
    canary = derive_canary(SECRET, family_id="f", candidate_id="a")
    once = embed_canary("statement body", canary)
    assert CANARY_SENTENCE in once
    assert contains_canary(once, canary)
    assert embed_canary(once, canary) == once
    assert find_canaries(once) == (canary,)


# ---- overlap ----------------------------------------------------------------------------------


def test_comments_do_not_change_surface_tokens() -> None:
    with_comment = "x = 1  # the answer\n"
    without = "x = 1\n"
    assert surface_tokens(with_comment) == surface_tokens(without)


def test_rust_attributes_are_not_treated_as_comments() -> None:
    assert "#" in surface_tokens("#[derive(Debug)]\nstruct A;")


def test_structural_view_matches_a_renamed_copy_but_surface_does_not() -> None:
    index = ReferenceIndex(ngram=8)
    index.add("seen/merge.py", SEEN_SOLUTION)
    overlap = index.screen(RENAMED_SOLUTION)
    assert overlap.view("structural").union_bp > overlap.view("surface").union_bp
    assert overlap.view("structural").max_document_bp >= 8000
    assert overlap.view("structural").max_document_id == "seen/merge.py"


def test_unrelated_text_has_negligible_overlap() -> None:
    index = ReferenceIndex(ngram=8)
    index.add("seen/merge.py", SEEN_SOLUTION)
    overlap = index.screen(FRESH_SOLUTION)
    assert overlap.view("surface").union_bp < 2000
    assert overlap.view("structural").union_bp < 5000


def test_reference_index_rejects_duplicates_and_bad_ngram() -> None:
    index = ReferenceIndex(ngram=8)
    index.add("doc", SEEN_SOLUTION)
    with pytest.raises(ValueError, match="duplicate"):
        index.add("doc", FRESH_SOLUTION)
    with pytest.raises(ValueError, match="between 3 and 64"):
        ReferenceIndex(ngram=2)


def test_structural_tokens_normalise_identifiers_and_numbers() -> None:
    tokens = structural_tokens("def score(alpha): return alpha + 42")
    assert tokens == ["def", "ID", "(", "ID", ")", ":", "return", "ID", "+", "NUM"]


def test_build_reference_index_digest_tracks_content(tmp_path: Path) -> None:
    corpus = tmp_path / "seen"
    corpus.mkdir()
    (corpus / "a.py").write_text(SEEN_SOLUTION, encoding="utf-8")
    (corpus / "notes.bin").write_bytes(b"\xff\xfe\x00")
    _, first = build_reference_index({"seen": corpus})
    _, again = build_reference_index({"seen": corpus})
    assert first == again
    (corpus / "a.py").write_text(FRESH_SOLUTION, encoding="utf-8")
    _, changed = build_reference_index({"seen": corpus})
    assert changed != first


# ---- exposure ledger --------------------------------------------------------------------------


def _at(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


def _event(task: str, audience: str, recipient: str, when: datetime) -> ExposureEvent:
    return ExposureEvent(
        task_id=task,
        audience=audience,  # type: ignore[arg-type]
        recipient_id=recipient,
        occurred_at=when,
    )


def test_unknown_cutoff_never_qualifies() -> None:
    ledger = ExposureLedger()
    decision = ledger.eligibility_for_model(
        "task-a", recipient_id="provider:x", cutoff_at=None, cutoff_confidence="unknown",
        evaluation_at=_at(2026, 10, 8),
    )  # fmt: skip
    assert not decision.eligible
    assert decision.reason == "model_cutoff_unknown"


def test_exposure_after_cutoff_is_eligible_and_before_is_not() -> None:
    ledger = ExposureLedger([_event("late", "generator_model", "provider:gen", _at(2026, 9, 1))])
    after = ledger.eligibility_for_model(
        "late", recipient_id="provider:model", cutoff_at=_at(2026, 8, 1),
        cutoff_confidence="verified", evaluation_at=_at(2026, 10, 8),
    )  # fmt: skip
    assert after.eligible and after.reason == "first_external_exposure_after_cutoff"

    early = ExposureLedger([_event("early", "generator_model", "provider:gen", _at(2026, 6, 1))])
    before = early.eligibility_for_model(
        "early", recipient_id="provider:model", cutoff_at=_at(2026, 8, 1),
        cutoff_confidence="verified", evaluation_at=_at(2026, 10, 8),
    )  # fmt: skip
    assert not before.eligible and before.reason == "exposed_on_or_before_cutoff"


def test_recipient_that_already_received_a_task_is_refused() -> None:
    ledger = ExposureLedger(
        [_event("held", "evaluated_model", "provider:model-a", _at(2026, 9, 15))]
    )
    decision = ledger.eligibility_for_model(
        "held", recipient_id="provider:model-a", cutoff_at=_at(2026, 1, 1),
        cutoff_confidence="verified", evaluation_at=_at(2026, 10, 8),
    )  # fmt: skip
    assert decision.reason == "already_exposed_to_recipient"
    other = ledger.eligibility_for_model(
        "held", recipient_id="provider:model-b", cutoff_at=_at(2026, 1, 1),
        cutoff_confidence="verified", evaluation_at=_at(2026, 10, 8),
    )  # fmt: skip
    assert other.eligible


def test_internal_exposure_does_not_count_as_seen() -> None:
    ledger = ExposureLedger([_event("local", "internal", "local:ollama", _at(2026, 9, 1))])
    assert ledger.recipients_seen("local") == frozenset()
    assert ledger.first_external_exposure("local") is None


def test_naive_timestamps_are_rejected() -> None:
    naive = ExposureEvent(
        task_id="t", audience="public", recipient_id="web",
        occurred_at=datetime(2026, 9, 1),
    )  # fmt: skip
    with pytest.raises(ValueError, match="timezone-aware"):
        ExposureLedger().record(naive)


# ---- splits and agreement ---------------------------------------------------------------------


def test_split_ratios_must_sum_to_whole() -> None:
    with pytest.raises(ValueError, match="10000"):
        SplitRatios(public_development_bp=3000, public_validation_bp=1000, private_heldout_bp=5000)


def test_split_assignment_is_keyed_and_roughly_proportional() -> None:
    assert assign_split("cluster-1", secret=SECRET, ratios=RATIOS) == assign_split(
        "cluster-1", secret=SECRET, ratios=RATIOS
    )
    counts: dict[str, int] = {}
    for index in range(2000):
        split = assign_split(f"c{index}", secret=SECRET, ratios=RATIOS)
        counts[split] = counts.get(split, 0) + 1
    assert 0.5 < counts["private_heldout"] / 2000 < 0.7
    # Without the secret an outsider cannot predict the split: another key reassigns clusters.
    assert any(
        assign_split(f"c{index}", secret=SECRET, ratios=RATIOS)
        != assign_split(f"c{index}", secret=OTHER_SECRET, ratios=RATIOS)
        for index in range(50)
    )


def test_clustering_joins_variants_and_keeps_unrelated_items_apart() -> None:
    clusters = cluster_by_containment(
        {"a": SEEN_SOLUTION, "b": SEEN_SOLUTION.replace("merged", "combined"), "c": FRESH_SOLUTION},
        ngram=8,
        link_threshold_bp=3000,
    )
    assert clusters["a"] == clusters["b"]
    assert clusters["c"] == "c"


def test_rank_agreement_reports_perfect_reversed_and_insufficient_cases() -> None:
    validation = {"m1": 10, "m2": 20, "m3": 30, "m4": 40}
    assert rank_agreement_bp(validation, dict(validation)) == 10000
    assert rank_agreement_bp(validation, {"m1": 40, "m2": 30, "m3": 20, "m4": 10}) == -10000
    assert rank_agreement_bp({"m1": 1, "m2": 2}, {"m1": 1, "m2": 2}) is None
    assert rank_agreement_bp(validation, {"m1": 5, "m2": 5, "m3": 5, "m4": 5}) is None


def test_rank_agreement_handles_ties_with_average_ranks() -> None:
    assert rank_agreement_bp({"a": 1, "b": 1, "c": 2}, {"a": 5, "b": 5, "c": 9}) == 10000


# ---- parametric instances ---------------------------------------------------------------------


def _family() -> ParametricFamily:
    return ParametricFamily(
        family_id="window-sum",
        parameters=(
            ParameterRange(name="width", minimum=2, maximum=900),
            ParameterRange(name="length", minimum=10, maximum=1000),
        ),
        statement_template="Return the largest sum of {width} consecutive values in a list of "
        "{length}.",
    )  # fmt: skip


def test_template_rejects_undeclared_placeholders() -> None:
    with pytest.raises(ValueError, match="undeclared"):
        ParametricFamily(
            family_id="bad",
            parameters=(ParameterRange(name="n", minimum=1, maximum=5),),
            statement_template="Use {n} and {missing}.",
        )


def test_parametric_instances_are_deterministic_and_round_bound() -> None:
    family = _family()
    round_secret = b"r" * 32
    first = family.sample_many(round_secret=round_secret, round_id="round-1", count=20)
    again = family.sample_many(round_secret=round_secret, round_id="round-1", count=20)
    assert first == again
    other_round = family.sample_many(round_secret=b"q" * 32, round_id="round-2", count=20)
    assert [item.parameters for item in first] != [item.parameters for item in other_round]
    for instance in first:
        assert "{" not in instance.statement
        values = dict(instance.parameters)
        assert 2 <= values["width"] <= 900 and 10 <= values["length"] <= 1000


def test_small_parameter_spaces_are_refused() -> None:
    tiny = ParametricFamily(
        family_id="tiny",
        parameters=(ParameterRange(name="n", minimum=1, maximum=3),),
        statement_template="Compute for {n}.",
    )
    with pytest.raises(ValueError, match="below the required minimum"):
        tiny.assert_minimum_space(1_000_000)
    with pytest.raises(ValueError, match="collided"):
        tiny.sample_many(round_secret=b"r" * 32, round_id="round-x", count=4)


def test_round_commitment_verifies_only_the_committed_secret() -> None:
    commitment = RoundCommitment.commit(round_id="round-7", round_secret=b"c" * 32)
    assert commitment.verify(b"c" * 32)
    assert not commitment.verify(b"d" * 32)


# ---- screening --------------------------------------------------------------------------------


def test_verbatim_copy_of_a_seen_solution_is_rejected(tmp_path: Path) -> None:
    screener = _screener(tmp_path)
    report = screener.screen_candidate(_candidate("copy-1", SEEN_SOLUTION))
    assert report.decision is ScreeningDecision.REJECTED
    assert _gate(report, "surface_document").passed is False  # type: ignore[attr-defined]


def test_renamed_copy_is_caught_by_the_structural_view(tmp_path: Path) -> None:
    screener = _screener(tmp_path)
    report = screener.screen_candidate(_candidate("rename-1", RENAMED_SOLUTION))
    assert report.decision is ScreeningDecision.REJECTED
    structural = [gate for gate in report.gates if gate.gate.startswith("structural")]
    assert any(not gate.passed for gate in structural)


def test_fresh_candidate_is_ready_and_gets_a_split(tmp_path: Path) -> None:
    screener = _screener(tmp_path)
    fresh = _candidate("fresh-1", FRESH_SOLUTION, statement="Find the busiest window.")
    (report,) = screener.screen_batch([fresh])
    assert report.decision is ScreeningDecision.READY_FOR_EXECUTABLE_ADMISSION
    assert report.split in {"public_development", "public_validation", "private_heldout"}
    assert report.cluster_id == "fresh-1"
    expected = derive_canary(SECRET, family_id=fresh.family_id, candidate_id="fresh-1")
    assert report.canary == expected


def test_external_generator_must_be_approved(tmp_path: Path) -> None:
    screener = _screener(tmp_path)
    report = screener.screen_candidate(
        _candidate("ext-1", FRESH_SOLUTION, external=True, generator="hosted-model-x")
    )
    assert report.decision is ScreeningDecision.REJECTED
    assert _gate(report, "generator_approval").reasons == ("external_generator_not_approved",)  # type: ignore[attr-defined]

    approved = _policy(approved_external_generators=("hosted-model-x",))
    allowed = _screener(tmp_path, policy=approved).screen_candidate(
        _candidate("ext-2", FRESH_SOLUTION, external=True, generator="hosted-model-x")
    )
    assert allowed.decision is ScreeningDecision.READY_FOR_EXECUTABLE_ADMISSION


def test_candidate_carrying_another_items_canary_is_rejected(tmp_path: Path) -> None:
    other = derive_canary(SECRET, family_id="other", candidate_id="elsewhere")
    screener = _screener(tmp_path)
    report = screener.screen_candidate(
        _candidate("leak-1", FRESH_SOLUTION, extra_tests=f"# CANARY GUID {other}\n")
    )
    assert report.decision is ScreeningDecision.REJECTED
    assert _gate(report, "canary_isolation").reasons == ("foreign_canary_marker_present",)  # type: ignore[attr-defined]


def test_batch_rejects_a_candidate_copied_from_an_earlier_one(tmp_path: Path) -> None:
    screener = _screener(tmp_path)
    original = _candidate("orig-1", FRESH_SOLUTION, statement="Find the busiest window.")
    duplicate = _candidate("dup-1", FRESH_SOLUTION, statement="Find the busiest window.")
    first, second = screener.screen_batch([original, duplicate])
    assert first.decision is ScreeningDecision.READY_FOR_EXECUTABLE_ADMISSION
    assert second.decision is ScreeningDecision.REJECTED


def test_batch_rejects_duplicate_candidate_ids(tmp_path: Path) -> None:
    screener = _screener(tmp_path)
    with pytest.raises(ValueError, match="unique"):
        screener.screen_batch(
            [_candidate("same", FRESH_SOLUTION), _candidate("same", FRESH_SOLUTION)]
        )


def test_index_and_policy_ngram_must_match(tmp_path: Path) -> None:
    index = ReferenceIndex(ngram=6)
    with pytest.raises(ValueError, match="n-gram size must match"):
        CandidateScreener(
            index=index,
            policy=_policy(),
            canary_secret=SECRET,
            split_secret=OTHER_SECRET,
            corpus_digest="sha256:" + "0" * 64,
        )


def test_related_candidate_inherits_its_registered_family_cluster(tmp_path: Path) -> None:
    registry = {"seen/merge_intervals.py": "legacy-family-7"}
    screener = _screener(
        tmp_path,
        policy=_policy(document_threshold_bp=9000, union_threshold_bp=9500),
        family_registry=registry,
    )
    # A related variant: two lines changed, so overlap is high enough to link but not to reject.
    variant_solution = SEEN_SOLUTION.replace(
        "ordered = sorted(intervals, key=lambda pair: pair[0])", "ordered = sorted(intervals)"
    ).replace("merged.append([start, end])", "merged.append([start, end, 'w'])")
    variant = _candidate(
        "variant-1",
        variant_solution,
        statement="A slightly different merge.",
    )
    (report,) = screener.screen_batch([variant])
    assert report.decision is ScreeningDecision.READY_FOR_EXECUTABLE_ADMISSION
    assert report.cluster_id == "legacy-family-7"


# ---- CLI --------------------------------------------------------------------------------------


def test_cli_screen_writes_reports_and_never_admits(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    corpus = tmp_path / "seen"
    corpus.mkdir()
    (corpus / "merge_intervals.py").write_text(SEEN_SOLUTION, encoding="utf-8")
    for name in ("canary.secret", "split.secret"):
        assert taskgen_main(["new-secret", "--output", str(tmp_path / name)]) == 0
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "policy_id": "cli-test",
                "split_ratios": {
                    "public_development_bp": 3000,
                    "public_validation_bp": 1000,
                    "private_heldout_bp": 6000,
                },
            }
        ),
        encoding="utf-8",
    )
    candidates = [
        _candidate("copy", SEEN_SOLUTION).model_dump(mode="json"),
        _candidate("fresh", FRESH_SOLUTION, statement="Find the busiest window.").model_dump(
            mode="json"
        ),
    ]
    candidates_path = tmp_path / "candidates.json"
    candidates_path.write_text(json.dumps(candidates), encoding="utf-8")
    output = tmp_path / "reports.json"
    code = taskgen_main([
        "screen",
        "--candidates", str(candidates_path),
        "--policy", str(policy_path),
        "--reference-root", f"seen={corpus}",
        "--canary-secret-file", str(tmp_path / "canary.secret"),
        "--split-secret-file", str(tmp_path / "split.secret"),
        "--output", str(output),
    ])  # fmt: skip
    assert code == 0
    reports = {
        item["candidate_id"]: item for item in json.loads(output.read_text(encoding="utf-8"))
    }
    assert reports["copy"]["decision"] == "rejected"
    assert reports["fresh"]["decision"] == "ready_for_executable_admission"
    assert "admitted" not in json.dumps(reports)
    assert "secret" not in capsys.readouterr().out


def test_cli_secret_files_refuse_overwrite_and_weak_secrets(tmp_path: Path) -> None:
    target = tmp_path / "s.secret"
    assert taskgen_main(["new-secret", "--output", str(target)]) == 0
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        taskgen_main(["new-secret", "--output", str(target)])
    weak = tmp_path / "weak.secret"
    weak.write_text("abcd\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="at least 32 bytes"):
        taskgen_main(
            ["canary", "--secret-file", str(weak), "--family-id", "f", "--candidate-id", "c"]
        )


def test_cli_exposure_check_exit_codes(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.json"
    ledger.write_text(
        json.dumps(
            [
                {
                    "task_id": "task-z",
                    "audience": "evaluated_model",
                    "recipient_id": "provider:model-a",
                    "occurred_at": "2026-09-20T00:00:00+00:00",
                    "evidence_digest": None,
                }
            ]
        ),
        encoding="utf-8",
    )
    base = [
        "exposure-check", "--ledger", str(ledger), "--task-id", "task-z",
        "--cutoff", "2026-08-01T00:00:00+00:00", "--cutoff-confidence", "verified",
        "--evaluation-at", "2026-10-08T00:00:00+00:00",
    ]  # fmt: skip
    assert taskgen_main([*base, "--recipient", "provider:model-b"]) == 0
    assert taskgen_main([*base, "--recipient", "provider:model-a"]) == 3


def test_cli_round_commit_and_verify(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    secret = tmp_path / "round.secret"
    assert taskgen_main(["new-secret", "--output", str(secret)]) == 0
    assert taskgen_main(
        ["round-commit", "--round-id", "round-9", "--secret-file", str(secret)]
    ) == 0  # fmt: skip
    commitment = tmp_path / "commitment.json"
    commitment.write_text(capsys.readouterr().out, encoding="utf-8")
    verify = ["round-verify", "--commitment", str(commitment)]
    assert taskgen_main([*verify, "--secret-file", str(secret)]) == 0
    other = tmp_path / "other.secret"
    assert taskgen_main(["new-secret", "--output", str(other)]) == 0
    assert taskgen_main([*verify, "--secret-file", str(other)]) == 4
