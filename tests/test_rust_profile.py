"""PCB-11-3: Rust diagnostic and idiom profiles and evidence ownership.

"DoD: clone/unwrap/unsafe tokens are not automatically violations; required behavior and actual
contextual evidence determine findings."

Context verdicts come from the real scanner (``pcb_rust_scan``) run on Rust source, so these tests
exercise the contract between what the scanner reports and what the profile counts.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from polycodebench_core.models import (
    Confidence,
    MeasurementStatus,
    Observation,
    ScoreDimension,
    SourceLocation,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "languages" / "rust" / "src"))

from polycodebench_lang_rust.guest import pcb_rust_scan as scanner  # noqa: E402
from polycodebench_lang_rust.observations import slug  # noqa: E402
from polycodebench_lang_rust.profile import load_profile  # noqa: E402

profile = load_profile()
MEASURED = MeasurementStatus.MEASURED
DIGEST = "sha256:" + "0" * 64
FULL = {
    "ownership_borrowing": 4,
    "unsafe_soundness": 2,
    "result_option": 4,
    "iterators_traits": 3,
    "concurrency": 2,
    "clippy": 5,
    "ownership_borrowing_api": 4,
    "iterator_trait_composition": 3,
    "result_option_modeling": 4,
    "concurrency_abstraction": 2,
}
_STATUS = {
    "violation": MEASURED,
    "benign_in_context": MeasurementStatus.NOT_APPLICABLE,
    "hint": MeasurementStatus.NEEDS_REVIEW,
}


def obs(
    check_id: str,
    line: int = 10,
    *,
    path: str = "src/lib.rs",
    status: MeasurementStatus = MEASURED,
    severity: str | None = "medium",
    confidence: Confidence | None = Confidence.MEDIUM,
) -> Observation:
    measured = status == MEASURED
    return Observation(
        schema_version=1,
        kind="observation",
        check_id=check_id,
        tool_digest=DIGEST,
        candidate_digest=DIGEST,
        status=status,
        value=True if measured else None,
        severity=severity if measured else None,  # type: ignore[arg-type]
        confidence=confidence if measured else None,
        location=SourceLocation(
            schema_version=1,
            kind="source_location",
            path=path,
            start_line=line,
            end_line=line,
            start_column=None,
            end_column=None,
            base_or_candidate_digest=DIGEST,
        ),
        baseline_relation=None,
        issue_key=profile.key_for(check_id, path, line),
        primary_owner=profile.owner(check_id),
        raw_artifact_ids=[],
        explanation="t",
    )


def scan_obs(tool: str, status: MeasurementStatus = MEASURED) -> Observation:
    return Observation(
        schema_version=1,
        kind="observation",
        check_id=f"rust.{tool}.scan",
        tool_digest=DIGEST,
        candidate_digest=DIGEST,
        status=status,
        value=0 if status == MEASURED else None,
        severity=None,
        confidence=None,
        location=None,
        baseline_relation=None,
        issue_key=None,
        primary_owner=None,
        raw_artifact_ids=[],
        explanation="scan",
    )


def from_scanner(source: str, path: str = "src/lib.rs") -> list[Observation]:
    """Context observations exactly as the parser will build them from the real scanner."""
    results: list[Observation] = []
    for item in scanner.scan_text(path, textwrap.dedent(source)):
        check = f"rust.context.{slug(item['rule'])}"
        results.append(
            obs(
                check,
                item["line"],
                path=path,
                status=_STATUS[item["verdict"]],
            )
        )
    return results


def evaluate(
    observations: list[Observation], opportunities: dict[str, int] | None = None, **kw: Any
) -> dict[str, Any]:
    scans = [scan_obs(tool) for tool in ("context", "clippy")]
    result = profile.evaluate(
        opportunities=opportunities if opportunities is not None else FULL,
        observations=[*scans, *profile.normalize(observations)],
        required_tools=kw.get("required_tools", ("context", "clippy")),
    )
    return {item.item_id: item for item in (*result.diagnostic, *result.idioms)} | {
        "_result": result
    }


# ------------------------------------------------------------------------ the profile itself


def test_profile_loads_with_the_configured_weights_and_stays_pilot_only() -> None:
    document = profile.profile
    assert document.language_id == "rust"
    assert document.effective_for_scoring is False
    assert {i.item_id: i.weight_bp for i in document.diagnostic_items} == {
        "ownership_borrowing": 2500,
        "unsafe_soundness": 2000,
        "result_option": 2000,
        "iterators_traits": 1500,
        "concurrency": 1000,
        "clippy": 1000,
    }
    assert sum(i.weight_bp for i in document.idiom_items) == 10_000
    assert document.syntax_count_bonus is False
    assert document.duplicate_composite_penalty is False


def test_check_ids_keep_their_dots_so_rule_mappings_can_match() -> None:
    assert slug("rust.clippy.redundant-clone") == "rust.clippy.redundant-clone"
    assert slug("rust.clippy.Redundant_Clone") == "rust.clippy.redundant_clone"
    assert profile.resolve("rust.clippy.redundant-clone") is not None


def test_every_token_lint_is_context_required_and_has_a_scanner_family() -> None:
    token_lints = (
        "rust.clippy.unwrap-used",
        "rust.clippy.expect-used",
        "rust.clippy.clone-on-copy",
        "rust.clippy.implicit-clone",
        "rust.clippy.unsafe-code",
        "rust.clippy.undocumented-unsafe-blocks",
    )
    for check in token_lints:
        assert profile.is_token_only(check), check
    for check in ("rust.clippy.redundant-clone", "rust.clippy.question-mark"):
        assert not profile.is_token_only(check), check


# ------------------------------------- tokens are not violations; context decides (the DoD)


def test_clone_that_moves_an_owned_value_is_not_a_violation() -> None:
    findings = from_scanner(
        """
        pub fn store(name: &str) -> String {
            let owned = name.to_string();
            let copy = owned.clone();
            copy
        }
        """
    )
    assert findings and all(o.status != MEASURED for o in findings)
    assert evaluate(findings)["ownership_borrowing"].unique_violations == 0


def test_a_clone_that_is_provably_redundant_is_a_violation() -> None:
    findings = from_scanner(
        """
        pub fn first(v: &&String) -> String {
            v.clone()
        }
        """
    )
    result = evaluate(findings)
    assert result["ownership_borrowing"].unique_violations == 1
    assert result["ownership_borrowing_api"].unique_violations == 1
    assert result["ownership_borrowing"].score_bp == 7500


def test_guarded_unwrap_in_code_and_in_tests_is_not_a_violation() -> None:
    findings = from_scanner(
        """
        pub fn parse(s: &str) -> i32 {
            if let Ok(n) = s.parse::<i32>() {
                return n;
            }
            s.parse::<i32>().unwrap_or(0)
        }

        #[test]
        fn test_parse() {
            assert_eq!(parse("4").to_string().parse::<i32>().unwrap(), 4);
        }
        """
    )
    assert findings and all(o.status != MEASURED for o in findings)
    assert evaluate(findings)["result_option"].unique_violations == 0


def test_unguarded_unwrap_discarding_an_error_path_is_a_violation() -> None:
    findings = from_scanner(
        """
        pub fn parse(s: &str) -> i32 {
            let value = s.trim();
            value.parse::<i32>().unwrap()
        }
        """
    )
    result = evaluate(findings)
    assert result["result_option"].unique_violations == 1
    assert result["result_option_modeling"].unique_violations == 1


def test_unsafe_never_scores_by_itself() -> None:
    documented = from_scanner(
        """
        pub fn read(p: *const u8) -> u8 {
            // SAFETY: caller guarantees `p` is valid for reads.
            unsafe { *p }
        }
        """
    )
    unjustified = from_scanner(
        """
        pub fn read(p: *const u8) -> u8 {
            let q = p;
            unsafe { *q }
        }
        """
    )
    assert all(o.status != MEASURED for o in documented)
    # A missing justification is a hint that needs reviewer or judge evidence, not a penalty.
    hints = [o for o in unjustified if o.status == MeasurementStatus.NEEDS_REVIEW]
    assert hints and not any(o.status == MEASURED for o in unjustified)
    for findings in (documented, unjustified):
        assert evaluate(findings)["unsafe_soundness"].unique_violations == 0
        assert evaluate(findings)["unsafe_soundness"].score_bp == 10_000


def test_token_only_lint_without_context_evidence_is_not_counted() -> None:
    lints = [
        obs("rust.clippy.unwrap-used", 12),
        obs("rust.clippy.expect-used", 20),
        obs("rust.clippy.clone-on-copy", 30),
        obs("rust.clippy.undocumented-unsafe-blocks", 40),
    ]
    normalized = profile.normalize(lints)
    assert [o.status for o in normalized] == [MeasurementStatus.NEEDS_REVIEW] * 4
    assert all(o.value is None and o.severity is None for o in normalized)
    result = evaluate(lints)
    assert result["result_option"].unique_violations == 0
    assert result["ownership_borrowing"].unique_violations == 0
    assert result["unsafe_soundness"].unique_violations == 0


def test_evaluate_ignores_a_token_lint_even_if_the_caller_skipped_normalisation() -> None:
    raw = [scan_obs("context"), scan_obs("clippy"), obs("rust.clippy.unwrap-used", 12)]
    result = profile.evaluate(
        opportunities=FULL, observations=raw, required_tools=("context", "clippy")
    )
    item = {i.item_id: i for i in result.diagnostic}["result_option"]
    assert item.unique_violations == 0


def test_scanner_confirmation_makes_the_token_lint_count_once() -> None:
    source = """
    pub fn parse(s: &str) -> i32 {
        s.parse::<i32>().unwrap()
    }
    """
    context = from_scanner(source)
    line = next(o.location.start_line for o in context if o.status == MEASURED)  # type: ignore[union-attr]
    lint = obs("rust.clippy.unwrap-used", line)
    assert lint.issue_key == context[0].issue_key
    result = evaluate([*context, lint])
    assert result["result_option"].unique_violations == 1
    assert result["result_option"].score_bp == 7500


def test_scanner_benign_verdict_overrides_a_token_lint_on_the_same_site() -> None:
    context = from_scanner(
        """
        pub fn parse(s: &str) -> i32 {
            s.parse::<i32>().unwrap_or_default()
        }
        """
    )
    line = context[0].location.start_line  # type: ignore[union-attr]
    lint = obs("rust.clippy.unwrap-used", line)
    normalized = profile.normalize([*context, lint])
    assert len(normalized) == 1 and normalized[0].status == MeasurementStatus.NOT_APPLICABLE
    assert evaluate([*context, lint])["result_option"].unique_violations == 0


def test_a_precise_lint_is_not_cancelled_by_a_heuristic_benign_scanner_verdict() -> None:
    """``redundant_clone`` is a compiler-level fact; the scanner saying "no pattern seen" is not."""
    benign = obs("rust.context.clone-contextual", 10, status=MeasurementStatus.NOT_APPLICABLE)
    precise = obs("rust.clippy.redundant-clone", 10)
    assert benign.issue_key == precise.issue_key
    merged = profile.normalize([benign, precise])
    assert len(merged) == 1 and merged[0].status == MEASURED
    assert evaluate([benign, precise])["ownership_borrowing"].unique_violations == 1


# ----------------------------------------------------- required behaviour decides applicability


@pytest.mark.parametrize(
    ("source", "item"),
    [
        ("pub fn f(v: &&String) -> String { v.clone() }", "ownership_borrowing"),
        ("pub fn f(s: &str) -> i32 { s.parse::<i32>().unwrap() }", "result_option"),
    ],
)
def test_no_frozen_opportunity_means_not_applicable_never_perfect(source: str, item: str) -> None:
    findings = from_scanner(source)
    assert any(o.status == MEASURED for o in findings)
    result = evaluate(findings, {**FULL, item: 0})
    assert result[item].status == "not_applicable"
    assert result[item].score_bp is None
    assert result["_result"].diagnostic_score_bp is not None


def test_unsafe_findings_count_only_when_required_behaviour_involves_unsafe() -> None:
    ub = obs("rust.miri.candidate-ub", 8)
    with_unsafe = evaluate([ub], {**FULL, "unsafe_soundness": 1})
    without_unsafe = evaluate([ub], {**FULL, "unsafe_soundness": 0})
    assert with_unsafe["unsafe_soundness"].unique_violations == 1
    assert with_unsafe["unsafe_soundness"].score_bp == 0
    assert without_unsafe["unsafe_soundness"].status == "not_applicable"


def test_duplicate_reports_and_syntax_volume_cannot_change_a_score() -> None:
    once = [obs("rust.clippy.redundant-clone", 10)]
    twice = [
        obs("rust.clippy.redundant-clone", 10),
        obs("rust.context.clone-redundant", 10),
        obs("rust.clippy.clone-on-copy", 10),
    ]
    assert (
        evaluate(once)["ownership_borrowing"].score_bp
        == evaluate(twice)["ownership_borrowing"].score_bp
        == 7500
    )
    # Hundreds of benign clones/unwraps in tests and owned-API sites change nothing either.
    noise = [
        obs(f"rust.context.{rule}", n, status=MeasurementStatus.NOT_APPLICABLE)
        for n in range(1, 60)
        for rule in ("clone-contextual", "unwrap-guarded")
    ]
    result = evaluate(noise)
    assert result["ownership_borrowing"].score_bp == 10_000
    assert result["result_option"].score_bp == 10_000


def test_more_violations_than_opportunities_floor_at_zero_never_negative() -> None:
    many = [obs("rust.clippy.redundant-clone", n) for n in range(1, 20)]
    item = evaluate(many)["ownership_borrowing"]
    assert item.unique_violations == 19 and item.score_bp == 0


# --------------------------------------------------------- evidence ownership and Miri states


def test_evidence_ownership_has_one_owner_per_issue() -> None:
    assert profile.owner("rust.miri.candidate-ub") == ScoreDimension.ROBUSTNESS
    assert profile.owner("rust.dependency.rustsec-2024-0001") == ScoreDimension.SECURITY
    assert profile.owner("rust.clippy.redundant-clone") is None
    # An advisory feeds the composite owner only; it creates no diagnostic item violation.
    advisory = obs("rust.dependency.rustsec-2024-0001", 1, path="Cargo.lock")
    result = evaluate([advisory])
    assert all(
        result[name].unique_violations == 0
        for name in result
        if name != "_result" and result[name].status == "measured"
    )


def test_miri_ub_unsupported_and_clean_are_three_different_states() -> None:
    opportunities = {**FULL, "unsafe_soundness": 1}
    base = [scan_obs("context"), scan_obs("clippy")]

    def run(miri: list[Observation], required: tuple[str, ...]):  # type: ignore[no-untyped-def]
        return profile.evaluate(
            opportunities=opportunities,
            observations=[*base, *miri],
            required_tools=required,
        )

    def unsafe_item(result):  # type: ignore[no-untyped-def]
        return {i.item_id: i for i in result.diagnostic}["unsafe_soundness"]

    clean = unsafe_item(run([scan_obs("miri")], ("context", "clippy", "miri")))
    ub = unsafe_item(
        run(
            [scan_obs("miri"), obs("rust.miri.candidate-ub", 9)],
            ("context", "clippy", "miri"),
        )
    )
    unsupported_required = unsafe_item(
        run(
            [scan_obs("miri", MeasurementStatus.NOT_APPLICABLE)],
            ("context", "clippy", "miri"),
        )
    )
    unsupported_optional = unsafe_item(
        run([scan_obs("miri", MeasurementStatus.NOT_APPLICABLE)], ("context", "clippy"))
    )
    crashed = unsafe_item(
        run([scan_obs("miri", MeasurementStatus.MISSING)], ("context", "clippy", "miri"))
    )
    assert (clean.status, clean.score_bp) == ("measured", 10_000)
    assert (ub.status, ub.score_bp, ub.unique_violations) == ("measured", 0, 1)
    # A task that requires Miri cannot be scored from a scan that could not run it.
    assert unsupported_required.status == "missing"
    assert unsupported_required.reasons == ("required scan unsupported: miri",)
    assert crashed.status == "missing"
    assert crashed.reasons == ("required scan incomplete: miri",)
    assert unsupported_required.reasons != crashed.reasons
    # A task for which Miri is not applicable is scored on the remaining evidence.
    assert unsupported_optional.status == "measured"


def test_a_missing_required_context_scan_makes_dependent_items_missing_not_clean() -> None:
    result = profile.evaluate(
        opportunities=FULL,
        observations=[scan_obs("clippy"), scan_obs("context", MeasurementStatus.MISSING)],
        required_tools=("context", "clippy"),
    )
    items = {i.item_id: i for i in (*result.diagnostic, *result.idioms)}
    for name in ("ownership_borrowing", "result_option", "iterators_traits"):
        assert items[name].status == "missing", name
        assert items[name].reasons == ("required scan incomplete: context",)
    assert result.complete is False
    assert result.diagnostic_score_bp is None


def test_profile_scores_unique_issues_against_frozen_opportunities() -> None:
    observations = [
        obs("rust.context.unwrap-unguarded", 5),
        obs("rust.context.unwrap-unguarded", 9),
        obs("rust.context.unwrap-unguarded", 9),
        obs("rust.clippy.question-mark", 14),
    ]
    item = evaluate(observations)["result_option"]
    assert item.unique_violations == 3
    assert item.score_bp == 2500
    assert item.issue_keys == tuple(sorted(item.issue_keys))
