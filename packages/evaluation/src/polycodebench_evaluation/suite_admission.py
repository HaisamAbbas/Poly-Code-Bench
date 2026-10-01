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
    """Candidate files of one variant: the files beside its declared solution path.

    When the output contract's ``allowed`` paths are given and the solution path ends with one of
    them (``hidden/reference/src/lib.rs`` ends with ``src/lib.rs``), the variant root is what
    precedes it, so a crate keeps its ``src/`` layout. Otherwise the root is the solution file's
    own directory (single-module languages).
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
    return {
        path[len(prefix) :]: data for path, data in package_files.items() if path.startswith(prefix)
    }


def _families(observations: list[Observation]) -> set[str]:
    return {
        obs.issue_key.split(".")[1]
        for obs in observations
        if obs.status == MeasurementStatus.MEASURED and obs.issue_key
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
        self._suffixes: tuple[str, ...] = tuple(getattr(plugin, "candidate_suffixes", (".py",)))
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
            count = repetitions if group.required else group.repetitions
            jobs.extend(one(group, rep) for rep in range(count))
        await asyncio.gather(*jobs)
        required = [g for g in inventory.values() if g.required]
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
            g.model_copy(update={"required": True}) for g in inventory.values() if not g.required
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
        """Run the smallest workload once on the reference: proves the workload is runnable and
        its verifier accepts the reference. (Paired timing is the Prompt 13 stage.)"""
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
        if run.record.exit_code != 0 or "out/perf.json" not in run.outputs:
            return False, f"iteration exit {run.record.exit_code}: {run.record.stderr_tail[-160:]}"
        record = json.loads(run.outputs["out/perf.json"])
        ok = bool(record.get("verified")) and int(record.get("elapsed_ns", 0)) > 0
        return (
            ok,
            f"scale={workload.scale} elapsed_ns={record.get('elapsed_ns')} "
            f"verified={record.get('verified')}",
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
            if path.startswith(("hidden/tests/", "hidden/perf/"))
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
        analyses: dict[str, tuple[list[Observation], tuple[str, ...]]] = {}
        for fixture, candidate_files, _ in outcomes:
            if fixture["variant"] in {"reference", "quality_defective"}:
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
        faulty = by_variant.get("faulty", [])
        check(
            "known-fault-rejection",
            bool(faulty)
            and all(
                set(r.gates) == {"fail"}
                and bool(f["expectation"]["failing_cases"])
                and set(f["expectation"]["failing_cases"]) <= set(r.failed_cases)
                for f, r in faulty
            ),
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
