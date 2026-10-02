"""Independent grading: connect frozen submissions to evidence (Technical Spec 12.1-12.5).

The evaluator never trusts candidate bytes for control: overlays, expected inventories and
config come from trusted storage (candidate bytes can never fill their roles), every plan runs
in a fresh guest through the same PlanRunner, acceptance is reconciled against the *declared*
inventory, analyzer exit semantics are supervised by the runtime rather than the tool, and all
issues are related to the baseline and deduplicated before a single normalized manifest is
frozen. Native tool metrics are reported separately from the stricter PolyCodeBench gate.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field  # noqa: F401
from typing import Any, Literal, cast

from polycodebench_core.canonical import canonical_digest, sha256_bytes
from polycodebench_core.identity import new_entity_id
from polycodebench_core.models import Candidate, MeasurementStatus, Observation
from polycodebench_plugins_api import (
    AnalysisContext,
    AnalysisPlan,
    FrozenTask,
    TestGroupPlan,
)
from polycodebench_plugins_api.protocols import ExecutableLanguagePlugin
from polycodebench_plugins_api.testreport import (
    GroupControl,
    InventoryGroup,
    TestCaseRecord,
    reconcile,
)

from polycodebench_evaluation.evidence import (
    AnalyzerEvidence,
    CaseEvidence,
    EvaluationEvidence,
    GroupVerdictEvidence,
    IssueEvidence,
    ProfileItemEvidence,
    PropertyEvidence,
    RawArtifactRef,
    Relation,
    ReviewItem,
    ScenarioEvidence,
    ToolRecord,
    native_metrics_for,
)
from polycodebench_evaluation.plan_runner import PlanRunner, digest_files, materialize_inputs

Gate = Literal["pass", "fail", "incomplete"]
_SEVERITY = {None: 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


class CandidateRejected(ValueError):
    """The submission failed digest/identity/path validation before any execution."""


def baseline_from_package(files: Mapping[str, bytes]) -> dict[str, bytes]:
    """The task's baseline source: files under ``visible/repo`` re-rooted to candidate paths."""
    return {
        path.removeprefix("visible/repo/"): data
        for path, data in files.items()
        if path.startswith("visible/repo/")
    }


def _family(issue_key: str) -> str:
    parts = issue_key.split(".")
    return parts[1] if len(parts) >= 3 else parts[-1]


def _path_of(obs: Observation) -> str | None:
    return obs.location.path if obs.location else None


@dataclass
class IssueGroup:
    issue_key: str
    members: list[Observation] = field(default_factory=list)


class Evaluator:
    def __init__(
        self,
        plugin: ExecutableLanguagePlugin,
        runner: PlanRunner,
        *,
        execution_tier: Literal[
            "local_fixture", "development_sandbox", "production_worker"
        ] = "development_sandbox",
        max_parallel: int = 3,
    ) -> None:
        self._plugin = plugin
        self._runner = runner
        self._tier = execution_tier
        self._gate = asyncio.Semaphore(max_parallel)
        self._trusted = getattr(plugin, "trusted_inputs", None)

    # ------------------------------------------------------------------ issue relations

    def _relations(
        self,
        candidate_issues: list[Observation],
        baseline_issues: list[Observation],
        candidate_files: Mapping[str, bytes],
        baseline_files: Mapping[str, bytes] | None,
    ) -> tuple[dict[str, str], list[IssueEvidence]]:
        """Relation of each candidate canonical issue to the baseline (Technical Spec 12.4).

        ``unchanged_in_scope`` debt sits in a file the candidate actually modified (so the
        candidate could have fixed it); ``unchanged_out_of_scope`` debt is carried over
        verbatim from the base revision and remains visible without candidate blame. Keys the
        candidate no longer reports are ``resolved``; same-family/same-file but different
        canonical key is ``unknown`` and stays reviewable instead of silently blamed or
        silently clean.
        """
        base_by_key = {o.issue_key: o for o in baseline_issues if o.issue_key}
        relations: dict[str, str] = {}
        resolutions: list[IssueEvidence] = []
        for obs in candidate_issues:
            key = obs.issue_key
            assert key is not None
            previous = base_by_key.get(key)
            if previous is not None:
                if _SEVERITY[obs.severity] > _SEVERITY[previous.severity]:
                    relations[key] = "worsened"
                else:
                    path = _path_of(obs)
                    same_file = (
                        path is not None
                        and path in candidate_files
                        and baseline_files is not None
                        and path in baseline_files
                        and sha256_bytes(candidate_files[path])
                        != sha256_bytes(baseline_files[path])
                    )
                    relations[key] = "unchanged_in_scope" if same_file else "unchanged_out_of_scope"
                continue
            # No exact match: did the same family move location (ambiguous) or is it new?
            family = _family(key)
            same_family = [
                o for o in baseline_issues if o.issue_key and _family(o.issue_key) == family
            ]
            same_path = [o for o in same_family if _path_of(o) == _path_of(obs)]
            if same_path:
                relations[key] = "unknown"
            else:
                relations[key] = "introduced"
        candidate_keys = {o.issue_key for o in candidate_issues if o.issue_key}
        for base_obs in baseline_issues:
            key = base_obs.issue_key
            if key is None or key in candidate_keys:
                continue
            family_moved = any(
                o.issue_key and _family(o.issue_key) == _family(key) for o in candidate_issues
            )
            relations_note: str | None = None
            if family_moved:
                relations_note = "same family re-reported elsewhere; mapping ambiguous"
            resolutions.append(
                IssueEvidence(
                    issue_key=key,
                    relation="unknown" if family_moved else "resolved",
                    owner=None,
                    severity=base_obs.severity,
                    confidence=base_obs.confidence,
                    path=_path_of(base_obs),
                    start_line=base_obs.location.start_line if base_obs.location else None,
                    end_line=base_obs.location.end_line if base_obs.location else None,
                    tools=tuple(sorted({base_obs.check_id})),
                    ambiguous=family_moved,
                    explanation=relations_note,
                )
            )
        return relations, resolutions

    def _issue_entries(
        self,
        groups: dict[str, IssueGroup],
        relations: dict[str, str],
        profile: Any,
        primary_of: dict[str, Observation],
    ) -> list[IssueEvidence]:
        entries: list[IssueEvidence] = []
        for key in sorted(groups):
            members = groups[key].members
            primary = primary_of[key]
            mapping = profile.resolve(primary.check_id) if profile is not None else None
            owner = profile.owner(primary.check_id) if profile is not None else None
            entries.append(
                IssueEvidence(
                    issue_key=key,
                    relation=cast(Relation, relations.get(key, "unknown")),
                    owner=owner,
                    severity=primary.severity,
                    confidence=primary.confidence,
                    path=_path_of(primary),
                    start_line=primary.location.start_line if primary.location else None,
                    end_line=primary.location.end_line if primary.location else None,
                    tools=tuple(sorted({m.check_id for m in members})),
                    ambiguous=relations.get(key) == "unknown",
                    applicability_rule=mapping.applicability if mapping is not None else None,
                    explanation=primary.explanation,
                )
            )
        return entries

    # ------------------------------------------------------------------ helpers

    async def _analysis_side(
        self,
        *,
        view: FrozenTask,
        files: Mapping[str, bytes],
        overlay: Mapping[str, bytes],
        config: Mapping[str, bytes],
        purpose: Literal["candidate", "baseline"],
        label: str,
    ) -> tuple[list[Observation], list[AnalyzerEvidence], dict[str, bytes]]:
        candidate_paths = tuple(
            sorted(
                p
                for p in files
                if p.endswith(tuple(getattr(self._plugin, "candidate_suffixes", (".py",))))
            )
        )
        context = AnalysisContext(
            task=view,
            candidate_digest=digest_files(files),
            candidate_paths=candidate_paths,
            purpose=purpose,
        )
        observations: list[Observation] = []
        evidences: list[AnalyzerEvidence] = []
        outputs: dict[str, bytes] = {}
        pool: dict[str, bytes] = dict(config)
        if view.dependency_inventory and "config/dependencies.json" not in pool:
            import json

            pool["config/dependencies.json"] = json.dumps(
                {name: "0" for name in view.dependency_inventory}
            ).encode()
        for plan in self._plugin.analysis_plans(context):
            try:
                materialized = materialize_inputs(
                    plan, {"candidate": files, "config": pool, "overlay": dict(overlay)}
                )
            except Exception as exc:  # plan/config/dependency mismatch: explicit incomplete
                evidences.append(self._failed_analyzer(plan, purpose, f"input: {exc}"))
                continue
            async with self._gate:
                run = await self._runner.run(
                    plan, materialized, stage_id=f"eval-{plan.analyzer_id}-{purpose}-{label}"[:100]
                )
            parsed = self._plugin.parse_analysis(run.reader(), plan)
            observations.extend(parsed)
            outputs.update(run.outputs)
            evidences.append(
                self._analyzer_evidence(plan, run, parsed, purpose, view.required_analyzers)
            )
        return observations, evidences, outputs

    def _analyzer_evidence(
        self,
        plan: AnalysisPlan,
        run: Any,
        parsed: list[Observation],
        purpose: Literal["candidate", "baseline"],
        required: tuple[str, ...] | list[str],
    ) -> AnalyzerEvidence:
        from polycodebench_plugins_api.results import plan_status

        status, record = plan_status(plan, run.reader())
        scan = next((o for o in parsed if o.check_id.endswith(".scan")), None)
        measured = [o for o in parsed if o.status == MeasurementStatus.MEASURED]
        # A findings exit is a *complete* scan; only tool_error/timed_out/output_missing or a
        # non-measured scan observation are incomplete.
        complete = status in {"completed", "completed_with_findings"} and (
            scan is None or scan.status == MeasurementStatus.MEASURED
        )
        raw = tuple(
            RawArtifactRef(
                stage=f"analysis:{plan.analyzer_id}:{purpose}",
                path=path,
                digest=sha256_bytes(data),
                size_bytes=len(data),
                format=next((o.format for o in plan.outputs if o.path == path), "json"),
            )
            for path, data in sorted(run.outputs.items())
        )
        return AnalyzerEvidence(
            tool=ToolRecord(
                analyzer_id=plan.analyzer_id,
                side=purpose,
                name=plan.tool.name,
                version=plan.tool.version,
                image_digest=plan.tool.image_digest,
                lock_digest=plan.tool.lock_digest,
                rule_bundle_digest=plan.tool.rule_bundle_digest,
                advisory_snapshot_digest=plan.tool.advisory_snapshot_digest,
                advisory_snapshot_state=plan.tool.advisory_snapshot_state,
                parser_version=plan.tool.parser_version,
                plan_id=plan.plan_id,
                parser_id=plan.parser_id,
                required=plan.analyzer_id in set(required),
                scope=tuple(str(s) for s in plan.scope),
            ),
            status=status,
            detail=""
            if record is None
            else f"exit={record.exit_code} timed_out={record.timed_out}",
            duration_ms=run.record.duration_ms,
            findings=len(measured),
            scan_status=None if scan is None else scan.status.value,
            scan_findings=None
            if scan is None or scan.status != MeasurementStatus.MEASURED
            else int(scan.value or 0),
            complete=complete,
            raw=raw,
        )

    def _failed_analyzer(
        self, plan: AnalysisPlan, purpose: Literal["candidate", "baseline"], detail: str
    ) -> AnalyzerEvidence:
        return AnalyzerEvidence(
            tool=ToolRecord(
                analyzer_id=plan.analyzer_id,
                side=purpose,
                name=plan.tool.name,
                version=plan.tool.version,
                image_digest=plan.tool.image_digest,
                lock_digest=plan.tool.lock_digest,
                rule_bundle_digest=plan.tool.rule_bundle_digest,
                advisory_snapshot_digest=plan.tool.advisory_snapshot_digest,
                advisory_snapshot_state=plan.tool.advisory_snapshot_state,
                parser_version=plan.tool.parser_version,
                plan_id=plan.plan_id,
                parser_id=plan.parser_id,
                required=False,
                scope=tuple(str(s) for s in plan.scope),
            ),
            status="tool_error",
            detail=detail[:300],
            duration_ms=0,
            findings=0,
            scan_status=None,
            scan_findings=None,
            complete=False,
            raw=(),
        )

    # ------------------------------------------------------------------ the graph

    async def evaluate(
        self,
        *,
        view: FrozenTask,
        candidate: Candidate,
        candidate_files: Mapping[str, bytes],
        overlay: Mapping[str, bytes],
        config: Mapping[str, bytes],
        allowed_paths: tuple[str, ...],
        baseline_files: Mapping[str, bytes] | None,
        gate_policy: Mapping[str, Any] | None = None,
        label: str = "evaluation",
    ) -> Evaluation:
        plugin = self._plugin

        def reject(reason: str) -> Evaluation:
            evidence = self._empty_evidence(
                view=view,
                candidate=candidate,
                overlay=overlay,
                config=config,
                allowed_paths=allowed_paths,
                baseline_files=baseline_files,
                gate="fail",
                reasons=(reason,),
            )
            return Evaluation(evidence=evidence, raw={}, observations=[], baseline_observations=[])

        # ---- 12.1 step 1: candidate digest and allowed changes
        if candidate.payload_digest != digest_files(candidate_files):
            return reject(f"candidate_digest_mismatch:{candidate.payload_digest}")
        if candidate.task_id != view.task_id or candidate.task_version != view.task_version:
            return reject("candidate_task_binding_mismatch")
        disallowed = sorted(
            p
            for p in candidate_files
            if not any(p == a or p.startswith(a.rstrip("/") + "/") for a in allowed_paths)
        )
        protected_hits = sorted(p for p in candidate_files if p in set(view.protected_paths))
        if disallowed or protected_hits:
            evidence = self._empty_evidence(
                view=view,
                candidate=candidate,
                overlay=overlay,
                config=config,
                allowed_paths=allowed_paths,
                baseline_files=baseline_files,
                gate="fail",
                reasons=("disallowed_paths:" + ",".join((disallowed + protected_hits)[:5]),),
            ).model_copy(
                update={
                    "allowed_paths_ok": False,
                    "disallowed_paths": tuple(disallowed + protected_hits),
                }
            )
            return Evaluation(evidence=evidence, raw={}, observations=[], baseline_observations=[])

        # ---- 12.1 step 2-3: fresh guest, build with task-declared flags
        build = plugin.build_plan(view, candidate)
        build_files = materialize_inputs(
            build, {"candidate": candidate_files, "config": dict(config)}
        )
        async with self._gate:
            build_run = await self._runner.run(build, build_files, stage_id=f"eval-build-{label}")
        build_verdict, build_detail = plugin.parse_build(build, build_run.reader())
        raw: dict[str, bytes] = {}
        raw_refs: list[RawArtifactRef] = []
        for path, data in build_run.outputs.items():
            raw[path] = data
            raw_refs.append(
                RawArtifactRef(
                    stage="build",
                    path=path,
                    digest=sha256_bytes(data),
                    size_bytes=len(data),
                    format="json",
                )
            )
        if build_verdict != "pass":
            gate: Gate = "fail" if build_verdict == "fail" else "incomplete"
            evidence = self._empty_evidence(
                view=view,
                candidate=candidate,
                overlay=overlay,
                config=config,
                allowed_paths=allowed_paths,
                baseline_files=baseline_files,
                gate=gate,
                reasons=(f"build:{build_detail}",),
                build_verdict=build_verdict,
                build_detail=build_detail,
                raw_refs=raw_refs,
            )
            return Evaluation(evidence=evidence, raw=raw, observations=[], baseline_observations=[])

        # ---- 12.1 step 4: mandatory acceptance groups vs expected inventory
        test_plan = plugin.test_plan(view)
        inventory = {g.group_id: g for g in plugin.inventory(view)}
        records: list[TestCaseRecord] = []
        controls: list[GroupControl] = []

        async def one(group_plan: TestGroupPlan, repetition: int) -> None:
            files = materialize_inputs(
                group_plan.plan,
                {"candidate": candidate_files, "overlay": overlay, "config": dict(config)},
            )
            async with self._gate:
                run = await self._runner.run(
                    group_plan.plan,
                    files,
                    stage_id=f"eval-{group_plan.group_id}-{repetition}-{label}"[:100],
                )
            recs, control = plugin.parse_test_group(
                group_plan, inventory[group_plan.group_id], run.reader(), repetition=repetition
            )
            records.extend(recs)
            controls.append(control)
            for path, data in run.outputs.items():
                raw[path] = data
                raw_refs.append(
                    RawArtifactRef(
                        stage=f"test:{group_plan.group_id}:r{repetition}",
                        path=path,
                        digest=sha256_bytes(data),
                        size_bytes=len(data),
                        format="jsonl",
                    )
                )

        await asyncio.gather(
            *(one(g, rep) for g in test_plan.groups for rep in range(g.repetitions))
        )
        required = [g for g in inventory.values() if g.required]
        verdict = reconcile(required, records, controls)
        gate = verdict.gate
        group_verdicts = tuple(
            GroupVerdictEvidence(
                group_id=g.group_id, required=g.required, verdict=g.verdict, reasons=g.reasons
            )
            for g in verdict.groups
        )

        # ---- 12.5 robustness scenarios: weighted, all repetitions, hard vs quality
        scenarios: list[ScenarioEvidence] = []
        scenario_gate_fail = False
        for scenario in cast(list[dict[str, Any]], view.inventory.get("robustness_scenarios", ())):
            group_id = scenario["group_id"]
            group_inventory = inventory.get(group_id)
            full_group = (
                InventoryGroup(
                    group_id=group_inventory.group_id,
                    required=True,
                    cases=group_inventory.cases,
                )
                if group_inventory
                else None
            )
            group_records = [r for r in records if r.group_id == group_id]
            group_controls = [c for c in controls if c.group_id == group_id]
            repetitions = sorted({c.repetition for c in group_controls})
            rep_verdicts: list[str] = []
            for rep in repetitions:
                if full_group is None:
                    rep_verdicts.append("incomplete")
                else:
                    rep_verdicts.append(
                        reconcile(
                            [full_group],
                            [r for r in group_records if r.repetition == rep],
                            [c for c in group_controls if c.repetition == rep],
                        ).gate
                    )
            passed = rep_verdicts.count("pass")
            expected = int(scenario["repetitions"])
            if (
                group_inventory is None
                or not repetitions
                or any(v == "incomplete" for v in rep_verdicts)
            ):
                status: Literal["passed", "failed", "incomplete"] = "incomplete"
                credit = 0
            elif passed == len(rep_verdicts) and len(rep_verdicts) >= expected:
                status = "passed"
                credit = int(scenario["weight_bp"])
            else:
                status = "failed"
                credit = 0
            hard = bool(scenario["hard_acceptance"])
            if hard and status != "passed":
                scenario_gate_fail = True
            scenarios.append(
                ScenarioEvidence(
                    scenario_id=scenario["scenario_id"],
                    group_id=group_id,
                    weight_bp=int(scenario["weight_bp"]),
                    repetitions=len(rep_verdicts),
                    passed_repetitions=passed,
                    hard_acceptance=hard,
                    status=status,
                    credit_bp=credit,
                    gate_effect=("hard_acceptance" if hard else "quality_weight_only"),
                    repetition_verdicts=tuple(rep_verdicts),
                )
            )
        if scenario_gate_fail and gate == "pass":
            gate = "fail"
            verdict = verdict.model_copy(
                update={"gate": "fail", "reasons": (*verdict.reasons, "hard_scenario_failed")}
            )
        complete_scenarios = [s for s in scenarios if s.status != "incomplete"]
        robustness_score_bp = (
            sum(s.credit_bp for s in scenarios)
            if scenarios and len(complete_scenarios) == len(scenarios)
            else None
        )

        # ---- 12.1 step 6: analyzers, only after the gate passes; base analysed too
        analyzers: list[AnalyzerEvidence] = []
        observations: list[Observation] = []
        baseline_observations: list[Observation] = []
        raw_c: list[Observation] = []
        out_b: dict[str, bytes] = {}
        # `language_profile` is the contract every executable plugin publishes (see
        # `LanguageProfileEvaluator` in the plugin API). Duck-typing `python_profile`/`rust_profile`
        # silently yielded None for every other language, which dropped that language's whole
        # quality section from the evaluation: findings were still recorded, but no item was ever
        # scored, so the profile looked empty rather than unevaluated.
        profile = getattr(plugin, "language_profile", None)
        incomplete_notes: list[str] = []
        reviews: list[ReviewItem] = []
        if gate == "pass":
            obs_c, ev_c, out_c = await self._analysis_side(
                view=view,
                files=candidate_files,
                overlay=overlay,
                config=config,
                purpose="candidate",
                label=label,
            )
            analyzers.extend(ev_c)
            # `raw_c` keeps every tool's report of an issue so the manifest can name all of
            # them; `observations` is the deduplicated, normalized evidence set.
            raw_c = obs_c
            observations = plugin.normalize(obs_c)
            if baseline_files is not None:
                obs_b, ev_b, out_b = await self._analysis_side(
                    view=view,
                    files=baseline_files,
                    overlay=overlay,
                    config=config,
                    purpose="baseline",
                    label=label,
                )
                analyzers.extend(ev_b)
                baseline_observations = plugin.normalize(obs_b)
            # Raw analyzer reports are retained as artifacts too, never only their summary
            for side, paths in (("candidate", out_c), ("baseline", out_b)):
                for path, data in paths.items():
                    raw[f"analysis/{side}/{path}"] = data
                    raw_refs.append(
                        RawArtifactRef(
                            stage=f"analysis:{side}",
                            path=path,
                            digest=sha256_bytes(data),
                            size_bytes=len(data),
                            format="json",
                        )
                    )
        else:
            # The gate failed: quality work must not run; only the failure is recorded.
            incomplete_notes.append("quality_work_gated_off:correctness_gate_not_pass")

        # ---- 12.2: required scans must be measured, never silently clean
        candidate_scans = {o.check_id: o for o in observations if o.check_id.endswith(".scan")}
        for analyzer in set(view.required_analyzers):
            prefix = f"{plugin.language_id}.{analyzer}.scan"
            scan = candidate_scans.get(prefix)
            if scan is None or scan.status != MeasurementStatus.MEASURED:
                incomplete_notes.append(f"required_scan_not_measured:{analyzer}")
                reviews.append(
                    ReviewItem(
                        issue_key=prefix,
                        reason="unsupported_required_scan",
                        detail="missing, crashed or unsupported required scan; never 'clean'",
                    )
                )

        # ---- 12.3/12.4: canonical issues, cross-tool dedup, baseline relations
        issue_members: dict[str, IssueGroup] = {}
        for obs in raw_c:
            if obs.issue_key is None or obs.status != MeasurementStatus.MEASURED:
                continue
            issue_members.setdefault(obs.issue_key, IssueGroup(obs.issue_key)).members.append(obs)
        baseline_issues = [
            o
            for o in baseline_observations
            if o.issue_key and o.status == MeasurementStatus.MEASURED
        ]
        # primary per key mirrors plugin.normalize (context wins, else max severity)
        primary_of: dict[str, Observation] = {}
        normalized_by_key = {o.issue_key: o for o in observations if o.issue_key}
        for key in issue_members:
            primary_of[key] = normalized_by_key.get(
                key, sorted(issue_members[key].members, key=lambda o: o.check_id)[0]
            )
        relations, resolutions = self._relations(
            list(primary_of.values()),
            baseline_issues,
            candidate_files,
            baseline_files,
        )
        issues = self._issue_entries(issue_members, relations, profile, primary_of)
        for obs in issues:
            if obs.ambiguous:
                reviews.append(
                    ReviewItem(
                        issue_key=obs.issue_key,
                        reason="ambiguous_baseline_mapping",
                        detail="same family at a different location; auto-relation suppressed",
                    )
                )
        for obs in list(observations):
            if obs.issue_key in relations and obs.status == MeasurementStatus.MEASURED:
                observations.remove(obs)
                observations.append(
                    obs.model_copy(
                        update={"baseline_relation": _OBS_RELATION[relations[obs.issue_key]]}
                    )
                )
        # ---- profile evaluation (applicability; no invented aggregate on missing evidence)
        profile_items: tuple[Any, ...] = ()
        diagnostic: int | None = None
        idiomatic: int | None = None
        profile_complete = False
        if gate == "pass" and profile is not None:
            quality_opportunities = view.quality.get("opportunities", {})
            opportunities: dict[str, int] = {}
            if isinstance(quality_opportunities, Mapping):
                for key, value in quality_opportunities.items():
                    if isinstance(value, int) and not isinstance(value, bool):
                        opportunities[str(key)] = value
            result = profile.evaluate(
                opportunities=opportunities,
                observations=observations,
                required_tools=view.required_analyzers,
            )
            diagnostic = result.diagnostic_score_bp
            idiomatic = result.idiom_score_bp
            profile_complete = result.complete
            profile_items = tuple(result.diagnostic) + tuple(result.idioms)

        case_evidence = tuple(
            CaseEvidence(
                group_id=r.group_id,
                case_id=r.case_id,
                required=r.required,
                repetition=r.repetition,
                outcome=r.outcome,
                reason=r.reason,
                duration_ms=r.duration_ms,
                input_seed=r.input_seed,
                expected_outcome_digest=r.expected_outcome_digest,
                stdout_digest=r.stdout_digest,
                stderr_digest=r.stderr_digest,
                execution_identity=r.execution_identity,
            )
            for r in sorted(records, key=lambda r: (r.group_id, r.case_id, r.repetition))
        )
        property_cases = [r for r in records if r.input_seed]
        hypothesis_info = _extract_hypothesis_info(raw)
        property_examples = (
            int(hypothesis_info["max_examples"])
            if hypothesis_info and isinstance(hypothesis_info.get("max_examples"), int)
            else _hypothesis_examples(view)
        )
        property_count = sum(
            1
            for g in cast(list[dict[str, Any]], view.inventory.get("groups", ()))
            for c in g.get("cases", ())
            if c.get("case_kind") == "property"
        )
        property_evidence = PropertyEvidence(
            engine=("cargo-test" if view.primary_language == "rust" else "hypothesis")
            if _hypothesis_examples(view) is not None or view.primary_language == "rust"
            else "pytest",
            engine_version=(
                str(hypothesis_info["version"])
                if hypothesis_info and hypothesis_info.get("version")
                else None
            ),
            deterministic_policy=(
                "hypothesis-derandomized"
                if hypothesis_info and hypothesis_info.get("derandomize") is True
                else ("cargo-test-source-seeded" if view.primary_language == "rust" else "unknown")
            ),
            examples_pinned=property_examples,
            case_timeout_seconds=_oracle_int(view, "case_timeout_seconds"),
            suite_timeout_seconds=_oracle_int(view, "suite_timeout_seconds"),
            seeds=tuple(sorted({r.input_seed for r in property_cases if r.input_seed})),
            cases=property_count,
        )
        evidence = EvaluationEvidence(
            evaluation_id=new_entity_id(),
            task_id=view.task_id,
            task_version=view.task_version,
            task_digest=view.task_digest,
            plugin_id=plugin.language_id,
            execution_tier=self._tier,
            candidate_digest=digest_files(candidate_files),
            baseline_digest=None if baseline_files is None else digest_files(baseline_files),
            overlay_digest=digest_files(overlay),
            config_digest=digest_files(config),
            inventory_digest=view.inventory_digest,
            allowed_paths_ok=not (disallowed or protected_hits),
            disallowed_paths=tuple(disallowed + protected_hits),
            build_verdict=build_verdict,
            build_detail=build_detail,
            gate=gate,
            gate_reasons=tuple(dict.fromkeys(verdict.reasons)),
            failed_cases=tuple(
                sorted({r.case_id for r in records if r.outcome != "pass" and r.required})
            ),
            group_verdicts=group_verdicts,
            scenarios=tuple(scenarios),
            robustness_score_bp=robustness_score_bp,
            cases=case_evidence,
            property_evidence=property_evidence,
            analyzers=tuple(analyzers),
            issues=tuple(issues),
            resolutions=tuple(resolutions),
            reviews=tuple(reviews),
            profile_items=tuple(
                ProfileItemEvidence(
                    item_id=str(i.item_id),
                    group=i.group,
                    weight_bp=i.weight_bp,
                    opportunities=i.opportunities,
                    unique_violations=i.unique_violations,
                    status=i.status,
                    score_bp=i.score_bp,
                    reasons=tuple(i.reasons),
                )
                for i in profile_items
            ),
            diagnostic_score_bp=diagnostic,
            idiom_score_bp=idiomatic,
            profile_complete=profile_complete,
            native_metrics=native_metrics_for(analyzers),
            incomplete=tuple(dict.fromkeys(incomplete_notes)),
            raw_artifacts=tuple(raw_refs),
            execution_note=(
                "candidate digest verified; candidate bytes never mint overlays/config/inventory; "
                "fresh guest per plan; exit semantics supervised; tool self-report is read-only"
            ),
        )
        digest = canonical_digest(evidence.model_dump(mode="json", exclude={"report_digest"}))
        evidence = evidence.model_copy(update={"report_digest": digest})
        return Evaluation(
            evidence=evidence,
            raw=raw,
            observations=observations,
            baseline_observations=baseline_observations,
        )

    def _empty_evidence(
        self,
        *,
        view: FrozenTask,
        candidate: Candidate,
        overlay: Mapping[str, bytes],
        config: Mapping[str, bytes],
        allowed_paths: tuple[str, ...],
        baseline_files: Mapping[str, bytes] | None,
        gate: Gate,
        reasons: tuple[str, ...],
        build_verdict: Literal["pass", "fail", "incomplete"] = "incomplete",
        build_detail: str = "not executed",
        raw_refs: list[RawArtifactRef] | None = None,
    ) -> EvaluationEvidence:
        evidence = EvaluationEvidence(
            evaluation_id=new_entity_id(),
            task_id=view.task_id,
            task_version=view.task_version,
            task_digest=view.task_digest,
            plugin_id=self._plugin.language_id,
            execution_tier=self._tier,
            candidate_digest=candidate.payload_digest,
            baseline_digest=None if baseline_files is None else digest_files(baseline_files),
            overlay_digest=digest_files(overlay),
            config_digest=digest_files(config),
            inventory_digest=view.inventory_digest,
            allowed_paths_ok=gate == "pass",
            disallowed_paths=(),
            build_verdict=build_verdict,
            build_detail=build_detail,
            gate=gate,
            gate_reasons=reasons,
            failed_cases=(),
            group_verdicts=(),
            scenarios=(),
            robustness_score_bp=None,
            cases=(),
            property_evidence=PropertyEvidence(
                engine="unknown",
                engine_version=None,
                deterministic_policy="unknown",
                examples_pinned=None,
                case_timeout_seconds=None,
                suite_timeout_seconds=None,
                seeds=(),
                cases=0,
            ),
            analyzers=(),
            issues=(),
            resolutions=(),
            reviews=(),
            profile_items=(),
            diagnostic_score_bp=None,
            idiom_score_bp=None,
            profile_complete=False,
            native_metrics={},
            incomplete=() if gate == "fail" else ("evaluation_incomplete",),
            raw_artifacts=tuple(raw_refs or ()),
            execution_note="rejected before full execution",
        )
        digest = canonical_digest(evidence.model_dump(mode="json", exclude={"report_digest"}))
        return evidence.model_copy(update={"report_digest": digest})


_OBS_RELATION = {
    "introduced": "introduced",
    "worsened": "worsened",
    "unchanged_in_scope": "unfixed",
    "unchanged_out_of_scope": "inherited",
    "unknown": "unknown",
}


def _extract_hypothesis_info(raw: Mapping[str, bytes]) -> dict[str, Any] | None:
    """The pytest report pins Hypothesis as derandomized; record its version/settings."""
    import json

    for path, data in raw.items():
        if not path.endswith(".jsonl"):
            continue
        for line in data.decode("utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            hypothesis = event.get("hypothesis")
            if isinstance(hypothesis, dict):
                return hypothesis
    return None


def _hypothesis_examples(view: FrozenTask) -> int | None:
    value = view.inventory.get("hypothesis_examples")
    return int(value) if isinstance(value, int) else None


def _oracle_int(view: FrozenTask, key: str) -> int | None:
    value = view.inventory.get(key)
    return int(value) if isinstance(value, int) else None


@dataclass
class Evaluation:
    evidence: EvaluationEvidence
    raw: dict[str, bytes]
    observations: list[Observation]
    baseline_observations: list[Observation]
