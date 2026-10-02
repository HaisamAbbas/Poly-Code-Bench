"""Local cross-language contract audit; this is not live sandbox conformance evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import scoring_support as scoring
import yaml
from polycodebench_core.identity import new_entity_id
from polycodebench_core.models import Candidate, ScoreDimension, TaskOutputContract
from polycodebench_core.solve_extraction import extract_from_response
from polycodebench_lang_c import CLanguagePlugin
from polycodebench_lang_cpp import CppLanguagePlugin
from polycodebench_lang_go import GoLanguagePlugin
from polycodebench_lang_java.plugin import JavaLanguagePlugin
from polycodebench_lang_java.profile import load_profile as load_java_profile
from polycodebench_lang_javascript.plugin import (
    JavaScriptLanguagePlugin,
    TypeScriptLanguagePlugin,
)
from polycodebench_lang_python import PythonLanguagePlugin
from polycodebench_lang_rust import RustLanguagePlugin
from polycodebench_plugins_api import (
    AnalysisContext,
    TaskDraft,
    load_allowlist,
    load_language_plugin,
)
from polycodebench_scoring.arithmetic import split_integer
from polycodebench_scoring.loader import load_evidence_ownership, load_scoring_policy
from polycodebench_scoring.replay import replay_outcome
from polycodebench_scoring.scorer import score_evaluation

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = ROOT / "config/plugins/allowlist-v1.yaml"
FIXTURE_LANGUAGES = ("python", "rust", "c", "cpp", "go", "java", "javascript", "typescript")
PROFILE_VERSIONS = {
    "python": "python-profile-v1",
    "rust": "rust-profile-v1",
    "c": "c-profile-v1",
    "cpp": "cpp-profile-v1",
    "go": "go-profile-v1",
    "java": "java-profile-v1",
    "javascript": "javascript-profile-v1",
    "typescript": "typescript-profile-v1",
}
OUTPUT_PATHS = {
    "python": "solution.py",
    "rust": "src/lib.rs",
    "javascript": "src/index.js",
    "typescript": "src/index.ts",
    "c": "src/main.c",
    "cpp": "src/main.cpp",
    "go": "main.go",
    "java": "src/main/java/demo/Main.java",
}


def _package_draft(language: str) -> tuple[TaskDraft, dict[str, Any]]:
    package = ROOT / "plugins/languages" / language / "fixtures/top-words"
    manifest = yaml.safe_load((package / "manifest.yaml").read_text(encoding="utf-8"))
    files = {
        item.relative_to(package).as_posix(): item.read_bytes()
        for area in ("visible", "hidden", "admission")
        for item in sorted((package / area).rglob("*"))
        if item.is_file() and "__pycache__" not in item.parts
    }
    return (
        TaskDraft(
            # Most language packs wrap their task identity under `task`; Java's typed task
            # fixture has a distinct, strict schema with task_id at the root. Keep the shared
            # audit on each plugin's actual admission representation instead of coercing Java's
            # manifest to another language's shape.
            task_id=(
                manifest["task"]["task_id"]
                if isinstance(manifest.get("task"), dict)
                else manifest["task_id"]
            ),
            primary_language=language,
            manifest=manifest,
            files=files,
        ),
        files,
    )


def _candidate(task_id: str) -> Candidate:
    return Candidate.model_validate_json(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "candidate",
                "candidate_id": new_entity_id(),
                "run_id": new_entity_id(),
                "task_id": task_id,
                "task_version": 1,
                "sample_index": 0,
                "submission_kind": "source_bundle",
                "payload_digest": "sha256:" + "3" * 64,
                "artifact_ids": [],
                "frozen_at": None,
            }
        )
    )


def test_recorded_language_entry_points_load_and_declare_their_own_profile() -> None:
    allowlist = load_allowlist(ALLOWLIST)
    # Every registered language must have a recorded image identity. A plugin allowlisted without
    # one would advertise a capability no administrator ever approved.
    assert {entry.plugin_id for entry in allowlist.plugins} == set(FIXTURE_LANGUAGES)
    for language, version in PROFILE_VERSIONS.items():
        plugin = load_language_plugin(allowlist, language)
        profile = plugin.profile(version)
        assert plugin.language_id == language
        assert profile.language_id == language
        assert profile.effective_for_scoring is False
        assert allowlist.get(language).image_digests


def test_registered_plugins_publish_the_shared_profile_and_property_engine_contracts() -> None:
    plugin_types = {
        "python": PythonLanguagePlugin,
        "rust": RustLanguagePlugin,
        "c": CLanguagePlugin,
        "cpp": CppLanguagePlugin,
        "go": GoLanguagePlugin,
        "java": JavaLanguagePlugin,
    }
    for language, plugin_type in plugin_types.items():
        draft, _files = _package_draft(language)
        if language == "java":
            # The profile/property engine contract is independent of the image, while plans are
            # exercised against the real pinned identities in test_java_docker.py.
            from java_plugin_support import identities as test_java_identities

            plugin = plugin_type(identities=test_java_identities())
        else:
            plugin = plugin_type()
        frozen = plugin.freeze_view(draft, "sha256:" + "1" * 64)
        profile = plugin.language_profile
        assert callable(profile.resolve) and callable(profile.owner) and callable(profile.evaluate)
        engine = plugin.property_engine(frozen, {})
        assert engine.kind == "property_engine_identity"
        assert engine.deterministic_policy

    # JS and TS are in the administrative image/plugin allowlist so local tools can load their
    # distinct identities. That entry does not mean either language has an admitted task pack;
    # pin the current zero-manifest state so registry metadata cannot silently be read as E2E
    # coverage.
    for plugin_type, language in (
        (JavaScriptLanguagePlugin, "javascript"),
        (TypeScriptLanguagePlugin, "typescript"),
    ):
        assert plugin_type.language_id == language
        assert hasattr(plugin_type, "language_profile")
        assert callable(plugin_type.property_engine)
        fixture_root = ROOT / "plugins/languages" / language / "fixtures"
        assert not tuple(fixture_root.rglob("manifest.yaml"))


def test_c_and_cpp_authored_tasks_validate_and_build_typed_plans() -> None:
    allowlist = load_allowlist(ALLOWLIST)
    for language, plugin_type in (("c", CLanguagePlugin), ("cpp", CppLanguagePlugin)):
        draft, _files = _package_draft(language)
        plugin = plugin_type()
        report = plugin.validate_task(draft)
        assert report.issues == ()
        frozen = plugin.freeze_view(draft, "sha256:" + "1" * 64)
        assert plugin.profile(f"{language}-profile-v1").language_id == language
        build = plugin.build_plan(frozen, _candidate(frozen.task_id))
        assert build.image_digest in allowlist.get(language).image_digests
        test_plan = plugin.test_plan(frozen)
        assert test_plan.groups and test_plan.expected_inventory_digest == frozen.inventory_digest
        context = AnalysisContext(
            task=frozen,
            candidate_digest="sha256:" + "2" * 64,
            candidate_paths=tuple(frozen.required_outputs),
        )
        plans = plugin.analysis_plans(context)
        assert plans and all(plan.language_id == language for plan in plans)
        assert all(plan.image_digest in allowlist.get(language).image_digests for plan in plans)
        if language == "cpp":
            sanitizer = next(plan for plan in plans if plan.analyzer_id == "asan")
            assert sanitizer.exit_semantics.classify(2, False) == "findings"
            assert sanitizer.exit_semantics.classify(126, False) == "error"


def test_output_contracts_accept_each_language_path_and_reject_traversal() -> None:
    for language, path in OUTPUT_PATHS.items():
        contract = TaskOutputContract.model_validate(
            {
                "schema_version": 1,
                "kind": "task_output_contract",
                "submission_kind": "files",
                "allowed_paths": [path],
                "maximum_artifact_bytes": 4096,
                "maximum_file_bytes": 2048,
                "maximum_files": 1,
                "findings_limit": None,
            },
            strict=False,
        )
        good = extract_from_response(
            json.dumps({"files": [{"path": path, "content": f"// {language}\n"}]}),
            contract=contract,
            rule="json_envelope",
            required_outputs=[path],
        )
        assert good.validity == "valid"
        traversal = extract_from_response(
            json.dumps({"files": [{"path": "../" + path, "content": "bad"}]}),
            contract=contract,
            rule="json_envelope",
            required_outputs=[],
        )
        assert traversal.validity == "contract_invalid"


def test_registered_language_profiles_flow_through_scoring_and_replay() -> None:
    profiles = {
        "python": PythonLanguagePlugin().profile("python-profile-v1"),
        "rust": RustLanguagePlugin().profile("rust-profile-v1"),
        "c": CLanguagePlugin().profile("c-profile-v1"),
        "cpp": CppLanguagePlugin().profile("cpp-profile-v1"),
        "go": GoLanguagePlugin().profile("go-profile-v1"),
        "java": load_java_profile().profile,
        "javascript": JavaScriptLanguagePlugin().profile("javascript-profile-v1"),
        "typescript": TypeScriptLanguagePlugin().profile("typescript-profile-v1"),
    }
    policy = load_scoring_policy(ROOT / "config/scoring/pilot-v1.yaml")
    ownership = load_evidence_ownership(ROOT / "config/scoring/evidence_ownership.yaml")
    applicable = (ScoreDimension.IDIOMATIC,)
    for language, profile in profiles.items():
        profile_shares = {item.item_id: item.weight_bp for item in profile.idiom_items}
        scaled = split_integer(policy.idiomatic.language_rubric_weight_bp, profile_shares)
        item_weights = scaled | policy.idiomatic.residual_judge_items
        items = [
            scoring.measured(item_id, ScoreDimension.IDIOMATIC, weight, 8_000)
            for item_id, weight in sorted(item_weights.items())
        ]
        task = scoring.frozen_task(applicable=applicable, required_analyzers=()).model_copy(
            update={"primary_language": language, "applicable_dimensions": applicable}
        )
        evidence = scoring.manifest(
            applicable=applicable,
            include_efficiency=False,
            items=items,
            required_evidence=(),
        ).model_copy(
            update={
                "language_id": language,
                "applicability": (ScoreDimension.CORRECTNESS, *applicable),
            }
        )
        outcome = score_evaluation(task, policy, evidence, ownership=ownership, profile=profile)
        replay = replay_outcome(
            task,
            policy,
            evidence,
            ownership=ownership,
            profile=profile,
            archived=outcome,
        )
        assert replay.matched


def test_javascript_and_typescript_are_registered_with_distinct_semantics() -> None:
    """Both are now admitted, and they must stay genuinely separate once they are.

    Sharing one source package is fine; sharing one toolchain is not. The images are what decide
    that: a JavaScript candidate must not be able to run `tsc` in the image that will judge it, so
    the JavaScript evaluator records `tsc` as absent while the TypeScript one records a version.
    """
    allowlist = load_allowlist(ALLOWLIST)
    registered = {entry.plugin_id for entry in allowlist.plugins}
    assert {"javascript", "typescript"} <= registered
    project = (ROOT / "plugins/languages/javascript/pyproject.toml").read_text(encoding="utf-8")
    assert 'javascript = "polycodebench_lang_javascript.plugin:JavaScriptLanguagePlugin"' in project
    assert 'typescript = "polycodebench_lang_javascript.plugin:TypeScriptLanguagePlugin"' in project
    js = yaml.safe_load(
        (ROOT / "config/languages/javascript-profile-v1.yaml").read_text(encoding="utf-8")
    )
    ts = yaml.safe_load(
        (ROOT / "config/languages/typescript-profile-v1.yaml").read_text(encoding="utf-8")
    )
    assert js["kind"] == "javascript_profile" and ts["kind"] == "typescript_profile"
    assert js["profile_version"] != ts["profile_version"]
    assert "typescript" not in js["weights_source"]
    assert any(
        "type_safety" in rule.get("items", [])
        for rule in ts["rule_mappings"]
        if isinstance(rule, dict)
    )
    assert not any(
        "type_safety" in rule.get("items", [])
        for rule in js["rule_mappings"]
        if isinstance(rule, dict)
    )


def test_javascript_and_typescript_images_do_not_share_a_toolchain() -> None:
    """The recorded identities, not just the profiles, keep the two languages apart."""
    recorded = {}
    for language in ("javascript", "typescript"):
        document = json.loads(
            (ROOT / f"config/images/{language}-v1.json").read_text(encoding="utf-8")
        )
        recorded[language] = document["images"]
    assert recorded["javascript"]["evaluator"]["tools"]["tsc"] == "absent"
    assert recorded["javascript"]["evaluator"]["tools"]["eslint"] != "absent"
    assert recorded["typescript"]["evaluator"]["tools"]["tsc"] != "absent"
    assert recorded["typescript"]["evaluator"]["tools"]["eslint"] != "absent"
    # Neither language's runtime image may carry an analyzer its candidates could inspect.
    for language, images in recorded.items():
        for recipe in ("runtime", "performance"):
            assert images[recipe]["tools"]["eslint"] == "absent", (language, recipe)
