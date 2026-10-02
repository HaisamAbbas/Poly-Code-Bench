"""PCB-21-2: C++ diagnostic and idiom profiles, applicability and evidence ownership.

"DoD: `new`/`delete`, a raw `T*` parameter, a pass-by-value container and `std::move` are
*tokens*, not findings. A mapping whose ``context_evaluator`` is ``context-required`` is counted
only when the context scanner independently confirms the same site; otherwise it is demoted to
``needs_review`` and costs nothing."

Context verdicts come from the real scanner (``pcb_cpp_scan``) run on C++ source, so these tests
exercise the contract between what the scanner reports and what the profile counts.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml
from cpp_plugin_support import frozen
from polycodebench_lang_cpp import CppLanguagePlugin
from polycodebench_plugins_api import (
    EXECUTION_RECORD_PATH,
    AnalysisContext,
    AnalysisPlan,
    DictArtifactReader,
)
from polycodebench_plugins_api.results import make_record
from polycodebench_core.models import (
    Confidence,
    MeasurementStatus,
    Observation,
    ScoreDimension,
    SourceLocation,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "languages" / "cpp" / "src"))

from polycodebench_lang_cpp.guest import pcb_cpp_scan as scanner  # noqa: E402
from polycodebench_lang_cpp.observations import COLUMN_KEYED_FAMILIES, issue_key, slug  # noqa: E402
from polycodebench_lang_cpp import parsers  # noqa: E402
from polycodebench_lang_cpp.profile import PROFILE_VERSION, load_profile  # noqa: E402

profile = load_profile()
MEASURED = MeasurementStatus.MEASURED
DIGEST = "sha256:" + "0" * 64
FULL = {
    "raii_ownership": 2,
    "moves_copies": 2,
    "stl_use": 3,
    "exception_safety": 1,
    "modern_features": 2,
    "abstraction_performance": 2,
    "undefined_behavior_memory_safety": 2,
    "ownership_raii_design": 2,
    "stl_container_choice": 2,
    "value_move_api_semantics": 2,
    "modern_constructs": 1,
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
    path: str = "src/top_words.cpp",
    status: MeasurementStatus = MEASURED,
    severity: str | None = "medium",
    confidence: Confidence | None = Confidence.MEDIUM,
    column: int | None = None,
) -> Observation:
    measured = status == MeasurementStatus.MEASURED
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
            start_column=column,
            end_column=None,
            base_or_candidate_digest=DIGEST,
        ),
        baseline_relation=None,
        issue_key=profile.key_for(check_id, path, line, column),
        primary_owner=profile.owner(check_id),
        raw_artifact_ids=[],
        explanation=f"{check_id} reported at line {line}",
    )


def scan_obs(tool: str, status: MeasurementStatus = MEASURED) -> Observation:
    return Observation(
        schema_version=1,
        kind="observation",
        check_id=f"cpp.{tool}.scan",
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


def from_scanner(source: str, path: str = "src/top_words.cpp") -> list[Observation]:
    """Context observations exactly as the parser will build them from the real scanner."""
    results: list[Observation] = []
    for item in scanner.scan_text(path, textwrap.dedent(source)):
        check = f"cpp.context.{slug(str(item['rule']))}"
        results.append(
            obs(
                check,
                item["line"],
                path=path,
                status=_STATUS[item["verdict"]],
                column=item.get("column") or None,
            ).model_copy(update={"explanation": str(item["message"])[:300]})
        )
    return results


def evaluate(
    observations: list[Observation], opportunities: dict[str, int] | None = None, **kw: Any
) -> dict[str, Any]:
    scans = [scan_obs(tool) for tool in ("context", "clang_tidy", "cppcheck")]
    result = profile.evaluate(
        opportunities=opportunities if opportunities is not None else FULL,
        observations=[*scans, *profile.normalize(observations)],
        required_tools=kw.get("required_tools", ("context", "clang_tidy", "cppcheck")),
    )
    return {item.item_id: item for item in (*result.diagnostic, *result.idioms)} | {
        "_result": result
    }


def _lint_plan() -> AnalysisPlan:
    """A real clang-tidy plan built from the pinned lock, scoped to the candidate source."""
    plugin = CppLanguagePlugin()
    (plan,) = [
        p
        for p in plugin.analysis_plans(
            AnalysisContext(
                task=frozen(plugin),
                candidate_digest="sha256:" + "2" * 64,
                candidate_paths=("src/top_words.cpp", "include/top_words.hpp"),
            )
        )
        if p.analyzer_id == "clang_tidy"
    ]
    return plan


def _raw(out_err: str, out_out: str) -> DictArtifactReader:
    """Recorded tool output for a run that started, exited 0 and captured both streams."""
    record = make_record(
        _lint_plan(),
        exit_code=0,
        timed_out=False,
        duration_ms=5,
        stdout=out_out.encode(),
        stderr=out_err.encode(),
        isolation_tier="development",
        sandbox_id="recorded-cpp-lint",
    )
    return DictArtifactReader(
        {
            EXECUTION_RECORD_PATH: record.model_dump_json().encode(),
            "out/clang_tidy.run.json": b"{}",
            "out/clang_tidy.err": out_err.encode(),
            "out/clang_tidy.out": out_out.encode(),
        }
    )


# ------------------------------------------------------------------------ the profile itself


def test_profile_loads_with_the_configured_weights_and_stays_pilot_only() -> None:
    document = profile.profile
    assert document.language_id == "cpp"
    assert document.profile_version == PROFILE_VERSION
    assert document.effective_for_scoring is False
    assert {i.item_id: i.weight_bp for i in document.diagnostic_items} == {
        "raii_ownership": 2500,
        "moves_copies": 1500,
        "stl_use": 1500,
        "exception_safety": 1000,
        "modern_features": 1500,
        "abstraction_performance": 1000,
        "undefined_behavior_memory_safety": 1000,
    }
    assert {i.item_id: i.weight_bp for i in document.idiom_items} == {
        "ownership_raii_design": 3500,
        "stl_container_choice": 2500,
        "value_move_api_semantics": 2000,
        "modern_constructs": 2000,
    }
    assert sum(i.weight_bp for i in document.diagnostic_items) == 10_000
    assert sum(i.weight_bp for i in document.idiom_items) == 10_000
    assert document.syntax_count_bonus is False
    assert document.duplicate_composite_penalty is False


def test_the_cpp_weights_are_the_cpp_section_of_the_published_table() -> None:
    """The C++ profile is scored on ``profiles.cpp``, never on ``profiles.c``.

    Both sections live in the same file and name the same tools, so a C++ weight drifting onto the
    C rubric is a silent scoring corruption; this asserts the published source of truth, not a copy.
    """
    published = yaml.safe_load((ROOT / "config/languages/profiles-v1.yaml").read_text("utf-8"))
    section = published["profiles"]["cpp"]
    assert profile.profile.language_id == "cpp"
    assert {i.item_id: i.weight_bp for i in profile.profile.diagnostic_items} == {
        name: int(round(float(percent) * 100))
        for name, percent in section["diagnostic_percent"].items()
    }
    assert {i.item_id: i.weight_bp for i in profile.profile.idiom_items} == {
        name: int(round(float(percent) * 100))
        for name, percent in section["idiomatic_percent"].items()
    }
    document = yaml.safe_load(
        (ROOT / "config/languages/cpp-profile-v1.yaml").read_text("utf-8")
    )
    assert document["weights_source"] == "config/languages/profiles-v1.yaml#profiles.cpp"


def test_the_item_ids_are_the_cpp_set_and_not_the_c_ones() -> None:
    """C and C++ share a toolchain and a ``tools`` list; only the item ids tell them apart."""
    published = yaml.safe_load((ROOT / "config/languages/profiles-v1.yaml").read_text("utf-8"))
    c_section, cpp_section = published["profiles"]["c"], published["profiles"]["cpp"]
    diagnostic = {i.item_id for i in profile.profile.diagnostic_items}
    idiomatic = {i.item_id for i in profile.profile.idiom_items}
    assert diagnostic == set(cpp_section["diagnostic_percent"])
    assert idiomatic == set(cpp_section["idiomatic_percent"])
    assert diagnostic.isdisjoint(c_section["diagnostic_percent"])
    assert idiomatic.isdisjoint(c_section["idiomatic_percent"])
    # The C items this profile must never emit: manual free, C const discipline, cache effects.
    assert not diagnostic & {"memory_safety", "undefined_behavior", "performance_cache"}
    assert not idiomatic & {"ownership_api_contracts", "const_type_portability"}


def test_a_cppcheck_id_is_slugged_into_the_namespaced_form_the_mappings_match() -> None:
    """cppcheck spells its ids camelCase; the parser namespaces and slugs each tool's own id."""
    assert slug("ArrayIndexOutOfBounds") == "arrayindexoutofbounds"
    # The parser builds `cpp.<tool>.<slug>`, and that is the form the mappings are written in.
    assert f"cpp.cppcheck.{slug('ArrayIndexOutOfBounds')}" == "cpp.cppcheck.arrayindexoutofbounds"
    assert f"cpp.clang_tidy.{slug('cppcoreguidelines-owning-memory')}" == (
        "cpp.clang_tidy.cppcoreguidelines-owning-memory"
    )
    # A cppcheck id resolves through the catch-all `cpp.cppcheck.` prefix mapping, which is
    # how an unmapped cppcheck id still counts against the hygiene item rather than vanishing.
    assert profile.resolve("cpp.cppcheck.ArrayIndexOutOfBounds").check_prefix == "cpp.cppcheck."
    # And the sanitizer ids the report parser builds are in the same namespace.
    assert profile.resolve("cpp.asan.candidate-defect") is not None
    assert profile.resolve("cpp.tsan.candidate-defect") is not None


def test_only_the_two_bare_token_lints_need_context_confirmation() -> None:
    """`context-required` is a small, named set; widening it silently changes what is counted."""
    token_only = {
        m.check_id
        for m in profile.profile.rule_mappings
        if m.context_evaluator == "context-required"
    }
    assert token_only == {
        "cpp.clang_tidy.cppcoreguidelines-owning-memory",
        "cpp.clang_tidy.performance-no-automatic-move",
    }
    # A precise, compiler-level fact stays precise: it needs no scanner confirmation.
    for check in (
        "cpp.clang_tidy.cppcoreguidelines-special-member-functions",
        "cpp.clang_tidy.performance-unnecessary-value-param",
        "cpp.clang_tidy.bugprone-use-after-move",
        "cpp.context.raw-owning-pointer",
    ):
        assert not profile.is_token_only(check), check


# ------------------------------------- tokens are not violations; context decides (the DoD)


def test_a_new_with_no_releaser_is_a_violation_the_scanner_confirms() -> None:
    findings = from_scanner(
        """
        std::string* copy_of(const std::string& text)
        {
            std::string* copy = new std::string(text);
            return copy;
        }
        """
    )
    owning = [o for o in findings if o.check_id == "cpp.context.raw-owning-pointer"]
    assert len(owning) == 1 and owning[0].status == MEASURED
    result = evaluate(findings)
    assert result["raii_ownership"].unique_violations == 1
    assert result["ownership_raii_design"].unique_violations == 1
    assert result["raii_ownership"].score_bp == 5000  # one of two frozen opportunities


def test_a_non_owning_raw_pointer_costs_nothing() -> None:
    """A `const char*` a callee reads but never frees is legal C++, so it is evidence, not a fault."""
    findings = from_scanner(
        """
        class TextView
        {
        public:
            explicit TextView(const char* data, std::size_t size);
        };
        """
    )
    assert [o.check_id for o in findings] == ["cpp.context.raw-pointer-nonowning"]
    verdict = findings[0]
    assert verdict.status == MeasurementStatus.NOT_APPLICABLE and verdict.value is None
    assert verdict.severity is None and verdict.confidence is None
    assert "never a violation" in (verdict.explanation or "")
    result = evaluate(findings)
    assert result["raii_ownership"].unique_violations == 0
    assert result["raii_ownership"].score_bp == 10_000
    assert "never a violation" in (findings[0].explanation or "")


def test_a_moved_const_binding_is_a_real_copy_the_scanner_measures() -> None:
    """`std::move` on a `const T&` cannot move, so the copy happens: the token is a fact here."""
    findings = from_scanner(
        """
        void take(std::string value) { (void)value; }

        void forward(const std::string& word)
        {
            take(std::move(word));
        }
        """
    )
    moved = [o for o in findings if o.check_id == "cpp.context.move-on-const"]
    by_value = [o for o in findings if o.check_id == "cpp.context.pass-by-value-container"]
    assert moved and all(o.status == MEASURED for o in moved)
    # Two independent faults in one snippet: the callee's by-value parameter and the caller's
    # move of a const binding. Different families, so they are two issues, not one.
    assert len(by_value) == 1
    assert {o.check_id for o in findings} == {
        "cpp.context.move-on-const",
        "cpp.context.pass-by-value-container",
    }
    result = evaluate(findings)
    assert result["moves_copies"].unique_violations == 2
    assert result["moves_copies"].score_bp == 0  # two frozen opportunities, two faults


def test_a_pass_by_value_container_is_a_violation_the_scanner_confirms() -> None:
    findings = from_scanner(
        """
        std::size_t letter_run_length(std::string word)
        {
            return word.size();
        }
        """
    )
    by_value = [o for o in findings if o.check_id == "cpp.context.pass-by-value-container"]
    assert by_value and by_value[0].status == MEASURED
    result = evaluate(findings)
    assert result["moves_copies"].unique_violations == 1
    assert result["moves_copies"].score_bp == 5000


def test_a_precise_copy_lint_alone_feeds_both_the_diagnostic_and_the_idiom_item() -> None:
    """clang-tidy's by-value lint is a compiler-level fact, so it needs no scanner confirmation."""
    site = 12
    lint = obs("cpp.clang_tidy.performance-unnecessary-value-param", site)
    result = evaluate([lint])
    assert result["moves_copies"].unique_violations == 1
    assert result["value_move_api_semantics"].unique_violations == 1
    assert result["value_move_api_semantics"].score_bp == 5000
    # And the scanner agreeing at the same site adds no second deduction: one key, one finding.
    context = [obs("cpp.context.pass-by-value-container", site)]
    assert context[0].issue_key == lint.issue_key
    agreed = evaluate([lint, *context])
    assert agreed["moves_copies"].unique_violations == 1
    assert agreed["moves_copies"].score_bp == 5000
    # The scanner's own finding does *not* feed the idiom item; only the precise lint does.
    assert evaluate(context)["value_move_api_semantics"].unique_violations == 0


def test_a_token_only_lint_without_context_evidence_is_demoted_and_not_counted() -> None:
    """The DoD: `cppcoreguidelines-owning-memory` alone proves nothing and is not scored."""
    lints = [
        obs("cpp.clang_tidy.cppcoreguidelines-owning-memory", 30, column=12),
        obs("cpp.clang_tidy.performance-no-automatic-move", 44),
    ]
    normalized = profile.normalize(lints)
    assert [o.status for o in normalized] == [MeasurementStatus.NEEDS_REVIEW] * 2
    assert all(o.value is None and o.severity is None and o.confidence is None for o in normalized)
    assert "not counted" in (normalized[0].explanation or "")
    result = evaluate(lints)
    assert result["raii_ownership"].unique_violations == 0
    assert result["raii_ownership"].score_bp == 10_000
    assert result["ownership_raii_design"].score_bp == 10_000
    assert result["moves_copies"].score_bp == 10_000


def test_the_same_lint_is_counted_once_the_scanner_confirms_the_same_site() -> None:
    context = from_scanner(
        """
        std::string* copy_of(const std::string& text)
        {
            std::string* copy = new std::string(text);
            return copy;
        }
        """
    )
    confirmed = next(o for o in context if o.check_id == "cpp.context.raw-owning-pointer")
    site = confirmed.location
    assert site is not None
    lint = obs(
        "cpp.clang_tidy.cppcoreguidelines-owning-memory",
        site.start_line,
        column=site.start_column,
    )
    # One defect, one key: the lint and the scanner witness meet on the same site.
    assert lint.issue_key == confirmed.issue_key
    result = evaluate([*context, lint])
    assert result["raii_ownership"].unique_violations == 1
    assert result["raii_ownership"].score_bp == 5000
    assert result["ownership_raii_design"].unique_violations == 1


def test_evaluate_ignores_a_token_lint_even_if_the_caller_skipped_normalisation() -> None:
    raw = [
        scan_obs("context"),
        scan_obs("clang_tidy"),
        scan_obs("cppcheck"),
        obs("cpp.clang_tidy.cppcoreguidelines-owning-memory", 30),
    ]
    result = profile.evaluate(
        opportunities=FULL, observations=raw, required_tools=("context", "clang_tidy", "cppcheck")
    )
    item = {i.item_id: i for i in result.diagnostic}["raii_ownership"]
    assert item.unique_violations == 0 and item.score_bp == 10_000


def test_a_token_lint_at_a_site_the_scanner_called_benign_costs_nothing() -> None:
    """The scanner's `benign_in_context` verdict outranks the bare token on the same site."""
    benign = obs(
        "cpp.context.raw-pointer-nonowning", 30, status=MeasurementStatus.NOT_APPLICABLE
    )
    lint = obs("cpp.clang_tidy.cppcoreguidelines-owning-memory", 30)
    # `raw-pointer` and `manual-ownership` are different families, so force the meeting site.
    lint = lint.model_copy(update={"issue_key": benign.issue_key})
    normalized = profile.normalize([benign, lint])
    assert len(normalized) == 1 and normalized[0].status == MeasurementStatus.NOT_APPLICABLE
    assert evaluate([benign, lint])["raii_ownership"].unique_violations == 0


# ------------------------------------------------------ one defect is one issue key


def test_two_tools_reporting_one_site_collapse_to_one_issue_and_one_deduction() -> None:
    """`cpp.context.raw-owning-pointer` and `cppcoreguidelines-owning-memory` are one defect."""
    once = [obs("cpp.context.raw-owning-pointer", 30, column=12)]
    twice = [
        *once,
        obs("cpp.clang_tidy.cppcoreguidelines-owning-memory", 30, column=12),
    ]
    single = evaluate(once)
    merged = evaluate(twice)
    assert single["raii_ownership"].unique_violations == 1
    assert merged["raii_ownership"].unique_violations == 1
    assert merged["raii_ownership"].score_bp == single["raii_ownership"].score_bp == 5000
    assert merged["ownership_raii_design"].unique_violations == 1
    # The token-only lint is deliberately *not* named in the survivor's note: it is the thing
    # being demoted, not a second witness.
    normalized = profile.normalize(twice)
    assert len(normalized) == 1
    assert normalized[0].check_id == "cpp.context.raw-owning-pointer"
    assert "cppcoreguidelines-owning-memory" not in (normalized[0].explanation or "")


def test_a_runtime_leak_and_a_static_owning_pointer_at_one_site_are_one_issue() -> None:
    """A leak and a hand-allocated pointer are the same fault seen by two different tools."""
    static_finding = obs("cpp.context.raw-owning-pointer", 44, column=20)
    runtime_finding = obs("cpp.asan.resource-leak", 44, column=20)
    assert static_finding.issue_key == runtime_finding.issue_key
    result = evaluate([static_finding, runtime_finding])
    assert result["raii_ownership"].unique_violations == 1
    assert result["undefined_behavior_memory_safety"].unique_violations == 0
    # One canonical issue; the surviving representative is the surviving check id, and the
    # collapsed one is named so a reviewer can still see which tools agreed.
    normalized = profile.normalize([static_finding, runtime_finding])
    assert len(normalized) == 1
    assert normalized[0].check_id in {
        "cpp.context.raw-owning-pointer",
        "cpp.asan.resource-leak",
    }
    assert "cpp.asan.resource-leak" in (normalized[0].explanation or "")


def test_two_ownership_faults_on_one_line_are_two_issues_not_one() -> None:
    """`manual-ownership` is column-keyed on purpose: one C++ line can carry two faults."""
    assert "manual-ownership" in COLUMN_KEYED_FAMILIES
    first = obs("cpp.context.raw-owning-pointer", 30, column=12)
    second = obs("cpp.context.raw-owning-pointer", 30, column=40)
    assert first.issue_key != second.issue_key
    assert evaluate([first, second])["raii_ownership"].unique_violations == 2
    assert evaluate([first, second])["raii_ownership"].score_bp == 0


def test_duplicate_reports_of_the_same_rule_on_the_same_site_cannot_change_a_score() -> None:
    once = [obs("cpp.context.pass-by-value-container", 12)]
    thrice = [*once, obs("cpp.context.pass-by-value-container", 12), obs("cpp.cppcheck.passedbyvalue", 12)]
    assert (
        evaluate(once)["moves_copies"].score_bp
        == evaluate(thrice)["moves_copies"].score_bp
        == 5000
    )


def test_the_profile_passes_a_column_only_for_the_families_that_need_one() -> None:
    """`issue_key` always takes a column; the *profile* decides which families key on it."""
    assert issue_key("value-copy", "src/top_words.cpp", 20) != issue_key(
        "value-copy", "src/top_words.cpp", 20, 3
    )
    # `value-copy` is not column-keyed: one copy per line, so the line is the whole site.
    assert profile.key_for(
        "cpp.context.pass-by-value-container", "src/top_words.cpp", 20, 7
    ) == profile.key_for("cpp.context.pass-by-value-container", "src/top_words.cpp", 20)
    # `manual-ownership` is: one C++ line can carry two independent ownership faults.
    assert profile.key_for("cpp.context.raw-owning-pointer", "src/top_words.cpp", 20, 3) != (
        profile.key_for("cpp.context.raw-owning-pointer", "src/top_words.cpp", 20, 40)
    )


# ----------------------------------------------------- required behaviour decides applicability


@pytest.mark.parametrize(
    ("source", "item"),
    [
        (
            "std::string* copy_of() { return new std::string(); }",
            "raii_ownership",
        ),
        ("std::size_t length_of(std::string word) { return word.size(); }", "moves_copies"),
        (
            "void f(std::vector<int> v) { for (std::size_t i = 0; i < v.size(); ++i) "
            "{ (void)v[i]; } }",
            "stl_use",
        ),
    ],
)
def test_no_frozen_opportunity_means_not_applicable_never_perfect(source: str, item: str) -> None:
    findings = from_scanner(source)
    assert any(o.status == MEASURED for o in findings), findings
    result = evaluate(findings, {**FULL, item: 0})
    assert result[item].status == "not_applicable"
    assert result[item].score_bp is None
    assert result[item].reasons == ("no frozen task opportunity",)
    assert result["_result"].diagnostic_score_bp is not None


def test_an_asan_finding_scores_only_when_the_task_declares_a_memory_opportunity() -> None:
    defect = obs("cpp.asan.candidate-defect", 44)
    with_memory = evaluate([defect], {**FULL, "undefined_behavior_memory_safety": 1})
    without = evaluate([defect], {**FULL, "undefined_behavior_memory_safety": 0})
    assert with_memory["undefined_behavior_memory_safety"].unique_violations == 1
    assert with_memory["undefined_behavior_memory_safety"].score_bp == 0
    assert without["undefined_behavior_memory_safety"].status == "not_applicable"


def test_a_leak_and_a_use_after_free_are_one_ownership_fault_seen_by_the_runtime() -> None:
    """Both carry the `manual-ownership` family, so a leak is not a second ownership penalty."""
    leak = obs("cpp.asan.resource-leak", 44)
    assert leak.issue_key is not None
    result = evaluate([leak])
    assert result["raii_ownership"].unique_violations == 1
    assert result["undefined_behavior_memory_safety"].unique_violations == 0


def test_more_violations_than_opportunities_floor_at_zero_never_negative() -> None:
    many = [obs("cpp.context.pass-by-value-container", n) for n in range(1, 20)]
    item = evaluate(many)["moves_copies"]
    assert item.unique_violations == 19 and item.score_bp == 0


# --------------------------------------------------------- required scans gate the profile


def test_a_missing_required_context_scan_makes_dependent_items_missing_not_clean() -> None:
    result = profile.evaluate(
        opportunities=FULL,
        observations=[
            scan_obs("clang_tidy"),
            scan_obs("cppcheck"),
            scan_obs("context", MeasurementStatus.MISSING),
        ],
        required_tools=("context", "clang_tidy", "cppcheck"),
    )
    items = {i.item_id: i for i in (*result.diagnostic, *result.idioms)}
    for name in (
        "raii_ownership",
        "moves_copies",
        "stl_use",
        "exception_safety",
        "ownership_raii_design",
        "value_move_api_semantics",
    ):
        assert items[name].status == "missing", name
        assert items[name].reasons == ("required scan incomplete: context",)
    assert result.complete is False
    # No aggregate is invented: an unanswered required check yields None, not 0 and not 10000.
    assert result.diagnostic_score_bp is None and result.idiom_score_bp is None


def test_an_item_no_failed_tool_feeds_is_still_measured() -> None:
    """The failure is scoped by the profile's own feeder table, not by "everything failed"."""
    result = profile.evaluate(
        opportunities=FULL,
        observations=[
            scan_obs("clang_tidy"),
            scan_obs("cppcheck"),
            scan_obs("context", MeasurementStatus.MISSING),
            obs("cpp.asan.candidate-defect", 44),
        ],
        required_tools=("context", "clang_tidy", "cppcheck"),
    )
    items = {i.item_id: i for i in result.diagnostic}
    # `modern_features` is fed only by clang_tidy and cppcheck, which both answered.
    assert items["modern_features"].status == "measured"
    # `undefined_behavior_memory_safety` is fed by the sanitizers and cppcheck, not by context.
    assert items["undefined_behavior_memory_safety"].status == "measured"
    assert items["undefined_behavior_memory_safety"].unique_violations == 1
    # And an item context really does feed stays missing.
    assert items["raii_ownership"].status == "missing"


def test_an_unsupported_required_scan_blocks_while_an_optional_one_is_scored() -> None:
    """`NOT_APPLICABLE` is its own state: not clean, not a crash."""
    base = [scan_obs("clang_tidy"), scan_obs("cppcheck"), scan_obs("context")]
    unsupported = [scan_obs("asan", MeasurementStatus.NOT_APPLICABLE)]

    def run(observations: list[Observation], required: tuple[str, ...]) -> Any:
        return profile.evaluate(
            opportunities=FULL, observations=observations, required_tools=required
        )

    optional = run([*base, *unsupported], ("context", "clang_tidy", "cppcheck"))
    required = run([*base, *unsupported], ("context", "clang_tidy", "cppcheck", "asan"))
    optional_item = {i.item_id: i for i in optional.diagnostic}["undefined_behavior_memory_safety"]
    required_item = {i.item_id: i for i in required.diagnostic}["undefined_behavior_memory_safety"]
    assert optional_item.status == "measured" and optional_item.score_bp == 10_000
    assert required_item.status == "missing"
    assert required_item.reasons == ("required scan unsupported: asan",)
    assert required.diagnostic_score_bp is None


def test_missing_unsupported_and_absent_required_scans_are_three_distinct_reasons() -> None:
    def states(scan: Observation | None, tool: str) -> tuple[str, tuple[str, ...]]:
        observations = [scan_obs(other) for other in ("clang_tidy", "cppcheck", "context")]
        if scan is not None:
            observations.append(scan)
        result = profile.evaluate(
            opportunities=FULL, observations=observations, required_tools=("clang_tidy", "asan")
        )
        item = {i.item_id: i for i in result.diagnostic}["undefined_behavior_memory_safety"]
        return item.status, item.reasons

    missing, missing_reasons = states(scan_obs("asan", MeasurementStatus.MISSING), "asan")
    unsupported, unsupported_reasons = states(
        scan_obs("asan", MeasurementStatus.NOT_APPLICABLE), "asan"
    )
    absent, absent_reasons = states(None, "asan")
    assert missing == unsupported == absent == "missing"
    assert missing_reasons == ("required scan incomplete: asan",)
    assert unsupported_reasons == ("required scan unsupported: asan",)
    assert absent_reasons == ("required scan incomplete: asan",)
    # Unsupported is distinguishable from an ordinary incomplete run: they are different states.
    assert unsupported_reasons != missing_reasons


# --------------------------------------------------------- evidence ownership has one owner


def test_evidence_ownership_has_one_owner_per_issue() -> None:
    assert profile.owner("cpp.asan.candidate-defect") == ScoreDimension.ROBUSTNESS
    assert profile.owner("cpp.context.raw-delete-mismatch") == ScoreDimension.ROBUSTNESS
    assert profile.owner("cpp.context.throw-in-destructor") == ScoreDimension.ROBUSTNESS
    assert profile.owner("cpp.context.string-concat-loop") == ScoreDimension.EFFICIENCY
    # A diagnostic that feeds no dimension is owned by none; it is not everyone's problem.
    assert profile.owner("cpp.context.raw-owning-pointer") is None
    assert profile.owner("cpp.context.raw-pointer-nonowning") is None
    assert profile.owner("cpp.clang_tidy.bugprone-use-after-move") == ScoreDimension.ROBUSTNESS


def test_an_unowned_diagnostic_feeds_its_item_without_becoming_a_cross_dimension_issue() -> None:
    """A cppcheck performance note owns no dimension; it still counts against its own item."""
    advisory = obs("cpp.cppcheck.constparameterreference", 1)
    assert advisory.primary_owner is None
    result = evaluate([advisory])
    measured = {
        name: item
        for name, item in result.items()
        if name != "_result" and item.status == "measured"
    }
    assert measured["abstraction_performance"].unique_violations == 1
    assert measured["abstraction_performance"].score_bp == 5000


def test_the_asan_defect_is_owned_by_robustness_not_by_the_style_dimension() -> None:
    defect = obs("cpp.asan.candidate-defect", 44)
    assert defect.primary_owner == ScoreDimension.ROBUSTNESS
    assert profile.owner("cpp.ubsan.candidate-defect") == ScoreDimension.ROBUSTNESS
    assert profile.owner("cpp.tsan.candidate-defect") == ScoreDimension.ROBUSTNESS


def test_an_idiom_item_is_fed_only_by_the_precise_lint_that_names_the_api_design() -> None:
    """`stl_container_choice` is fed by the scanner's index-loop finding; the copy idiom is not."""
    stl = [obs("cpp.context.index-loop-container", 20)]
    result = evaluate(stl)
    assert result["stl_use"].unique_violations == 1
    assert result["stl_container_choice"].unique_violations == 1
    assert result["moves_copies"].status == "measured"
    assert result["moves_copies"].unique_violations == 0
    # The copy idiom needs the lint: a by-value parameter is a diagnostic, not a verdict on the
    # interface design, so only the precise clang-tidy lint moves the idiom item.
    lint = evaluate([obs("cpp.clang_tidy.performance-unnecessary-value-param", 12)])
    assert lint["value_move_api_semantics"].unique_violations == 1
    scanned = evaluate([obs("cpp.context.pass-by-value-container", 12)])
    assert scanned["value_move_api_semantics"].unique_violations == 0


def test_a_hint_is_evidence_for_a_reviewer_never_a_penalty() -> None:
    """A `noexcept(false)` destructor is a hint; only an actual `throw` is the violation."""
    source = textwrap.dedent(
        """
        class TokenList
        {
        public:
            ~TokenList() noexcept(false);
        };
        """
    )
    findings = from_scanner(source)
    hints = [o for o in findings if o.check_id == "cpp.context.throw-in-destructor"]
    assert hints and all(o.status == MeasurementStatus.NEEDS_REVIEW for o in hints)
    assert all(o.value is None and o.severity is None for o in hints)
    assert evaluate(findings)["exception_safety"].unique_violations == 0
    assert evaluate(findings)["exception_safety"].score_bp == 10_000


def test_an_actual_throw_in_a_destructor_is_a_violation() -> None:
    findings = from_scanner(
        """
        class TokenList
        {
        public:
            ~TokenList() { if (poisoned_) { throw std::runtime_error("no"); } }
        private:
            bool poisoned_ = false;
        };
        """
    )
    throws = [o for o in findings if o.check_id == "cpp.context.throw-in-destructor"]
    assert throws and all(o.status == MEASURED for o in throws)
    item = evaluate(findings)["exception_safety"]
    assert item.unique_violations == 1 and item.score_bp == 0


def test_benign_findings_in_bulk_cannot_move_a_score() -> None:
    """Hundreds of non-owning raw pointers and guarded moves are evidence, not violations."""
    noise = [
        *[
            obs("cpp.context.raw-pointer-nonowning", n, status=MeasurementStatus.NOT_APPLICABLE)
            for n in range(1, 60)
        ],
        *[
            obs("cpp.context.move-on-const", n, status=MeasurementStatus.NOT_APPLICABLE)
            for n in range(1, 60)
        ],
    ]
    result = evaluate(noise)
    assert result["raii_ownership"].score_bp == 10_000
    assert result["moves_copies"].score_bp == 10_000
    assert result["_result"].diagnostic_score_bp == 10_000


def test_the_profile_scores_unique_issues_against_frozen_opportunities() -> None:
    findings = from_scanner(
        """
        std::size_t a(std::string one) { return one.size(); }
        std::size_t b(std::string two) { return two.size(); }
        std::size_t c(std::string three) { return three.size(); }
        """
    )
    item = evaluate(findings)["moves_copies"]
    assert item.unique_violations == 3
    # Three faults against two frozen opportunities floors at zero, never a negative score.
    assert item.score_bp == 0
    assert item.issue_keys == tuple(sorted(item.issue_keys))


# ---------------------------------------------------------------- analyzer silence (PCB-21-2)


def test_an_analyzer_that_printed_nothing_is_missing_not_clean() -> None:
    """An omitted analyzer must never read as a clean scan.

    This is the DoD failure mode in its purest form: a run that produced no evidence at all was
    reported as ``findings=0``, which is ``MEASURED`` rather than ``MISSING``. Every item the
    tool feeds then scored full marks, so a candidate whose source merely *broke* the analyzer
    outranked one that was genuinely clean. Zero findings is only evidence of a clean scan when
    the run demonstrably looked.
    """
    scan = parsers.PARSERS["clang_tidy"](
        _raw(out_err="", out_out=""), _lint_plan(), profile
    )
    assert [o.status for o in scan] == [MeasurementStatus.MISSING]


def test_a_clean_clang_tidy_run_is_measured_and_clean() -> None:
    """The counterpart: a run that ran and found nothing *is* evidence, and scores full marks."""
    scan = parsers.PARSERS["clang_tidy"](
        _raw(
            out_err="1 warning generated.\n",
            out_out="",
        ),
        _lint_plan(),
        profile,
    )
    assert [o.status for o in scan] == [MEASURED]
    assert scan[0].value == 0
    assert evaluate(scan)["modern_features"].score_bp == 10_000


def test_clang_tidy_findings_are_found_whichever_stream_carries_them() -> None:
    """clang-tidy writes to stderr on the pinned build and to stdout on others.

    Reading one fixed stream would drop real findings on the other build while still reporting
    zero, so both streams are evidence and neither may be ignored.
    """
    diagnostic = "src/top_words.cpp:12:5: warning: pass by value [modernize-pass-by-value]\n"
    for out, err in ((diagnostic, ""), ("", diagnostic)):
        scan = parsers.PARSERS["clang_tidy"](_raw(out_err=err, out_out=out), _lint_plan(), profile)
        findings = [o for o in scan if o.check_id != "cpp.clang_tidy.scan"]
        assert len(findings) == 1, (out, err)
        assert findings[0].check_id == "cpp-clang-tidy-modernize-pass-by-value"
