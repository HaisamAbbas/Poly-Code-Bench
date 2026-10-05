"""Rust language-plugin conformance run (Technical Spec 18.5, Architecture 7; E2E-15, E2E-16).

Executes the ``top-words`` fixture package and fault-injected plans in the local Docker sandbox
and records one case per conformance category: valid solution, incorrect solution, known
anti-pattern, analyzer failure, missing dependency, timeout and profile applicability. The report
is development-tier evidence; production isolation is a separate (deferred) gate.

    .venv/Scripts/python.exe scripts/rust_conformance.py --report REPORT.json
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import yaml  # type: ignore[import-untyped,unused-ignore]
from polycodebench_core.models import MeasurementStatus
from polycodebench_evaluation.plan_runner import PlanRunner, materialize_inputs
from polycodebench_evaluation.suite_admission import SuiteAdmission, variant_files
from polycodebench_lang_rust import RustLanguagePlugin
from polycodebench_plugins_api import AnalysisContext, AnalysisPlan, TaskDraft
from polycodebench_plugins_api.admission import (
    ConformanceCaseResult,
    ConformanceReport,
    make_conformance_report,
)
from polycodebench_plugins_api.protocols import ExecutableLanguagePlugin
from polycodebench_runner.provider import LocalDockerSandboxProvider

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "plugins" / "languages" / "rust" / "fixtures" / "top-words"
RUNNER = "/opt/pcb/guest/pcb_rust_run.py"
ALLOWED = ["src/lib.rs"]

# Tokens that merely *look* suspicious: a guarded unwrap, a clone that moves an owned value and a
# documented unsafe block. Prompt 11: none of them may be penalised.
TOKEN_ONLY = b"""use std::collections::HashMap;

/// Return the `k` most frequent words of `text`, ties broken alphabetically.
pub fn top_words(text: &str, k: usize) -> Vec<(String, usize)> {
    let mut counts: HashMap<String, usize> = HashMap::new();
    let mut seen: Vec<String> = Vec::new();
    for word in text
        .split(|c: char| !c.is_ascii_alphabetic())
        .filter(|word| !word.is_empty())
    {
        let owned = word.to_ascii_lowercase();
        let key = owned.clone();
        *counts.entry(key).or_insert(0) += 1;
        seen.push(owned);
    }
    debug_assert!(seen.len() >= counts.len());
    let mut ranked: Vec<(String, usize)> = counts.into_iter().collect();
    ranked.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
    ranked.truncate(k);
    let first = ranked.first().map(|entry| entry.1).unwrap_or(0);
    let bytes = [first as u8];
    // SAFETY: the pointer comes from a live one-element array and index 0 is in bounds.
    let _checked = unsafe { *bytes.as_ptr() };
    ranked
}
"""
UNRESOLVED_IMPORT = b"""use rand::Rng;

pub fn top_words(_text: &str, _k: usize) -> Vec<(String, usize)> {
    let _ = rand::thread_rng().gen::<u8>();
    Vec::new()
}
"""
NOT_RUST = b"pub fn top_words(text: &str, k: usize) -> Vec<(String, usize)> { this is not rust\n"


def _files() -> dict[str, bytes]:
    return {
        p.relative_to(FIXTURE).as_posix(): p.read_bytes()
        for area in ("visible", "hidden", "admission")
        for p in sorted((FIXTURE / area).rglob("*"))
        if p.is_file()
    }


def _case(
    category: str, name: str, passed: bool, expected: str, observed: str
) -> ConformanceCaseResult:
    return ConformanceCaseResult(
        category=category,  # type: ignore[arg-type]
        name=name,
        passed=bool(passed),
        expected=expected[:300],
        observed=observed[:600],
    )


def _injected(
    plan: AnalysisPlan, name: str, command: tuple[str, ...], timeout: int | None = None
) -> AnalysisPlan:
    """The plan with its tool replaced by ``command`` (still run through the guest runner)."""
    argv = (
        "python",
        "-B",
        RUNNER,
        "--name",
        f"out/{name}",
        "--deadline",
        "100",
        "--cleanup",
        "/workspace/target",
        "--",
        *command,
    )
    update: dict[str, Any] = {"argv": argv}
    if timeout is not None:
        update["resources"] = plan.resources.model_copy(update={"timeout_seconds": timeout})
    return plan.model_copy(update=update)


async def run_conformance() -> ConformanceReport:
    plugin = RustLanguagePlugin()
    ids = plugin.identities
    provider = LocalDockerSandboxProvider(
        allowed_images={
            ids.runtime.reference: ids.runtime.digest,
            ids.evaluator.reference: ids.evaluator.digest,
        },
        state_dir=ROOT / ".cache" / "rust-conformance-state",
        operation_timeout_seconds=120,
    )
    runner = PlanRunner(provider, lane="admission")
    # ``RustProfile.evaluate`` returns the Rust plugin's own ``ProfileResult`` model, whose fields
    # are a superset of the shared ``plugins-api`` one, so the plugin really is an
    # ``ExecutableLanguagePlugin``; only the nominal return type differs. Verified against the
    # protocol's resolve/owner/evaluate by importing the plugin.
    engine = SuiteAdmission(
        cast("ExecutableLanguagePlugin", plugin),
        runner,
        image_digests=(ids.runtime.digest, ids.evaluator.digest),
    )
    files = _files()
    manifest = yaml.safe_load((FIXTURE / "manifest.yaml").read_text(encoding="utf-8"))
    draft = TaskDraft(
        task_id="conformance-rust-top-words",
        primary_language="rust",
        manifest=manifest,
        files=files,
    )
    view = plugin.freeze_view(draft, "sha256:" + "1" * 64)
    overlay = {
        plugin.overlay_prefix + p.removeprefix("hidden/"): d
        for p, d in files.items()
        if p.startswith("hidden/tests/")
    }
    config = plugin.trusted_inputs(files, view)
    cases: list[ConformanceCaseResult] = []

    async def evaluate(name: str, candidate: dict[str, bytes], reps: int = 1):  # type: ignore[no-untyped-def]
        return await engine.evaluate(
            view=view,
            candidate_files=candidate,
            overlay=overlay,
            label=name,
            repetitions=reps,
            config=config,
        )

    def solution(path: str) -> dict[str, bytes]:
        return variant_files(files, path, ALLOWED)

    async def analyze(name: str, candidate: dict[str, bytes], task: Any = None):  # type: ignore[no-untyped-def]
        return await engine.analyze(
            view=task or view, candidate_files=candidate, label=name, overlay=overlay, config=config
        )

    # 1. valid solution ---------------------------------------------------------------
    reference, alternative = await asyncio.gather(
        evaluate("conf-ref", solution("hidden/reference/src/lib.rs")),
        evaluate("conf-alt", solution("admission/alternative-btree/src/lib.rs")),
    )
    cases.append(
        _case(
            "valid_solution",
            "reference-and-alternative-pass",
            reference.gate == "pass" and alternative.gate == "pass",
            "both valid solutions pass every required group",
            f"reference={reference.gate} alternative={alternative.gate}",
        )
    )

    # 2. incorrect solution -----------------------------------------------------------
    faulty = await evaluate("conf-faulty", solution("admission/faulty-ties/src/lib.rs"))
    wanted = "behaviour::ties_break_alphabetically"
    cases.append(
        _case(
            "incorrect_solution",
            "wrong-tie-break-fails-its-case",
            faulty.gate == "fail" and wanted in faulty.failed_cases,
            f"gate fail naming {wanted}",
            f"gate={faulty.gate} failed={faulty.failed_cases}",
        )
    )
    broken = await evaluate("conf-syntax", {"src/lib.rs": NOT_RUST})
    cases.append(
        _case(
            "incorrect_solution",
            "compile-error-is-a-candidate-failure",
            broken.gate == "fail" and broken.reasons[0].startswith("build:"),
            "build failure, gate fail (not incomplete)",
            f"gate={broken.gate} {broken.reasons}",
        )
    )

    # 3. known anti-pattern ----------------------------------------------------------
    defective_files = solution("admission/quality-defective/src/lib.rs")
    defective = await evaluate("conf-defective", defective_files)
    observed, _scans = await analyze("conf-defect", defective_files)
    families = {
        o.issue_key.split(".")[1]
        for o in observed
        if o.status == MeasurementStatus.MEASURED and o.issue_key
    }
    opportunities: dict[str, int] = dict(cast(Mapping[str, int], view.quality["opportunities"]))
    result = plugin.rust_profile.evaluate(
        opportunities=opportunities, observations=observed, required_tools=("clippy", "context")
    )
    unique = {i.item_id: i.unique_violations for i in result.diagnostic}
    cases.append(
        _case(
            "anti_pattern",
            "defects-seen-despite-passing-tests",
            defective.gate == "pass"
            and {"clone-redundant", "index-loop", "unwrap-unguarded"} <= families
            and unique["ownership_borrowing"] >= 1
            and unique["result_option"] >= 1,
            "functional gate passes; analyzers and profile record the defects",
            f"gate={defective.gate} families={sorted(families)} violations={unique}",
        )
    )
    token_obs, _ = await analyze("conf-tokens", {"src/lib.rs": TOKEN_ONLY})
    token_result = plugin.rust_profile.evaluate(
        opportunities=opportunities,
        observations=token_obs,
        required_tools=("clippy", "context"),
    )
    token_gate = await evaluate("conf-tokens-gate", {"src/lib.rs": TOKEN_ONLY})
    cases.append(
        _case(
            "anti_pattern",
            "clone-unwrap-unsafe-tokens-are-not-penalised",
            token_gate.gate == "pass"
            and token_result.diagnostic_score_bp == 10_000
            and not _measured_findings(token_obs),
            "guarded unwrap, owned clone, documented unsafe: perfect score, no findings",
            f"gate={token_gate.gate} score={token_result.diagnostic_score_bp} "
            f"measured={sorted(_measured_findings(token_obs))}",
        )
    )

    # 4. analyzer failure (E2E-16) ------------------------------------------------------
    candidate = solution("admission/quality-defective/src/lib.rs")
    context = AnalysisContext(
        task=view, candidate_digest="sha256:" + "4" * 64, candidate_paths=("src/lib.rs",)
    )
    plans = {p.analyzer_id: p for p in plugin.analysis_plans(context)}

    async def analyse(plan: AnalysisPlan):  # type: ignore[no-untyped-def]
        run = await runner.run(
            plan,
            materialize_inputs(
                plan, {"candidate": candidate, "config": config, "overlay": overlay}
            ),
            stage_id=f"conf-{plan.analyzer_id}",
        )
        return run, plugin.parse_analysis(run.reader(), plan)

    run, obs = await analyse(plans["clippy"])
    cases.append(
        _case(
            "analyzer_failure",
            "clippy-warnings-exit-zero-are-parsed-as-findings",
            run.record.exit_code == 0
            and next(o for o in obs if o.check_id == "rust.clippy.scan").status
            == MeasurementStatus.MEASURED
            and len([o for o in obs if o.status == MeasurementStatus.MEASURED]) > 1,
            "clippy exits 0 with warnings; findings come from cargo's JSON, not the exit status",
            f"exit={run.record.exit_code} observations={len(obs)}",
        )
    )
    injected = {
        "clippy-crash-exit-101": (
            "clippy",
            ("python", "-c", "import sys; sys.stderr.write('internal error'); sys.exit(101)"),
            None,
        ),
        "clippy-missing-tool": ("clippy", ("no-such-clippy", "check"), None),
        "clippy-hang-timeout": ("clippy", ("python", "-c", "while True: pass"), 5),
        "miri-hang-timeout": ("miri", ("python", "-c", "while True: pass"), 5),
    }
    for name, (tool, command, timeout) in injected.items():
        run, obs = await analyse(_injected(plans[tool], tool, command, timeout))
        scan_obs = next(o for o in obs if o.check_id == f"rust.{tool}.scan")
        cases.append(
            _case(
                "analyzer_failure",
                name,
                scan_obs.status == MeasurementStatus.MISSING
                and scan_obs.value is None
                and len(obs) == 1,
                "required scan is MISSING (never clean) and yields no findings",
                f"exit={run.record.exit_code} timed_out={run.record.timed_out} "
                f"status={scan_obs.status.value}",
            )
        )
    no_scan = plans["context"].model_copy(
        update={"argv": ("python", "-c", "import sys; sys.exit(0)")}
    )
    run, obs = await analyse(no_scan)
    cases.append(
        _case(
            "analyzer_failure",
            "context-scanner-without-output-is-missing",
            obs[0].status == MeasurementStatus.MISSING and len(obs) == 1,
            "exit 0 with no report is a missing scan, not a clean one",
            f"exit={run.record.exit_code} status={obs[0].status.value}",
        )
    )
    broken_result = plugin.rust_profile.evaluate(
        opportunities={"result_option": 1, "clippy": 1},
        observations=[
            *(await analyse(plans["clippy"]))[1],
            *(await analyse(_injected(plans["context"], "context", ("no-such-tool",))))[1],
        ],
        required_tools=("clippy", "context"),
    )
    states = {i.item_id: i.status for i in broken_result.diagnostic if i.status != "not_applicable"}
    cases.append(
        _case(
            "analyzer_failure",
            "dependent-profile-items-are-missing-not-perfect",
            states == {"result_option": "missing", "clippy": "measured"}
            and broken_result.diagnostic_score_bp is None,
            "items fed by the failed scan are missing; no aggregate score is invented",
            str(states),
        )
    )

    # 5. missing dependency ----------------------------------------------------------
    dep = await evaluate("conf-missing-dep", {"src/lib.rs": UNRESOLVED_IMPORT})
    cases.append(
        _case(
            "missing_dependency",
            "unvendored-crate-is-a-candidate-failure",
            dep.gate == "fail" and dep.reasons[0].startswith("build:"),
            "a crate that is not vendored fails the candidate's build offline (no online fetch)",
            f"gate={dep.gate} reasons={dep.reasons}",
        )
    )
    with_inventory = view.model_copy(update={"dependency_inventory": ("serde",)})
    advisory = next(
        p
        for p in plugin.analysis_plans(
            AnalysisContext(
                task=with_inventory,
                candidate_digest="sha256:" + "4" * 64,
                candidate_paths=("src/lib.rs",),
            )
        )
        if p.analyzer_id == "dependency"
    )
    run = await runner.run(
        advisory,
        materialize_inputs(advisory, {"candidate": candidate, "config": config}),
        stage_id="conf-advisory",
    )
    obs = plugin.parse_analysis(run.reader(), advisory)
    cases.append(
        _case(
            "missing_dependency",
            "unpopulated-advisory-snapshot-is-never-clean",
            obs[0].status == MeasurementStatus.MISSING and len(obs) == 1,
            "a dependency audit without a populated pinned snapshot is a missing scan",
            f"exit={run.record.exit_code} status={obs[0].status.value}",
        )
    )

    # 6. timeout ---------------------------------------------------------------------
    hung = await evaluate("conf-timeout", solution("admission/timeout-case/src/lib.rs"))
    cases.append(
        _case(
            "timeout",
            "hung-test-is-a-candidate-timeout-naming-the-case",
            hung.gate == "fail"
            and any(r.startswith("candidate_timeout:behaviour::") for r in hung.reasons),
            "the hard deadline stops the run; the case in flight is recorded; candidate failure",
            f"gate={hung.gate} reasons={hung.reasons[:2]}",
        )
    )

    # 7. profile applicability --------------------------------------------------------
    ub = solution("hidden/reference/src/lib.rs")
    foreign = {
        "src/lib.rs": ub["src/lib.rs"]
        .replace(
            b"pub fn top_words",
            b'extern "C" {\n    fn getppid() -> i32;\n}\n\npub fn top_words',
            1,
        )
        .replace(
            b"    let mut counts",
            b"    // SAFETY: getppid has no preconditions.\n    let _ppid = unsafe { getppid() };\n"
            b"    let mut counts",
            1,
        )
    }
    required_view = view.model_copy(
        update={
            "quality": {
                **view.quality,
                "miri": "required",
                "required_analyzers": ["clippy", "context", "miri"],
                "opportunities": {**opportunities, "unsafe_soundness": 1},
            },
            "required_analyzers": ("clippy", "context", "miri"),
        }
    )
    optional_view = view.model_copy(
        update={
            "quality": {
                **view.quality,
                "opportunities": {**opportunities, "unsafe_soundness": 1},
            }
        }
    )
    required_obs, _ = await analyze("conf-miri-required", foreign, required_view)
    optional_obs, _ = await analyze("conf-miri-optional", foreign, optional_view)
    blocked = plugin.rust_profile.evaluate(
        opportunities={"unsafe_soundness": 1},
        observations=required_obs,
        required_tools=("clippy", "context", "miri"),
    )
    scored = plugin.rust_profile.evaluate(
        opportunities={"unsafe_soundness": 1},
        observations=optional_obs,
        required_tools=("clippy", "context"),
    )
    none = plugin.rust_profile.evaluate(
        opportunities={"clippy": 1}, observations=optional_obs, required_tools=("clippy",)
    )
    unsafe_blocked = next(i for i in blocked.diagnostic if i.item_id == "unsafe_soundness")
    unsafe_scored = next(i for i in scored.diagnostic if i.item_id == "unsafe_soundness")
    unsafe_none = next(i for i in none.diagnostic if i.item_id == "unsafe_soundness")
    miri_scan = next(o for o in required_obs if o.check_id == "rust.miri.scan")
    cases.append(
        _case(
            "profile_applicability",
            "miri-applicability-is-contextual",
            miri_scan.status == MeasurementStatus.NOT_APPLICABLE
            and unsafe_blocked.status == "missing"
            and unsafe_blocked.reasons == ("required scan unsupported: miri",)
            and unsafe_scored.status == "measured"
            and unsafe_scored.unique_violations == 0
            and unsafe_none.status == "not_applicable",
            "an unsupported Miri run blocks only tasks that require it; no opportunity means N/A",
            f"scan={miri_scan.status.value} required={unsafe_blocked.status}"
            f"{unsafe_blocked.reasons} optional={unsafe_scored.status} none={unsafe_none.status}",
        )
    )
    return make_conformance_report(
        plugin_id="rust",
        plugin_version="0.1.0",
        execution_tier="development_sandbox",
        image_digests=(ids.runtime.digest, ids.evaluator.digest),
        cases=tuple(cases),
    )


def _measured_findings(observations: list[Any]) -> set[str]:
    return {
        o.check_id
        for o in observations
        if o.status == MeasurementStatus.MEASURED and not o.check_id.endswith(".scan")
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="rust_conformance")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    report = asyncio.run(run_conformance())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    for case in report.cases:
        print(
            ("PASS" if case.passed else "FAIL"), case.category, case.name, "|", case.observed[:110]
        )
    print(f"conformance passed={report.passed} cases={len(report.cases)}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
