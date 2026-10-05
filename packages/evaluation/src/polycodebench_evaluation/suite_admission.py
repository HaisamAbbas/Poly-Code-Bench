"""Executable admission for suite-mode language-plugin tasks (Technical Spec 11.2, E2E-04).

Every claim is made by *running* the authored solutions through the plugin's plans in an isolated
guest: the reference must pass the required inventory five times with identical outcomes, each
known-faulty variant must fail the cases it was written to break, the alternative-valid solution
must pass, a quality-defective variant must pass the functional gate while the analyzers report
its intended defect, and a timeout variant must be stopped and classified as a candidate failure.

What this module deliberately does not claim: performance baselines (Prompt 13), judge anchors
(Prompt 14), scoring replay (Prompt 15), generic evaluator stage integration (Prompt 12), a
production execution tier, or curator/owner approvals. Those stay in ``pending_gates``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from polycodebench_core.canonical import canonical_digest
from polycodebench_core.identity import new_entity_id
from polycodebench_core.models import Candidate, MeasurementStatus, Observation
from polycodebench_plugins_api import (
    AnalysisContext,
    FrozenTask,
    TaskDraft,
    TestGroupPlan,
)
from polycodebench_plugins_api.admission import (
    AdmissionCheck,
    SuiteAdmissionReport,
    VariantRun,
)
from polycodebench_plugins_api.protocols import ExecutableLanguagePlugin
from polycodebench_plugins_api.testreport import (
    GroupControl,
    TestCaseRecord,
    reconcile,
)

from polycodebench_evaluation.plan_runner import PlanRunner, digest_files, materialize_inputs

PENDING_GATES = (
    "generic-evaluator-stage-integration (Prompt 12)",
    "performance-baseline-canary-and-paired-measurement (Prompt 13)",
    "judge-anchors-and-human-calibration (Prompt 14)",
    "deterministic-scoring-replay-and-golden-calculations (Prompt 15)",
    "production-worker-execution-tier (Prompt 06 owner-deferred)",
    "curator-approval-and-task-freeze",
    "owner-rights-confirmation",
)
REFERENCE_REPETITIONS = 5
MAX_PARALLEL = 3
Gate = Literal["pass", "fail", "incomplete"]
_ORDER = {"pass": 0, "fail": 1, "incomplete": 2}


@dataclass(slots=True)
class CandidateEvaluation:
    gates: tuple[Gate, ...]
    reasons: tuple[str, ...]
    failed_cases: tuple[str, ...]
    outcome_digests: tuple[str, ...]
    durations_ms: tuple[int, ...]
    quality_only_pass: bool | None = None

    @property
    def gate(self) -> Gate:
        return max(self.gates, key=lambda g: _ORDER[g]) if self.gates else "incomplete"


def _candidate(task_id: str, files: Mapping[str, bytes]) -> Candidate:
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
                "payload_digest": digest_files(files),
                "artifact_ids": [],
                "frozen_at": None,
            }
        )
    )


def _within(path: str, allowed: list[str]) -> bool:
    return any(path == a or path.startswith(a.rstrip("/") + "/") for a in allowed)


def variant_files(
    package_files: Mapping[str, bytes],
    solution_path: str,
    allowed: list[str] | None = None,
) -> dict[str, bytes]:
    """Candidate files of one variant, with unchanged declared outputs from the visible starter.

    When the output contract's ``allowed`` paths are given and the solution path ends with one of
    them (``hidden/reference/src/lib.rs`` ends with ``src/lib.rs``), the variant root is what
    precedes it, so a crate keeps its ``src/`` layout. Otherwise the root is the solution file's
    own directory (single-module languages). Required outputs absent from that variant can reuse
    their public starter bytes from ``visible/repo``; this avoids duplicating public support files
    into the hidden archive while keeping the fixture candidate complete.
    """
    base = solution_path.rsplit("/", 1)[0] if "/" in solution_path else ""
    for path in sorted(allowed or (), key=len, reverse=True):
        if solution_path == path:
            base = ""
            break
        if solution_path.endswith("/" + path):
            base = solution_path[: -len(path) - 1]
            break
    prefix = base + "/" if base else ""
    selected = {
        path[len(prefix) :]: data for path, data in package_files.items() if path.startswith(prefix)
    }
    for path in allowed or ():
        if path not in selected:
            starter_path = f"visible/repo/{path}"
            if starter_path in package_files:
                selected[path] = package_files[starter_path]
    return selected


def _declared(plugin: object, name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """A plugin declaration, resolved whether it is a class attribute or a property.

    One plugin class can serve two language identities and still declare per-language values, which
    it can only do through ``@property``. Reading such a declaration with a bare ``getattr`` returns
    the property object, and because ``str.endswith`` accepts any object the mismatch is silent:
    the value simply never matches, and every path it was supposed to select is dropped.
    """
    value = getattr(plugin, name, default)
    if isinstance(value, property):
        value = value.fget(plugin)  # type: ignore[misc]
    if not isinstance(value, (tuple, list)):
        return default
    return tuple(str(item) for item in value)


def _families(observations: list[Observation]) -> set[str]:
    return {
        obs.issue_key.split(".")[1]
        for obs in observations
        if obs.status == MeasurementStatus.MEASURED and obs.issue_key
    }


def _group_classifications(view: FrozenTask) -> dict[str, str]:
    """Read gate roles from the frozen language-neutral oracle document.

    `InventoryGroup` intentionally contains only case identities, so the source oracle (preserved
    in `FrozenTask.inventory`) remains authoritative for whether a required group is acceptance or
    quality-only evidence. Older language fixtures without a classification default to acceptance.
    """
    inventory = view.inventory
    groups = inventory.get("groups", ()) if isinstance(inventory, Mapping) else ()
    return {
        str(group["group_id"]): str(group.get("classification", "acceptance"))
        for group in groups
        if isinstance(group, Mapping) and isinstance(group.get("group_id"), str)
    }


class SuiteAdmission:
    def __init__(
        self,
        plugin: ExecutableLanguagePlugin,
        runner: PlanRunner,
        *,
        profile_id: str = "admission-v1",
        execution_tier: Literal["local_fixture", "development_sandbox", "production_worker"] = (
            "development_sandbox"
        ),
        image_digests: tuple[str, ...],
    ) -> None:
        self._plugin = plugin
        self._runner = runner
        self._profile_id = profile_id
        self._tier = execution_tier
        self._images = image_digests
        self._gate = asyncio.Semaphore(MAX_PARALLEL)
        # Language-specific layout comes from the plugin; the defaults are the Python layout.
        self._prefix: str = getattr(plugin, "overlay_prefix", "")
        # Resolved, not read raw: a plugin may declare `candidate_suffixes` as a `@property` (the
        # JavaScript/TypeScript plugin does, because one class serves both identities), and a bare
        # `getattr` then yields the property object rather than its value. `str.endswith` accepts
        # any object, so no error surfaced - the suffix tuple silently matched nothing, every
        # candidate path list came out empty, and the analyzers scanned no source at all.
        self._suffixes: tuple[str, ...] = tuple(_declared(plugin, "candidate_suffixes", (".py",)))
        # Which directories under ``hidden/`` hold overlay files (tests, workloads). A language
        # whose hidden tests must live inside the package directory - Go's `_test.go` files need
        # the package's own identifiers - cannot use the Python ``hidden/tests/`` layout, so the
        # plugin declares its own roots rather than the engine growing a per-language branch.
        self._overlay_roots: tuple[str, ...] = tuple(
            getattr(plugin, "overlay_roots", ("hidden/tests/", "hidden/perf/"))
        )
        self._overlay_suffixes: tuple[str, ...] = _declared(plugin, "overlay_suffixes", ())
        self._trusted: Any = getattr(plugin, "trusted_inputs", None)

    # ----------------------------------------------------------------- evaluation

    async def evaluate(
        self,
        *,
        view: FrozenTask,
        candidate_files: Mapping[str, bytes],
        overlay: Mapping[str, bytes],
        label: str,
        repetitions: int,
        config: Mapping[str, bytes] | None = None,
    ) -> CandidateEvaluation:
        pool = dict(config or {})
        candidate = _candidate(view.task_id, candidate_files)
        build = self._plugin.build_plan(view, candidate)
        async with self._gate:
            build_run = await self._runner.run(
                build,
                materialize_inputs(build, {"candidate": candidate_files, "config": pool}),
                stage_id=f"adm-build-{label}"[:100],
            )
        verdict, detail = self._plugin.parse_build(build, build_run.reader())
        if verdict != "pass":
            gate: Gate = "fail" if verdict == "fail" else "incomplete"
            return CandidateEvaluation(
                (gate,), (f"build:{detail}",), (), (), (build_run.record.duration_ms,)
            )
        plan = self._plugin.test_plan(view)
        inventory = {g.group_id: g for g in self._plugin.inventory(view)}
        classifications = _group_classifications(view)
        records: list[TestCaseRecord] = []
        controls: list[GroupControl] = []
        durations: list[int] = []

        async def one(group_plan: TestGroupPlan, repetition: int) -> None:
            files = materialize_inputs(
                group_plan.plan,
                {"candidate": candidate_files, "overlay": overlay, "config": pool},
            )
            async with self._gate:
                run = await self._runner.run(
                    group_plan.plan,
                    files,
                    stage_id=f"adm-{label}-{group_plan.group_id}-{repetition}"[:100],
                )
            recs, control = self._plugin.parse_test_group(
                group_plan, inventory[group_plan.group_id], run.reader(), repetition=repetition
            )
            records.extend(recs)
            controls.append(control)
            durations.append(run.record.duration_ms)

        jobs: list[Coroutine[Any, Any, None]] = []
        for group in plan.groups:
            # `required` says whether the task must produce this evidence; classification says
            # whether it is an acceptance gate. Quality-only probes can be required evidence
            # without turning a resource leak or race into a wrong-answer failure.
            classification = classifications.get(group.group_id, "acceptance")
            count = repetitions if classification == "acceptance" else group.repetitions
            jobs.extend(one(group, rep) for rep in range(count))
        await asyncio.gather(*jobs)
        required = [
            g
            for g in inventory.values()
            if g.required and classifications.get(g.group_id, "acceptance") == "acceptance"
        ]
        required_ids = {g.group_id for g in required}
        gates: list[Gate] = []
        digests: list[str] = []
        reasons: list[str] = []
        for rep in range(repetitions):
            rep_records = [r for r in records if r.repetition == rep]
            rep_controls = [c for c in controls if c.repetition == rep]
            verdict_rep = reconcile(required, rep_records, rep_controls)
            gates.append(verdict_rep.gate)
            reasons.extend(verdict_rep.reasons)
            digests.append(
                canonical_digest(
                    sorted(
                        [r.group_id, r.case_id, r.outcome]
                        for r in rep_records
                        if r.group_id in required_ids
                    )
                )
            )
        quality_only = [
            g.model_copy(update={"required": True})
            for g in inventory.values()
            if classifications.get(g.group_id, "acceptance") == "quality_only"
        ]
        quality_pass: bool | None = None
        if quality_only:
            quality_ids = {g.group_id for g in quality_only}
            quality_pass = (
                reconcile(
                    quality_only,
                    [r for r in records if r.group_id in quality_ids],
                    [c for c in controls if c.group_id in quality_ids],
                ).gate
                == "pass"
            )
        failed = tuple(
            sorted(
                {r.case_id for r in records if r.outcome != "pass" and r.group_id in required_ids}
            )
        )
        return CandidateEvaluation(
            tuple(gates),
            tuple(dict.fromkeys(reasons)),
            failed,
            tuple(digests),
            tuple(durations),
            quality_pass,
        )

    async def analyze(
        self,
        *,
        view: FrozenTask,
        candidate_files: Mapping[str, bytes],
        label: str,
        overlay: Mapping[str, bytes] | None = None,
        config: Mapping[str, bytes] | None = None,
    ) -> tuple[list[Observation], tuple[str, ...]]:
        paths = tuple(sorted(p for p in candidate_files if p.endswith(self._suffixes)))
        context = AnalysisContext(
            task=view, candidate_digest=digest_files(candidate_files), candidate_paths=paths
        )
        observations: list[Observation] = []
        scans: list[str] = []
        pool: dict[str, bytes] = dict(config or {})
        if view.dependency_inventory and "config/dependencies.json" not in pool:
            pool["config/dependencies.json"] = json.dumps(
                {name: "0" for name in view.dependency_inventory}
            ).encode()
        for plan in self._plugin.analysis_plans(context):
            files = materialize_inputs(
                plan,
                {"candidate": candidate_files, "config": pool, "overlay": dict(overlay or {})},
            )
            async with self._gate:
                run = await self._runner.run(
                    plan, files, stage_id=f"adm-{plan.analyzer_id}-{label}"[:100]
                )
            parsed = self._plugin.parse_analysis(run.reader(), plan)
            observations.extend(parsed)
            scans.extend(
                f"{obs.check_id}={obs.status.value}"
                for obs in parsed
                if obs.check_id.endswith(".scan")
            )
        return self._plugin.normalize(observations), tuple(sorted(scans))

    async def workload_smoke(
        self,
        *,
        view: FrozenTask,
        candidate_files: Mapping[str, bytes],
        overlay: Mapping[str, bytes],
        config: Mapping[str, bytes] | None = None,
    ) -> tuple[bool, str]:
        """Run the smallest workload once on the reference: proves the workload is runnable
        under the pinned recipe. (Paired timing is the Prompt 13 stage.)

        What counts as proof is read from the plan's own declared outputs rather than assumed to be
        one document. Python's iteration runs a perf driver that writes ``out/perf.json`` with a
        verifier verdict and a duration; C compiles a workload that prints a checksum and declares
        ``out/perf.build.json`` plus the captured stdout. Hard-coding the Python shape made this
        check unsatisfiable for every other language - C could never emit a file no C plan builds.
        """
        plan = self._plugin.performance_plan(view)
        if plan is None:
            return True, "no performance workload declared"
        workload = min(plan.workloads, key=lambda w: w.scale)
        argv = tuple(
            part.replace("{scale}", str(workload.scale)).replace("{seed}", str(workload.input_seed))
            for part in plan.iteration_plan.argv
        )
        iteration = plan.iteration_plan.model_copy(update={"argv": argv})
        files = materialize_inputs(
            iteration,
            {"candidate": candidate_files, "overlay": overlay, "config": dict(config or {})},
        )
        async with self._gate:
            run = await self._runner.run(iteration, files, stage_id="adm-perf-smoke")
        declared = {output.path for output in plan.iteration_plan.outputs}
        if run.record.exit_code != 0:
            detail = (run.record.stderr_tail or "").strip()[-200:]
            stdout = next(
                (body for path, body in sorted(run.outputs.items()) if path.endswith(".out")),
                b"",
            )
            return (
                False,
                f"iteration exit {run.record.exit_code}: stderr={detail!r} "
                f"stdout={stdout[-200:]!r} outputs={sorted(run.outputs)}",
            )
        missing = sorted(
            path
            for path in declared
            # The captured runner triplet is produced by the supervisor wrapper, not the workload.
            if not path.endswith((".out", ".err"))
            and not path.endswith(".run.json")
            and path not in run.outputs
        )
        if missing:
            return False, f"iteration produced no {', '.join(missing)}"
        if "out/perf.json" in run.outputs:
            record = json.loads(run.outputs["out/perf.json"])
            ok = bool(record.get("verified")) and int(record.get("elapsed_ns", 0)) > 0
            return (
                ok,
                f"scale={workload.scale} elapsed_ns={record.get('elapsed_ns')} "
                f"verified={record.get('verified')}",
            )
        # No separate perf driver: the workload ran to a clean exit and printed its checksum or
        # metric record. C++ workloads emit JSON metrics, while several other languages use an
        # integer checksum.
        checksum_bytes = run.outputs.get("out/perf.out") or b""
        checksum = checksum_bytes.decode("utf-8", "replace").strip().splitlines()
        value = checksum[-1].strip() if checksum else ""
        valid_integer = value.isdigit()
        valid_metrics = False
        if value.startswith("{") and plan.metric_ids:
            try:
                metrics = json.loads(value)
            except ValueError:
                metrics = None
            valid_metrics = (
                isinstance(metrics, dict)
                and all(
                    isinstance(metrics.get(metric), (int, float))
                    and not isinstance(metrics.get(metric), bool)
                    and metrics[metric] > 0
                    for metric in plan.metric_ids
                )
            )
        ok = valid_integer or valid_metrics
        return (
            ok,
            f"scale={workload.scale} outputs={sorted(run.outputs)} "
            f"checksum={value if value else None!r}",
        )

    # ------------------------------------------------------------------ admission

    async def admit(
        self,
        *,
        files: Mapping[str, bytes],
        manifest: Mapping[str, Any],
        package_digest: str,
        precheck: Mapping[str, tuple[bool, str]],
    ) -> SuiteAdmissionReport:
        task = manifest["task"]
        draft = TaskDraft(
            task_id=task["task_id"],
            primary_language=task["primary_language"],
            manifest=dict(manifest),
            files=dict(files),
        )
        validation = self._plugin.validate_task(draft)
        view = self._plugin.freeze_view(draft, package_digest, int(task["version"]))
        overlay = {
            self._prefix + path.removeprefix("hidden/"): data
            for path, data in files.items()
            if path.startswith(self._overlay_roots)
            and (not self._overlay_suffixes or path.endswith(self._overlay_suffixes))
        }
        config: dict[str, bytes] = dict(self._trusted(files, view)) if self._trusted else {}
        allowed = list(manifest["output_contract"]["allowed_paths"])
        fixtures = list(manifest["fixtures"])

        async def run_fixture(
            fixture: Mapping[str, Any],
        ) -> tuple[Mapping[str, Any], dict[str, bytes], CandidateEvaluation]:
            candidate_files = variant_files(files, fixture["solution_path"], allowed)
            reps = REFERENCE_REPETITIONS if fixture["variant"] == "reference" else 1
            result = await self.evaluate(
                view=view,
                candidate_files=candidate_files,
                overlay=overlay,
                label=fixture["name"],
                repetitions=reps,
                config=config,
            )
            return fixture, candidate_files, result

        outcomes = await asyncio.gather(*(run_fixture(f) for f in fixtures))
        reference_files = next((cf for f, cf, _ in outcomes if f["variant"] == "reference"), {})
        smoke = await self.workload_smoke(
            view=view, candidate_files=reference_files, overlay=overlay, config=config
        )
        # A fixture is analyzed when it is one of the two variants whose findings are part of the
        # evidence contract, or when it *declares* lane expectations. The second condition is what
        # makes an ownership/leak/race fixture checkable at all: those variants pass the behaviour
        # contract, so only an analyzer run distinguishes them from the reference.
        analyses: dict[str, tuple[list[Observation], tuple[str, ...]]] = {}
        for fixture, candidate_files, _ in outcomes:
            declares_lane = bool(fixture["expectation"].get("expected_lane_findings"))
            if fixture["variant"] in {"reference", "quality_defective"} or declares_lane:
                analyses[fixture["name"]] = await self.analyze(
                    view=view,
                    candidate_files=candidate_files,
                    label=fixture["name"],
                    overlay=overlay,
                    config=config,
                )
        return self._report(
            task, package_digest, validation, outcomes, analyses, allowed, view, precheck, smoke
        )

    def _report(
        self,
        task: Mapping[str, Any],
        package_digest: str,
        validation: Any,
        outcomes: list[tuple[Mapping[str, Any], dict[str, bytes], CandidateEvaluation]],
        analyses: Mapping[str, tuple[list[Observation], tuple[str, ...]]],
        allowed: list[str],
        view: FrozenTask,
        precheck: Mapping[str, tuple[bool, str]],
        smoke: tuple[bool, str],
    ) -> SuiteAdmissionReport:
        checks: list[AdmissionCheck] = []

        def check(check_id: str, ok: bool, detail: str = "") -> None:
            checks.append(
                AdmissionCheck(
                    check_id=check_id, status="pass" if ok else "fail", detail=detail[:600]
                )
            )

        for key, (ok, detail) in sorted(precheck.items()):
            check(key, ok, detail)
        problems = [f"{i.code}:{i.path or ''}" for i in validation.issues if i.severity == "error"]
        check("plugin-task-validation", validation.ok, "; ".join(problems[:6]))
        runs: list[VariantRun] = []
        by_variant: dict[str, list[tuple[Mapping[str, Any], CandidateEvaluation]]] = {}
        for fixture, candidate_files, result in outcomes:
            by_variant.setdefault(fixture["variant"], []).append((fixture, result))
            observed, scans = analyses.get(fixture["name"], ([], ()))
            runs.append(
                VariantRun(
                    name=fixture["name"],
                    variant=fixture["variant"],
                    repetitions=len(result.gates),
                    gates=result.gates,
                    failing_cases=result.failed_cases,
                    reasons=result.reasons[:12],
                    outcome_digests=result.outcome_digests,
                    analyzer_scans=scans,
                    issue_families=tuple(sorted(_families(observed))),
                    durations_ms=result.durations_ms,
                    quality_only_pass=result.quality_only_pass,
                )
            )
            check(
                f"output-contract-{fixture['name']}",
                bool(candidate_files) and all(_within(p, allowed) for p in candidate_files),
                "variant files must stay inside the output contract",
            )

        def all_gate(variant: str, expected: Gate) -> bool:
            items = by_variant.get(variant, [])
            return bool(items) and all(set(r.gates) == {expected} for _, r in items)

        reference = by_variant.get("reference", [])
        check("reference-acceptance", all_gate("reference", "pass"))
        check(
            "five-reference-repetitions",
            bool(reference)
            and all(
                len(r.gates) == REFERENCE_REPETITIONS and len(set(r.outcome_digests)) == 1
                for _, r in reference
            ),
            "five identical outcome inventories required",
        )
        # Only wrong-answer fixtures declare `failing_cases`; crash/build fixtures and
        # analyzer-only fixtures are checked by their own gates below.
        faulty = [
            (f, r)
            for f, r in by_variant.get("faulty", [])
            if f["expectation"].get("expected_failure") not in {"candidate_crash", "build_error"}
            and not f["expectation"].get("expected_lane_findings")
        ]

        def matches_declared_fault(fixture: Mapping[str, Any], result: CandidateEvaluation) -> bool:
            expected_cases = fixture["expectation"].get("failing_cases") or ()
            return (
                set(result.gates) == {"fail"}
                and bool(expected_cases)
                and set(expected_cases) <= set(result.failed_cases)
            )

        check(
            "known-fault-rejection",
            bool(faulty) and all(matches_declared_fault(f, r) for f, r in faulty),
            "faulty variants must fail their declared cases",
        )
        check("alternative-solution-acceptance", all_gate("alternative", "pass"))
        timeouts = by_variant.get("timeout", [])
        check(
            "timeout-variants-rejected",
            bool(timeouts)
            and all(
                set(r.gates) == {"fail"} and any("timeout" in reason for reason in r.reasons)
                for _, r in timeouts
            ),
            "timeouts are candidate failures, not harness errors",
        )
        defective = by_variant.get("quality_defective", [])
        defect_ok = bool(defective)
        for fixture, result in defective:
            wanted = set(fixture["expectation"].get("expected_issue_families", ()))
            seen = _families(analyses[fixture["name"]][0])
            defect_ok = (
                defect_ok and set(result.gates) == {"pass"} and bool(wanted) and wanted <= seen
            )
        check(
            "quality-defect-detected",
            defect_ok,
            "quality-defective variants pass the functional gate yet show their intended defect",
        )
        quality_probe_ok = True
        quality_probe_detail: list[str] = []
        for fixture, _candidate_files, result in outcomes:
            expectation = fixture["expectation"]
            if "expected_quality_only_pass" not in expectation:
                continue
            expected = bool(expectation["expected_quality_only_pass"])
            if result.quality_only_pass is not expected:
                quality_probe_ok = False
                quality_probe_detail.append(
                    f"{fixture['name']}: expected {expected}, got {result.quality_only_pass}"
                )
        check(
            "required-quality-probe-outcomes",
            quality_probe_ok,
            "; ".join(quality_probe_detail[:6])
            or "all declared quality-only probe outcomes matched",
        )
        # A fixture whose defect only an instrumented lane can observe still has to be *shown* to
        # produce it. Without this the four ownership/leak/race/crash fixtures were declared and
        # then never checked, so a task could ship with a defect fixture that its own analyzers
        # do not detect. Each lane is checked against the families that lane actually reported.
        lane_ok = True
        lane_detail: list[str] = []
        for fixture, _, _ in outcomes:
            declared = dict(fixture["expectation"].get("expected_lane_findings") or {})
            if not declared:
                continue
            observed, _ = analyses.get(fixture["name"], ([], ()))
            seen = _families(observed)
            for lane, wanted in declared.items():
                missing = sorted(set(wanted) - seen)
                if missing:
                    lane_ok = False
                    lane_detail.append(f"{fixture['name']}/{lane} missing {','.join(missing)}")
        check(
            "instrumented-lane-defect-detected",
            lane_ok,
            "; ".join(lane_detail[:6]) or "every declared lane reported its families",
        )
        crash_faults = [
            (fixture, result)
            for fixture, _, result in outcomes
            if fixture["expectation"].get("expected_failure") in {"candidate_crash", "build_error"}
        ]
        check(
            "crash-and-build-fixtures-rejected",
            all(set(r.gates) == {"fail"} for _, r in crash_faults),
            "a crashing or unbuildable solution is a candidate failure, not a harness error",
        )
        required_scans = {f"{self._plugin.language_id}.{a}.scan" for a in view.required_analyzers}
        reference_scans = {
            entry.split("=")[0]: entry.split("=")[1]
            for f, _ in reference
            for entry in analyses[f["name"]][1]
        }
        check(
            "required-analyzer-compatibility",
            bool(reference) and all(reference_scans.get(s) == "measured" for s in required_scans),
            f"scans: {sorted(reference_scans.items())}",
        )
        check("performance-workload-smoke", smoke[0], smoke[1])
        check(
            "quality-opportunity-coverage",
            validation.ok and bool(view.applicable_dimensions),
            "dimensions: "
            + ",".join(sorted(str(getattr(d, "value", d)) for d in view.applicable_dimensions)),
        )
        document = {
            "schema_version": 1,
            "kind": "suite_admission_report",
            "profile_id": self._profile_id,
            "task_id": task["task_id"],
            "task_version": int(task["version"]),
            "package_digest": package_digest,
            "plugin_id": self._plugin.language_id,
            "execution_tier": self._tier,
            "image_digests": list(self._images),
            "checks": [c.model_dump(mode="json") for c in checks],
            "runs": [r.model_dump(mode="json") for r in runs],
            "executable_admission_passed": all(c.status == "pass" for c in checks),
            "pending_gates": list(PENDING_GATES),
            "quality_admission": "pending",
        }
        document["report_digest"] = canonical_digest(document)
        return SuiteAdmissionReport.model_validate_json(json.dumps(document))
