"""Offline unit tests for the independent grading evaluator's normalization rules (Prompt 12).

E2E-18 semantics: duplicate scanner reports of one issue share one canonical key and one
composite owner; preexisting baseline debt is related without unjustified candidate blame;
ambiguous baseline mappings stay reviewable instead of silently clean.

"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from polycodebench_core.identity import new_entity_id
from polycodebench_core.models import Candidate, Observation
from polycodebench_evaluation.evaluator import Evaluator, _family
from polycodebench_lang_python import PythonLanguagePlugin
from python_plugin_support import frozen

plugin = PythonLanguagePlugin()
VIEW = frozen(plugin)


_LOCATION: dict[str, Any] = {
    "schema_version": 1,
    "kind": "source_location",
    "path": "solution.py",
    "start_line": 3,
    "end_line": 3,
    "start_column": None,
    "end_column": None,
    "base_or_candidate_digest": "sha256:" + "2" * 64,
}


def _observation(**fields: Any) -> Observation:
    base: dict[str, Any] = {
        "schema_version": 1,
        "kind": "observation",
        "tool_digest": "sha256:" + "1" * 64,
        "candidate_digest": "sha256:" + "2" * 64,
        "status": "measured",
        "value": True,
        "severity": "high",
        "confidence": "high",
        "location": {
            "schema_version": 1,
            "kind": "source_location",
            "path": "solution.py",
            "start_line": 3,
            "end_line": 3,
            "start_column": None,
            "end_column": None,
            "base_or_candidate_digest": "sha256:" + "2" * 64,
        },
        "baseline_relation": None,
        "issue_key": None,
        "primary_owner": None,
        "raw_artifact_ids": [],
        "explanation": None,
    }
    base.update(fields)
    return Observation.model_validate_json(json.dumps(base))


def _evaluator() -> Evaluator:
    return Evaluator(plugin, runner=None)  # type: ignore[arg-type]


def test_rejected_evaluation_keeps_the_persisted_evaluation_identity() -> None:
    view = frozen(plugin)
    evaluation_id = new_entity_id()
    candidate = Candidate(
        schema_version=1,
        kind="candidate",
        candidate_id=new_entity_id(),
        run_id=new_entity_id(),
        task_id=view.task_id,
        task_version=view.task_version,
        sample_index=0,
        submission_kind="source_bundle",
        payload_digest="sha256:" + "2" * 64,
        artifact_ids=[new_entity_id()],
        frozen_at="2026-10-02T00:00:00.000000Z",
    )

    result = asyncio.run(
        _evaluator().evaluate(
            view=view,
            candidate=candidate,
            candidate_files={"solution.py": b"return 1"},
            overlay={},
            config={},
            allowed_paths=("solution.py",),
            baseline_files=None,
            evaluation_id=evaluation_id,
        )
    )

    assert result.evidence.evaluation_id == evaluation_id
    assert result.evidence.gate == "fail"


def test_duplicate_scanner_reports_share_key_owner_and_count_once() -> None:
    profile = plugin.python_profile
    bandit = _observation(
        check_id="python.bandit.b105",
        issue_key="py.hardcoded-credential.abcd1234abcd",
        primary_owner="security",
    )
    semgrep = _observation(
        check_id="python.semgrep.hardcoded-credential",
        issue_key="py.hardcoded-credential.abcd1234abcd",
        primary_owner="security",
    )
    merged = plugin.normalize([bandit, semgrep])
    assert len(merged) == 1  # one underlying issue, one entry
    merged_obs = merged[0]
    # the composite owner comes from the reviewed mapping, not the loudest tool
    assert profile.owner(merged_obs.check_id) == "security"
    assert "semgrep" in (merged_obs.explanation or "") or "bandit" in (merged_obs.explanation or "")


def test_relations_introduced_worsened_unchanged_resolved_unknown() -> None:
    evaluator = _evaluator()
    introduced = _observation(check_id="python.bandit.b602", issue_key="py.subprocess-shell.1")
    unchanged = _observation(check_id="python.context.bare-except", issue_key="py.bare-except.2")
    worsened_c = _observation(
        check_id="python.bandit.b105", issue_key="py.hardcoded-credential.3", severity="critical"
    )
    moved = _observation(
        check_id="python.context.bare-except",
        issue_key="py.bare-except.4",
        location={**_LOCATION, "start_line": 9, "end_line": 9, "path": "solution.py"},  # type: ignore[arg-type]
    )
    baseline = [
        _observation(check_id="python.context.bare-except", issue_key="py.bare-except.2"),
        _observation(
            check_id="python.bandit.b105", issue_key="py.hardcoded-credential.3", severity="low"
        ),
        _observation(check_id="python.context.manual-counter", issue_key="py.manual-counter.5"),
        _observation(
            check_id="python.context.bare-except",
            issue_key="py.bare-except.6",
            location={**_LOCATION, "start_line": 9, "end_line": 9, "path": "solution.py"},  # type: ignore[arg-type]
        ),
    ]
    relations, resolutions = evaluator._relations(
        [introduced, unchanged, worsened_c, moved],
        baseline,
        {"solution.py": b"candidate-bytes"},
        {"solution.py": b"base-bytes"},
    )
    assert relations["py.subprocess-shell.1"] == "introduced"
    assert relations["py.bare-except.2"] == "unchanged_in_scope"
    assert relations["py.hardcoded-credential.3"] == "worsened"
    # same family + same file but different canonical key (moved) -> unresolved, reviewable
    assert relations["py.bare-except.4"] in {"introduced", "unknown"}
    resolved = {r.issue_key: r.relation for r in resolutions}
    assert resolved["py.manual-counter.5"] == "resolved"


def test_ambiguous_baseline_mapping_is_not_auto_blamed() -> None:
    evaluator = _evaluator()
    moved_candidate = _observation(
        check_id="python.context.bare-except",
        issue_key="py.bare-except.40",
        location={**_LOCATION, "start_line": 42, "end_line": 42},  # type: ignore[arg-type]
    )
    base = _observation(
        check_id="python.context.bare-except",
        issue_key="py.bare-except.41",
        location={**_LOCATION, "start_line": 7, "end_line": 7},  # type: ignore[arg-type]
    )
    relations, resolutions = evaluator._relations(
        [moved_candidate], [base], {"solution.py": b"x"}, {"solution.py": b"y"}
    )
    assert relations["py.bare-except.40"] == "unknown"
    assert resolutions[0].relation == "unknown" and resolutions[0].ambiguous


def test_family_extraction() -> None:
    assert _family("py.hardcoded-credential.abcd") == "hardcoded-credential"
    assert _family("rust.clone-redundant.0123") == "clone-redundant"
