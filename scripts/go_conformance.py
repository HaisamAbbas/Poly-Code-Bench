"""Go language-plugin conformance run (Technical Spec 18.5, Architecture 7; E2E-15, E2E-35).

Executes the ``top-words`` fixture package and fault-injected plans in the local Docker sandbox
and records one case per conformance category: valid solution, incorrect solution, known
anti-pattern, analyzer failure, missing dependency, timeout and profile applicability. The report
is development-tier evidence; production isolation is a separate (deferred) gate.

    .venv/Scripts/python.exe scripts/go_conformance.py --report REPORT.json
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import yaml
from polycodebench_core.models import MeasurementStatus
from polycodebench_evaluation.plan_runner import PlanRunner, materialize_inputs
from polycodebench_evaluation.suite_admission import SuiteAdmission, variant_files
from polycodebench_lang_go import GoLanguagePlugin
from polycodebench_lang_go import plans as go_plans
from polycodebench_plugins_api import AnalysisContext, AnalysisPlan, FrozenTask, TaskDraft
from polycodebench_plugins_api.admission import (
    ConformanceCaseResult,
    ConformanceReport,
    make_conformance_report,
)
from polycodebench_runner.provider import LocalDockerSandboxProvider

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "plugins" / "languages" / "go" / "fixtures" / "top-words"
RUNNER = "/opt/pcb/guest/pcb_go_run.py"
GO_CACHE = "/opt/pcb/cache/go-build"
ALLOWED = ["topwords/topwords.go"]

# Constructs that only *look* suspicious: a deferred close, a wrapped error, a sentinel compared
# with errors.Is, a root context at an entry point and a goroutine that is joined. Prompt 22:
# none of them may be penalised.
TOKEN_ONLY = b"""package topwords

import (
\t"context"
\t"errors"
\t"fmt"
\t"io"
\t"os"
\t"sort"
\t"strings"
\t"sync"
)

// TopWords is deliberately written with every construct the scanner treats as suspicious *used
// correctly*: a deferred close, a wrapped error, errors.Is, a root context at the entry point and
// a goroutine that is joined.
func TopWords(text string, k int) []Count {
\tif k <= 0 {
\t\treturn []Count{}
\t}
\treader := strings.NewReader(text)
\tdefer reader.Close()

\tctx := context.Background()
\tif err := ctx.Err(); err != nil {
\t\treturn nil
\t}

\tcounts := make(map[string]int, len(text)/4+1)
\tvar group sync.WaitGroup
\tgroup.Add(1)
\tgo func() {
\t\tdefer group.Done()
\t\tfor _, word := range splitWords(text) {
\t\t\tcounts[strings.ToLower(word)]++
\t\t}
\t}()
\tgroup.Wait()

\tline, err := reader.ReadString('\n')
\tif err != nil && !errors.Is(err, io.EOF) {
\t\treturn nil
\t}
\t_ = line

\tif _, err := os.Stdout.Stat(); err != nil {
\t\treturn nil
\t}

\tvar builder strings.Builder
\tfor word, count := range counts {
\t\tfmt.Fprintf(&builder, "%s=%d;", word, count)
\t}
\tranked := make([]Count, 0, len(counts))
\tfor word, count := range counts {
\t\tranked = append(ranked, Count{Word: word, Count: count})
\t}
\tsort.Slice(ranked, func(i, j int) bool {
\t\tif ranked[i].Count != ranked[j].Count {
\t\t\treturn ranked[i].Count > ranked[j].Count
\t\t}
\t\treturn ranked[i].Word < ranked[j].Word
\t})
\tif len(ranked) > k {
\t\tranked = ranked[:k]
\t}
\treturn ranked
}

func splitWords(text string) []string {
\treturn strings.FieldsFunc(text, func(r rune) bool {
\t\treturn !((r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z'))
\t})
}
"""
UNVENDORED_MODULE = b"""package topwords

import "github.com/pkg/errors"

// Wrap is deliberately unresolvable: nothing in go.mod/go.sum pins this module, and the runtime
// runs with GOPROXY=off, so the candidate's build fails instead of fetching it.
func Wrap(err error) error {
\treturn errors.Wrap(err, "topwords")
}

func TopWords(text string, k int) []Count {
\t_ = Wrap(nil)
\t_ = text
\t_ = k
\treturn nil
}
"""
NOT_GO = b"package topwords\n\nfunc TopWords(text string, k int) []Count { this is not go\n"


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
        GO_CACHE,
        "--",
        *command,
    )
    update: dict[str, Any] = {"argv": argv}
    if timeout is not None:
        update["resources"] = plan.resources.model_copy(update={"timeout_seconds": timeout})
    return plan.model_copy(update=update)


def _concurrent_view(
    view: FrozenTask, *, race: str, opportunities: Mapping[str, int]
) -> FrozenTask:
    """The same frozen task, read as a concurrent one (the applicability switch under test)."""
    quality = {
        **view.quality,
        "race": race,
        "opportunities": {
            **cast(Mapping[str, int], view.quality["opportunities"]),
            **opportunities,
        },
    }
    analyzers = list(view.required_analyzers)
    if race == "required":
        analyzers = sorted({*analyzers, "race"})
    else:
        analyzers = [item for item in analyzers if item != "race"]
    return view.model_copy(update={"quality": quality, "required_analyzers": tuple(analyzers)})


async def run_conformance() -> ConformanceReport:
    plugin = GoLanguagePlugin()
    ids = plugin.identities
    provider = LocalDockerSandboxProvider(
        allowed_images={
            ids.runtime.reference: ids.runtime.digest,
            ids.evaluator.reference: ids.evaluator.digest,
        },
        state_dir=ROOT / ".cache" / "go-conformance-state",
        operation_timeout_seconds=120,
    )
    runner = PlanRunner(provider, lane="admission")
    engine = SuiteAdmission(
        plugin, runner, image_digests=(ids.runtime.digest, ids.evaluator.digest)
    )
    files = _files()
    manifest = yaml.safe_load((FIXTURE / "manifest.yaml").read_text(encoding="utf-8"))
    draft = TaskDraft(
        task_id="conformance-go-top-words",
        primary_language="go",
        manifest=manifest,
        files=files,
    )
    view = plugin.freeze_view(draft, "sha256:" + "1" * 64)
    overlay = {
        plugin.overlay_prefix + p.removeprefix("hidden/"): d
        for p, d in files.items()
        if p.startswith("hidden/") and p.endswith("_test.go")
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
        return cast("dict[str, bytes]", variant_files(files, path, ALLOWED))

    async def analyze(  # type: ignore[no-untyped-def]
        name: str, candidate: dict[str, bytes], task: Any = None
    ):
        return await engine.analyze(
            view=task or view, candidate_files=candidate, label=name, overlay=overlay, config=config
        )

    # 1. valid solution ---------------------------------------------------------------
    reference, alternative = await asyncio.gather(
        evaluate("conf-ref", solution("hidden/reference/topwords/topwords.go")),
        evaluate("conf-alt", solution("admission/alternative-heaps/topwords/topwords.go")),
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
    faulty = await evaluate("conf-faulty", solution("admission/faulty-ties/topwords/topwords.go"))
    wanted = "TestTiesBreakAlphabetically"
    cases.append(
        _case(
            "incorrect_solution",
            "wrong-tie-break-fails-its-case",
            faulty.gate == "fail" and wanted in faulty.failed_cases,
            f"gate fail naming {wanted}",
            f"gate={faulty.gate} failed={faulty.failed_cases}",
        )
    )
    broken = await evaluate("conf-syntax", {"topwords/topwords.go": NOT_GO})
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
    defective_files = solution("admission/quality-defective/topwords/topwords.go")
    defective = await evaluate("conf-defective", defective_files)
    observed, _scans = await analyze("conf-defect", defective_files)
    families = {
        o.issue_key.split(".")[1]
        for o in observed
        if o.status == MeasurementStatus.MEASURED and o.issue_key
    }
    opportunities: dict[str, int] = dict(cast(Mapping[str, int], view.quality["opportunities"]))
    result = plugin.go_profile.evaluate(
        opportunities=opportunities, observations=observed, required_tools=("context",)
    )
    unique = {i.item_id: i.unique_violations for i in result.diagnostic}
    cases.append(
        _case(
            "anti_pattern",
            "defects-seen-despite-passing-tests",
            defective.gate == "pass"
            and {"ignored-error", "error-chain", "error-sentinel", "string-building"} <= families
            and unique["error_handling"] >= 2
            and unique["standard_library"] >= 1,
            "functional gate passes; analyzers and profile record the defects",
            f"gate={defective.gate} families={sorted(families)} violations={unique}",
        )
    )
    token_obs, _ = await analyze("conf-tokens", {"topwords/topwords.go": TOKEN_ONLY})
    token_result = plugin.go_profile.evaluate(
        opportunities=opportunities,
        observations=token_obs,
        required_tools=("context",),
    )
    token_gate = await evaluate("conf-tokens-gate", {"topwords/topwords.go": TOKEN_ONLY})
    measured = _measured_findings(token_obs)
    cases.append(
        _case(
            "anti_pattern",
            "defer-close-wrapped-error-errors-is-and-joined-goroutine-are-not-penalised",
            token_gate.gate == "pass"
            and token_result.diagnostic_score_bp == 10_000
            and not measured,
            "deferred close, wrapped error, errors.Is, root context and a joined goroutine: "
            "perfect score, no findings",
            f"gate={token_gate.gate} score={token_result.diagnostic_score_bp} "
            f"measured={sorted(measured)}",
        )
    )
    # E2E-35: a task with no concurrency opportunity is never charged for not using channels.
    concurrency = {
        item.item_id: item.status
        for item in result.diagnostic
        if item.item_id in {"goroutines_channels", "cancellation_context"}
    }
    cases.append(
        _case(
            "anti_pattern",
            "nonconcurrent-task-marks-concurrency-not-applicable",
            concurrency
            == {"goroutines_channels": "not_applicable", "cancellation_context": "not_applicable"}
            and not any(p.analyzer_id == "race" for p in plugin.analysis_plans(_context(view))),
            "no frozen concurrency opportunity means N/A and no race plan at all",
            f"statuses={concurrency} quality.race={view.quality['race']}",
        )
    )
    # E2E-35 / PCB-22-2: an instrumented run can never reach the measurement lane.
    instrumented = _instrumentation_case(plugin, view)
    cases.append(instrumented)

    # 4. analyzer failure (E2E-16) ----------------------------------------------------
    candidate = solution("admission/quality-defective/topwords/topwords.go")
    context = _context(view)
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

    run, obs = await analyse(plans["gofmt"])
    cases.append(
        _case(
            "analyzer_failure",
            "gofmt-lists-files-while-exiting-zero",
            run.record.exit_code == 0
            and next(o for o in obs if o.check_id == "go.gofmt.scan").status
            == MeasurementStatus.MEASURED,
            "gofmt exits 0 with a listing; the listing is the verdict, not the exit status",
            f"exit={run.record.exit_code} observations={len(obs)}",
        )
    )
    injected = {
        "staticcheck-crash": (
            "staticcheck",
            ("python", "-c", "import sys; sys.stderr.write('internal error'); sys.exit(101)"),
            None,
        ),
        "staticcheck-missing-tool": ("staticcheck", ("no-such-staticcheck", "./..."), None),
        "staticcheck-hang-timeout": ("staticcheck", ("python", "-c", "while True: pass"), 5),
        "context-hang-timeout": ("context", ("python", "-c", "while True: pass"), 5),
    }
    for name, (tool, command, timeout) in injected.items():
        run, obs = await analyse(_injected(plans[tool], tool, command, timeout))
        scan_obs = next(o for o in obs if o.check_id == f"go.{tool}.scan")
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
    broken_result = plugin.go_profile.evaluate(
        opportunities={"error_handling": 1, "standard_library": 1},
        observations=[
            *(await analyse(_injected(plans["context"], "context", ("no-such-tool",))))[1],
            *(await analyse(_injected(plans["gosec"], "gosec", ("no-such-tool",))))[1],
        ],
        required_tools=("context", "gosec"),
    )
    states = {i.item_id: i.status for i in broken_result.diagnostic if i.status != "not_applicable"}
    cases.append(
        _case(
            "analyzer_failure",
            "dependent-profile-items-are-missing-not-perfect",
            states == {"error_handling": "missing", "standard_library": "missing"}
            and broken_result.diagnostic_score_bp is None,
            "items fed by the failed scans are missing; no aggregate score is invented",
            str(states),
        )
    )

    # 5. missing dependency ----------------------------------------------------------
    dep = await evaluate("conf-missing-dep", {"topwords/topwords.go": UNVENDORED_MODULE})
    cases.append(
        _case(
            "missing_dependency",
            "unvendored-module-is-a-candidate-failure",
            dep.gate == "fail" and dep.reasons[0].startswith("build:"),
            "a module the graph does not pin fails the candidate's build offline "
            "(GOPROXY=off, no fetch)",
            f"gate={dep.gate} reasons={dep.reasons}",
        )
    )
    with_inventory = view.model_copy(update={"dependency_inventory": ("github.com/pkg/errors",)})
    advisory = next(
        p for p in plugin.analysis_plans(_context(with_inventory)) if p.analyzer_id == "dependency"
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
    hung = await evaluate("conf-timeout", solution("admission/timeout-case/topwords/topwords.go"))
    cases.append(
        _case(
            "timeout",
            "hung-test-is-a-candidate-timeout-naming-the-case",
            hung.gate == "fail" and any(r.startswith("candidate_timeout:") for r in hung.reasons),
            "the hard deadline stops the run; the case in flight is recorded; candidate failure",
            f"gate={hung.gate} reasons={hung.reasons[:2]}",
        )
    )

    # 7. profile applicability -------------------------------------------------------
    concurrent = _concurrent_view(
        view, race="required", opportunities={"goroutines_channels": 1, "cancellation_context": 1}
    )
    race_files = solution("admission/race-defective/topwords/topwords.go")
    race_obs, _ = await analyze("conf-race-required", race_files, concurrent)
    race_scan = next((o for o in race_obs if o.check_id == "go.race.scan"), None)
    race_finding = next((o for o in race_obs if o.check_id == "go.race.data-race"), None)
    scored = plugin.go_profile.evaluate(
        opportunities={"goroutines_channels": 1, "cancellation_context": 1},
        observations=race_obs,
        required_tools=("race",),
    )
    race_item = next(i for i in scored.diagnostic if i.item_id == "goroutines_channels")
    clean_obs, _ = await analyze(
        "conf-race-clean", solution("hidden/reference/topwords/topwords.go"), concurrent
    )
    clean_scan = next((o for o in clean_obs if o.check_id == "go.race.scan"), None)
    optional = _concurrent_view(view, race="optional", opportunities={})
    optional_obs, _ = await analyze("conf-race-optional", race_files, optional)
    optional_item = plugin.go_profile.evaluate(
        opportunities=cast(Mapping[str, int], optional.quality["opportunities"]),
        observations=optional_obs,
        required_tools=tuple(optional.required_analyzers),
    )
    cases.append(
        _case(
            "profile_applicability",
            "race-applicability-is-contextual",
            race_scan is not None
            and race_scan.status == MeasurementStatus.MEASURED
            and race_scan.value == 1
            and race_finding is not None
            and race_finding.primary_owner == "robustness"
            and race_item.status == "measured"
            and race_item.unique_violations == 1
            and clean_scan is not None
            and clean_scan.status == MeasurementStatus.MEASURED
            and clean_scan.value == 0
            and all(
                i.item_id not in {"goroutines_channels"} or i.status == "not_applicable"
                for i in optional_item.diagnostic
            ),
            "a required race run measures a real race with a robustness owner; a clean run is "
            "measured with zero findings; an optional run with no opportunity is N/A",
            f"required={race_scan.status.value if race_scan else None}"
            f"/{race_scan.value if race_scan else None} owner="
            f"{race_finding.primary_owner if race_finding else None} item={race_item.status}"
            f"/{race_item.unique_violations} clean="
            f"{clean_scan.status.value if clean_scan else None}"
            f"/{clean_scan.value if clean_scan else None}",
        )
    )
    return make_conformance_report(
        plugin_id="go",
        plugin_version="0.1.0",
        execution_tier="development_sandbox",
        image_digests=(ids.runtime.digest, ids.evaluator.digest),
        cases=tuple(cases),
    )


def _context(view: FrozenTask) -> AnalysisContext:
    return AnalysisContext(
        task=view,
        candidate_digest="sha256:" + "4" * 64,
        candidate_paths=("topwords/topwords.go",),
    )


def _refuses(flag: str) -> bool:
    """Whether the release guard refuses this instrumented flag on a measurement plan."""
    try:
        go_plans.assert_release(("go", "test", flag, "./..."), where="the conformance check")
    except ValueError:
        return True
    return False


def _instrumentation_case(plugin: GoLanguagePlugin, view: FrozenTask) -> ConformanceCaseResult:
    """An instrumented build can never be planned into the measurement lane (PCB-22-2)."""
    # Every flag that turns a build into an instrumented one, not just `-race`: a coverage build
    # times 2-5x slower and would silently enter the same lane through a different spelling.
    refused_flags = sorted(flag for flag in go_plans.INSTRUMENTED_FLAGS if _refuses(flag) is False)
    try:
        go_plans.assert_release(("go", "test", "-race", "./..."), where="the conformance check")
    except ValueError:
        refused = True
        detail = "assert_release refused -race"
    else:
        refused = False
        detail = "assert_release accepted -race"
    identities = plugin.identities
    # The flag check alone is not enough: the recipe a measurement targets must also declare no
    # instrumentation, so a recipe swap cannot smuggle an instrumented build into the lane.
    try:
        identities.require_release_recipe("performance")
    except ValueError as error:
        recipe_guarded = False
        recipe_detail = str(error)[:120]
    else:
        recipe_guarded = True
        recipe_detail = "require_release_recipe accepted the performance recipe"
    concurrent = _concurrent_view(view, race="required", opportunities={"goroutines_channels": 1})
    race_plan = next(
        (p for p in plugin.analysis_plans(_context(concurrent)) if p.analyzer_id == "race"), None
    )
    instrumented_image = race_plan.image_digest if race_plan is not None else ""
    return _case(
        "anti_pattern",
        "race-instrumentation-never-enters-the-measurement-lane",
        refused
        and not refused_flags
        and recipe_guarded
        and race_plan is not None
        and instrumented_image == identities.runtime.digest
        and instrumented_image != identities.performance.digest
        and race_plan.tool.version.endswith("-race")
        and race_plan.environment.get("CGO_ENABLED") == "1",
        "every instrumented flag is refused on a measurement plan, the measurement recipe "
        "declares no instrumentation, and the race run happens in the runtime image under a "
        "distinct instrumented tool identity",
        f"{detail}; unrefused_flags={refused_flags}; {recipe_detail}; "
        f"race_image=runtime:{instrumented_image == identities.runtime.digest} "
        f"tool={race_plan.tool.version if race_plan else None} "
        f"cgo={race_plan.environment.get('CGO_ENABLED') if race_plan else None}",
    )


def _measured_findings(observations: list[Any]) -> set[str]:
    return {
        o.check_id
        for o in observations
        if o.status == MeasurementStatus.MEASURED and not o.check_id.endswith(".scan")
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="go_conformance")
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
