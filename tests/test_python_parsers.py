"""Python parsers, profile evaluation and failure semantics (offline, replaying real recordings)."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from polycodebench_core.models import MeasurementStatus, Observation
from polycodebench_lang_python import PythonLanguagePlugin
from python_plugin_support import Recording

plugin = PythonLanguagePlugin()
profile = plugin.python_profile
MEASURED = MeasurementStatus.MEASURED
MISSING = MeasurementStatus.MISSING


def parse(rec: Recording, **reader: Any) -> list[Observation]:
    return plugin.parse_analysis(rec.reader(**reader), rec.plan)


def checks(observations: list[Observation], status: MeasurementStatus = MEASURED) -> set[str]:
    return {o.check_id for o in observations if o.status == status}


def scan(observations: list[Observation], tool: str) -> Observation:
    return next(o for o in observations if o.check_id == f"python.{tool}.scan")


def all_defects() -> list[Observation]:
    result: list[Observation] = []
    for tool in ("ruff", "bandit", "semgrep", "context"):
        result.extend(parse(Recording("defects", tool)))
    return result


# --------------------------------------------------- real recordings are parsed correctly


def test_findings_exits_are_parsed_not_treated_as_failures() -> None:
    ruff = parse(Recording("defects", "ruff"))
    assert scan(ruff, "ruff").status == MEASURED and scan(ruff, "ruff").value == 3
    assert {"python.ruff.b006", "python.ruff.e722", "python.ruff.sim115"} <= checks(ruff)
    bandit = parse(Recording("defects", "bandit"))
    assert {"python.bandit.b602", "python.bandit.b301", "python.bandit.b105"} <= checks(bandit)
    b602 = next(o for o in bandit if o.check_id == "python.bandit.b602")
    assert b602.severity == "high" and b602.primary_owner.value == "security"
    assert b602.location is not None and b602.location.path == "solution.py"
    semgrep = parse(Recording("defects", "semgrep"))
    assert {"python.semgrep.subprocess-shell-true", "python.semgrep.weak-hash"} <= checks(semgrep)
    context = parse(Recording("defects", "context"))
    assert {
        "python.context.mutable-default-shared",
        "python.context.bare-except",
        "python.context.manual-counter",
        "python.context.range-len-index-loop",
    } <= checks(context)


def test_a_completed_clean_scan_is_measured_zero_and_only_then() -> None:
    for tool in ("ruff", "bandit", "semgrep", "context"):
        observations = parse(Recording("clean", tool))
        assert len(observations) == 1
        assert scan(observations, tool).status == MEASURED and scan(observations, tool).value == 0


def test_mypy_findings_and_typing_findings_are_recorded() -> None:
    observations = parse(Recording("typed-bad", "mypy"))
    assert scan(observations, "mypy").value == 2
    assert {"python.mypy.return-value", "python.mypy.no-untyped-def"} == checks(observations) - {
        "python.mypy.scan"
    }


# ------------------------------------------- missing, crashing or contradictory checks


def _record(rec: Recording, **changes: Any) -> dict[str, Any]:
    return {**copy.deepcopy(rec.record), **changes}


def _is_incomplete(observations: list[Observation], tool: str) -> bool:
    return (
        len(observations) == 1
        and scan(observations, tool).status == MISSING
        and scan(observations, tool).value is None
    )


@pytest.mark.parametrize("tool", ["ruff", "bandit", "semgrep", "context"])
def test_no_execution_record_timeout_and_error_exits_never_look_clean(tool: str) -> None:
    rec = Recording("clean", tool)
    assert _is_incomplete(parse(rec, drop_record=True), tool)
    assert _is_incomplete(parse(rec, record=_record(rec, exit_code=None, timed_out=True)), tool)
    assert _is_incomplete(parse(rec, record=_record(rec, exit_code=2)), tool)  # tool error
    assert _is_incomplete(parse(rec, record=_record(rec, exit_code=127)), tool)  # missing tool
    assert _is_incomplete(parse(rec, record=_record(rec, exit_code=99)), tool)  # undeclared exit


@pytest.mark.parametrize("tool", ["ruff", "bandit", "semgrep"])
def test_missing_or_unparsable_output_is_incomplete(tool: str) -> None:
    rec = Recording("clean", tool)
    assert _is_incomplete(parse(rec, drop=(f"out/{tool}.json",)), tool)
    assert _is_incomplete(parse(rec, files={**rec.files, f"out/{tool}.json": b"not json"}), tool)
    assert _is_incomplete(parse(rec, files={**rec.files, f"out/{tool}.json": b""}), tool)


def test_bandit_crash_with_a_findings_exit_code_is_not_a_finding() -> None:
    """Bandit exits 1 both for findings and for an uncaught crash; only the report decides."""
    rec = Recording("defects", "bandit")
    crashed = rec.files | {"out/bandit.json": b"Traceback (most recent call last): ..."}
    assert _is_incomplete(parse(rec, files=crashed), "bandit")
    empty_report = json.loads(rec.files["out/bandit.json"]) | {"results": []}
    files = rec.files | {"out/bandit.json": json.dumps(empty_report).encode()}
    assert _is_incomplete(parse(rec, files=files), "bandit")  # findings exit, zero findings


def test_success_exit_that_still_reports_findings_is_contradictory() -> None:
    rec = Recording("defects", "ruff")
    assert _is_incomplete(parse(rec, record=_record(rec, exit_code=0)), "ruff")
    rec = Recording("defects", "context")
    assert _is_incomplete(parse(rec, record=_record(rec, exit_code=0)), "context")


def test_analysis_errors_and_uncovered_scope_make_the_scan_incomplete() -> None:
    bandit = Recording("clean", "bandit")
    document = json.loads(bandit.files["out/bandit.json"])
    errored = {**document, "errors": [{"filename": "./work/solution.py", "reason": "syntax"}]}
    files = bandit.files | {"out/bandit.json": json.dumps(errored).encode()}
    assert _is_incomplete(parse(bandit, files=files), "bandit")
    uncovered = {**document, "metrics": {"_totals": {}}}
    files = bandit.files | {"out/bandit.json": json.dumps(uncovered).encode()}
    assert _is_incomplete(parse(bandit, files=files), "bandit")

    semgrep = Recording("clean", "semgrep")
    document = json.loads(semgrep.files["out/semgrep.json"])
    broken = {**document, "errors": [{"level": "error", "type": "PartialParsing"}]}
    files = semgrep.files | {"out/semgrep.json": json.dumps(broken).encode()}
    assert _is_incomplete(parse(semgrep, files=files), "semgrep")
    skipped = {**document, "paths": {"scanned": [], "skipped": [{"path": "work/solution.py"}]}}
    files = semgrep.files | {"out/semgrep.json": json.dumps(skipped).encode()}
    assert _is_incomplete(parse(semgrep, files=files), "semgrep")

    context = Recording("clean", "context")
    document = json.loads(context.files["out/context.json"])
    files = context.files | {
        "out/context.json": json.dumps({**document, "complete": False}).encode()
    }
    assert _is_incomplete(parse(context, files=files), "context")


def test_mypy_exit_zero_with_output_is_not_clean() -> None:
    rec = Recording("typed-bad", "mypy")
    assert _is_incomplete(parse(rec, record=_record(rec, exit_code=0)), "mypy")
    crash = _record(rec, exit_code=2)
    assert _is_incomplete(parse(rec, record=crash), "mypy")  # mypy crash / missing file
    no_records = rec.files | {"out/mypy.json": b""}
    assert _is_incomplete(parse(rec, files=no_records), "mypy")  # findings exit, no error rows


# ------------------------------------------------------- profile: ownership, dedupe, context


def test_normalisation_merges_one_issue_reported_by_several_tools() -> None:
    observed = all_defects()
    merged = plugin.normalize(observed)
    keys = [o.issue_key for o in merged if o.issue_key and o.status == MEASURED]
    assert len(keys) == len(set(keys))
    by_check = {o.check_id: o for o in merged if o.status == MEASURED}
    # bandit + semgrep report the same shell invocation: one issue, strongest severity kept
    shell = next(
        o for o in merged if o.issue_key and o.issue_key.startswith("py.subprocess-shell.")
    )
    assert shell.severity == "high" and "also reported by" in (shell.explanation or "")
    assert "python.semgrep.subprocess-shell-true" not in by_check or (
        "python.bandit.b602" not in by_check
    )
    # ruff B006 and the context scanner describe the same mutable default: context decides
    default = [o for o in merged if o.issue_key and o.issue_key.startswith("py.mutable-default.")]
    assert len(default) == 1 and default[0].check_id == "python.context.mutable-default-shared"
    # ownership: security evidence is owned by security only, lint evidence by nobody
    assert {
        o.primary_owner.value
        for o in merged
        if o.check_id.startswith("python.bandit.") and o.primary_owner
    } == {"security"}
    assert all(o.primary_owner is None for o in merged if o.check_id.startswith("python.ruff."))


def test_mutable_default_is_judged_in_context_not_by_syntax() -> None:
    risky = plugin.normalize(
        parse(Recording("defects", "context")) + parse(Recording("defects", "ruff"))
    )
    assert any(
        o.check_id == "python.context.mutable-default-shared" and o.status == MEASURED
        for o in risky
    )
    # the same ruff B006 report becomes a benign, uncounted record when the scanner says the
    # default is only read: simulate with the scanner's own benign verdict for that location
    context = Recording("defects", "context")
    document = json.loads(context.files["out/context.json"])
    for item in document["findings"]:
        if item["rule"] == "mutable-default-shared":
            item["rule"], item["verdict"] = "mutable-default-benign", "benign_in_context"
    files = context.files | {"out/context.json": json.dumps(document).encode()}
    benign = plugin.normalize(parse(context, files=files) + parse(Recording("defects", "ruff")))
    default = [o for o in benign if o.issue_key and o.issue_key.startswith("py.mutable-default.")]
    assert len(default) == 1 and default[0].status == MeasurementStatus.NOT_APPLICABLE


OPPORTUNITIES = {
    "readability_idioms": 4,
    "error_handling": 3,
    "stdlib_use": 2,
    "performance_awareness": 2,
    "lint_style": 1,
    "iteration_laziness": 3,
    "stdlib_api_choice": 1,
}
REQUIRED = ("ruff", "context")


def evaluate(observations: list[Observation], opportunities: dict[str, int] | None = None):  # type: ignore[no-untyped-def]
    return profile.evaluate(
        opportunities=opportunities or OPPORTUNITIES,
        observations=plugin.normalize(observations),
        required_tools=REQUIRED,
    )


def item(result, group: str, item_id: str):  # type: ignore[no-untyped-def]
    members = result.idioms if group == "idiom" else result.diagnostic
    return next(i for i in members if i.item_id == item_id)


def test_profile_scores_unique_issues_against_frozen_opportunities() -> None:
    result = evaluate(all_defects())
    assert result.complete
    readability = item(result, "diagnostic", "readability_idioms")
    assert (readability.opportunities, readability.unique_violations, readability.score_bp) == (
        4,
        3,
        2500,
    )
    assert item(result, "diagnostic", "error_handling").score_bp == 0  # 3 issues, 3 chances
    assert item(result, "diagnostic", "stdlib_use").score_bp == 5000
    assert item(result, "diagnostic", "lint_style").score_bp == 10_000
    assert item(result, "idiom", "iteration_laziness").score_bp == 3333
    assert item(result, "idiom", "stdlib_api_choice").score_bp == 0
    assert item(result, "diagnostic", "type_hints").status == "not_applicable"
    assert item(result, "idiom", "data_protocol_modeling").status == "not_applicable"
    assert result.diagnostic_score_bp is not None and result.idiom_score_bp is not None


def test_no_opportunity_is_not_applicable_never_perfect() -> None:
    result = evaluate(all_defects(), {"lint_style": 1})
    assert [i.status for i in result.diagnostic if i.item_id != "lint_style"] == [
        "not_applicable"
    ] * 5
    assert result.idiom_score_bp is None  # no idiom item applies: no number is invented
    assert result.diagnostic_score_bp == 10_000  # only the applicable item is averaged


def test_duplicate_reports_and_syntax_volume_cannot_change_a_score() -> None:
    once = evaluate(all_defects())
    repeated = evaluate(all_defects() + all_defects() + all_defects())
    assert once == repeated  # the same canonical issue is counted once
    clean = evaluate([o for t in ("ruff", "context") for o in parse(Recording("clean", t))])
    assert item(clean, "diagnostic", "readability_idioms").score_bp == 10_000
    assert item(clean, "idiom", "iteration_laziness").score_bp == 10_000  # no bonus above full


def test_a_missing_required_scan_makes_dependent_items_missing_not_clean() -> None:
    context_rec = Recording("clean", "context")
    broken = parse(context_rec, record=_record(context_rec, exit_code=2))
    observed = [*parse(Recording("clean", "ruff")), *broken]
    result = evaluate(observed)
    assert item(result, "diagnostic", "error_handling").status == "missing"
    assert item(result, "diagnostic", "readability_idioms").status == "missing"
    assert item(result, "diagnostic", "lint_style").status == "measured"  # ruff-only item
    assert not result.complete and result.diagnostic_score_bp is None
    assert (
        "required scan incomplete: context" in item(result, "diagnostic", "error_handling").reasons
    )


def test_type_expectations_apply_only_when_the_task_contract_has_them() -> None:
    typed = [*parse(Recording("typed-bad", "context")), *parse(Recording("typed-bad", "mypy"))]
    expected = profile.evaluate(
        opportunities={"type_hints": 4},
        observations=plugin.normalize(typed),
        required_tools=("context", "mypy"),
    )
    hints = item(expected, "diagnostic", "type_hints")
    # context missing-annotation and mypy no-untyped-def are one issue; return-value is another
    assert (hints.opportunities, hints.unique_violations, hints.score_bp) == (4, 2, 5000)
    unexpected = profile.evaluate(
        opportunities={"lint_style": 1},
        observations=plugin.normalize(typed),
        required_tools=("context",),
    )
    assert item(unexpected, "diagnostic", "type_hints").status == "not_applicable"


def test_more_violations_than_opportunities_floor_at_zero_never_negative() -> None:
    """Review gap found by mutation: the count is capped at the frozen opportunity count."""
    result = evaluate(all_defects(), {"error_handling": 1, "readability_idioms": 1})
    handling = item(result, "diagnostic", "error_handling")
    assert handling.unique_violations == 3 and handling.opportunities == 1
    assert handling.score_bp == 0
    assert item(result, "diagnostic", "readability_idioms").score_bp == 0
