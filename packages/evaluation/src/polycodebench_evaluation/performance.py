"""Paired performance measurement (Technical Spec 13.1-13.3).

Everything the score needs and nothing it may invent:

* **Same worker.** Candidate and reference run inside one *reserved* guest, so the physical
  machine, the CPU allocation class and the environment digest are shared by construction and are
  re-verified from the guest's own report before any number is accepted.
* **Same runtime, no instrumented builds.** The speed lane accepts only plans without
  sanitizer/coverage/profiler/Miri markers, and the two sides must differ *only* in which source
  tree they read.
* **Paired and randomized.** Each measured pair runs the same input for both sides; which side goes
  first is drawn from the plan seed, so ordering bias cannot systematically favour one side.
* **Every iteration retained.** Cold start, warmups and measured iterations are separate records
  with their input and environment identity; compile time is its own record.
* **Blocks, not cherry-picks.** A canary validates each block; the *first valid* block wins by time
  order, every block is retained, and an invalid canary invalidates its whole block - including the
  iterations taken under it.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from polycodebench_core.canonical import canonical_digest, sha256_bytes
from polycodebench_core.identity import new_entity_id
from polycodebench_plugins_api import ExecutionPlan, FrozenTask, PerformancePlan, PlanInput
from polycodebench_plugins_api.protocols import ExecutableLanguagePlugin

from polycodebench_evaluation.efficiency import (
    MEMORY_FLOOR_KB,
    TIME_BREAKPOINT,
    TIME_FLOOR_NS,
    EfficiencyError,
    censored_time_outcome,
    efficiency_score,
    mad,
    median,
    relative_mad,
    scale_growth_diagnostic,
    stdev,
    weighted_geometric_mean,
)
from polycodebench_evaluation.efficiency import (
    ratio as floored_ratio,
)
from polycodebench_evaluation.evidence import RawArtifactRef
from polycodebench_evaluation.perfcontracts import (
    BlockRecord,
    BuildTiming,
    CanaryRecord,
    EfficiencyResult,
    HardwareRecord,
    IterationRecord,
    PerformanceEvidence,
    ReservationRecord,
    SeriesSummary,
    SpeedLaneCheck,
    WorkloadMeasurement,
)
from polycodebench_evaluation.plan_runner import (
    PlanRunner,
    ReservedGuest,
    digest_files,
    materialize_inputs,
)

CANDIDATE_ROOT = "cand"
REFERENCE_ROOT = "ref"
GUEST_MEMORY_METRIC = "guest_process_peak_rss_ru_maxrss_kb"
DEFAULT_CANARY_BAND = Decimal("1.10")
DEFAULT_CANARY_MAX_RELATIVE_MAD = Decimal("0.05")
CANARY_SAMPLES = 3
MAX_BLOCKS = 2

# Technical Spec 13.2: "No sanitizers, Miri, coverage or profilers in performance runs."
# Bare stems are matched deliberately: instrumentation is usually switched on by an
# environment key or a flag spelling we did not anticipate (`COVERAGE=1`, `-fsanitize=...`).
FORBIDDEN_SPEED_TOKENS = (
    "asan",
    "tsan",
    "ubsan",
    "msan",
    "sanitize",
    "coverage",
    "lcov",
    "miri",
    "valgrind",
    "profil",
    "pprof",
    "instrument",
)

# The guest reports its own machine with a python one-liner; no image rebuild is required.
_HARDWARE_PROBE = (
    "import json,os,platform,sys\n"
    "def read(p):\n"
    "    try:\n"
    "        return open(p).read().strip()\n"
    "    except Exception:\n"
    "        return None\n"
    "def num(paths):\n"
    "    for p in paths:\n"
    "        v = read(p)\n"
    "        if v and v.isdigit():\n"
    "            return int(v)\n"
    "    return None\n"
    "cpu = read('/proc/cpuinfo') or ''\n"
    "model = next(\n"
    "    (l.split(':', 1)[1].strip() for l in cpu.splitlines() if l.startswith('model name')),\n"
    "    'unknown',\n"
    ")\n"
    "print(json.dumps({\n"
    "    'cpu_model': model,\n"
    "    'machine': platform.machine(),\n"
    "    'logical_cpus': os.cpu_count() or 1,\n"
    "    'kernel': platform.release(),\n"
    "    'python': sys.version.split()[0],\n"
    "    'cgroup_memory_limit_bytes': num(\n"
    "        ['/sys/fs/cgroup/memory.max', '/sys/fs/cgroup/memory/memory.limit_in_bytes']\n"
    "    ),\n"
    "    'cgroup_peak_bytes': num(\n"
    "        ['/sys/fs/cgroup/memory.peak', '/sys/fs/cgroup/memory/memory.max_usage_in_bytes']\n"
    "    ),\n"
    "}))"
)


class PerformanceRefused(ValueError):
    """The measurement cannot be run defensibly (validation, reservation or lane failure)."""


@dataclass
class ExclusiveGuestReservation:
    """Exclusivity contract for the measurement window.

    A production worker satisfies this by holding one scheduler capacity slot for the whole
    measurement stage; the development implementation holds a dedicated guest. Either way no other
    stage runs on the same physical worker while a block is being measured.
    """

    runner: PlanRunner
    plan: ExecutionPlan
    files: Mapping[str, bytes]
    stage_id: str
    record: ReservationRecord | None = None
    released: bool = False

    @asynccontextmanager
    async def hold(self) -> AsyncIterator[ReservedGuest]:
        started = int(time.monotonic() * 1000)
        async with self.runner.reserved_guest(
            self.plan, self.files, stage_id=self.stage_id
        ) as guest:
            handle = guest.handle
            self.record = ReservationRecord(
                exclusive=True,
                worker_id=f"{handle.driver}:{handle.resource_id}",
                sandbox_id=handle.sandbox_id,
                fence=handle.fence,
                acquired_at_ms=started,
                released=False,
            )
            try:
                yield guest
            finally:
                # The evidence must record the reservation *as held* during measurement, so the
                # snapshot is not mutated here; `ReleasedReservation` exposes the post-run state.
                self.released = True


def specialize_for_side(
    plan: ExecutionPlan, side: Literal["candidate", "reference"]
) -> ExecutionPlan:
    """Re-root one side's candidate sources (``cand/`` or ``ref/``) without touching anything else.

    Only the candidate input paths and the bare ``work`` argv token change between the two plans;
    that near-identity is the evidence for "same flags, same runtime, same workload".
    """
    root = CANDIDATE_ROOT if side == "candidate" else REFERENCE_ROOT
    inputs = tuple(
        PlanInput(
            path=(
                item.path.replace("work/", f"{root}/", 1) if item.role == "candidate" else item.path
            ),
            role=item.role,
            digest=item.digest,
        )
        for item in plan.inputs
    )
    argv = tuple(root if part == "work" else part for part in plan.argv)
    environment = {key: value.replace("work", root) for key, value in plan.environment.items()}
    scope = tuple(item.replace("work/", f"{root}/", 1) for item in plan.scope)
    return plan.model_copy(
        update={"inputs": inputs, "argv": argv, "environment": environment, "scope": scope}
    )


def side_invariants(plan: ExecutionPlan) -> str:
    """Digest of everything both sides must share (image, flags, runtime, workload, environment)."""
    return str(
        canonical_digest(
            {
                "image": plan.image,
                "image_digest": plan.image_digest,
                "argv": [part for part in plan.argv if part not in {CANDIDATE_ROOT, REFERENCE_ROOT}]
                # the two roots are structurally the same slot, so they are named alike
                + ["root:<side>" for part in plan.argv if part in {CANDIDATE_ROOT, REFERENCE_ROOT}],
                "resources": plan.resources.model_dump(mode="json"),
                "exit_semantics": plan.exit_semantics.model_dump(mode="json"),
                "tool": plan.tool.model_dump(mode="json"),
                "trusted_inputs": [
                    item.path for item in plan.inputs if item.role in {"overlay", "config"}
                ],
                "environment": [
                    f"{key}={value}"
                    for key, value in sorted(plan.environment.items())
                    if CANDIDATE_ROOT not in value and REFERENCE_ROOT not in value
                ],
                "parser_id": plan.parser_id,
            }
        )
    )


def check_speed_lane(plan: ExecutionPlan) -> SpeedLaneCheck:
    """Refuse instrumented or profiling builds in the speed lane."""
    # The offending token is recorded *with its position in the plan*, so a reviewer can see
    # whether the marker came from the build flags, the environment or the image reference.
    haystacks = {
        "argv": " ".join(plan.argv).lower(),
        # environment *keys* matter as much as values: COVERAGE=1 or RUSTFLAGS=-Cinstrumentation
        # is how instrumentation is usually switched on
        "environment": " ".join(
            [f"{key}={value}" for key, value in plan.environment.items()]
        ).lower(),
        "image": plan.image.lower(),
        "tool": f"{plan.tool.name} {plan.tool.version} {plan.parser_id}".lower(),
    }
    found = tuple(
        sorted(
            f"{where}:{token}"
            for where, haystack in haystacks.items()
            for token in FORBIDDEN_SPEED_TOKENS
            if token in haystack
        )
    )
    return SpeedLaneCheck(
        accepted=not found,
        declared_recipe=getattr(plan.tool, "recipe", None),
        flags_digest=sha256_bytes(" ".join(plan.argv).encode()),
        forbidden_tokens_found=found,
    )


def hardware_probe_argv() -> tuple[str, ...]:
    return ("python", "-I", "-B", "-c", _HARDWARE_PROBE)


def paired_order(seed: int, workload_seed: int, iteration: int) -> bool:
    """Deterministic pair ordering: True when the candidate runs first.

    Derived from the plan seed, the workload's own input seed and the iteration index, so the
    order is reproducible from evidence and never from wall-clock or process state.
    """
    digest = hashlib.sha256(f"{seed}:{workload_seed}:{iteration}".encode()).digest()
    return digest[0] % 2 == 0


def _parse_probe(payload: str) -> dict[str, Any]:
    start = payload.find("{")
    if start == -1:
        raise PerformanceRefused("hardware probe produced no JSON object")
    try:
        document = json.loads(payload[start:])
    except ValueError as error:
        raise PerformanceRefused(f"hardware probe is not JSON: {error}") from None
    if not isinstance(document, dict):
        raise PerformanceRefused("hardware probe is not an object")
    return document


def _iteration_json(raw: bytes) -> dict[str, Any]:
    """Parse one guest iteration record.

    An absent or unparsable record is treated as *no* measurement, never as a fast one.
    """
    if not raw:
        return {}
    try:
        document = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        return {}
    if isinstance(document, dict) and document.get("schema") == "pcb-perf-iteration-v1":
        return document
    return {}


@dataclass
class _BlockOutcome:
    canary: CanaryRecord
    iterations: list[IterationRecord] = field(default_factory=list)
    status: Literal["valid", "invalid_canary", "invalid_iterations"] = "valid"
    reason: str = ""


class PerformanceRunner:
    def __init__(
        self,
        plugin: ExecutableLanguagePlugin,
        *,
        execution_tier: Literal[
            "local_fixture", "development_sandbox", "production_worker"
        ] = "development_sandbox",
        plan_seed: int = 0,
        canary_band: Decimal = DEFAULT_CANARY_BAND,
        canary_max_relative_mad: Decimal = DEFAULT_CANARY_MAX_RELATIVE_MAD,
        hardware_baseline: Mapping[str, Any] | None = None,
        canary_baseline_ns: Decimal | None = None,
        dedicated_hardware: bool = False,
        build: Callable[[ExecutionPlan, Mapping[str, bytes]], Awaitable[int]] | None = None,
    ) -> None:
        self._plugin = plugin
        self._tier = execution_tier
        self._plan_seed = plan_seed
        self._canary_band = canary_band
        self._canary_max_mad = canary_max_relative_mad
        self._hardware_baseline = dict(hardware_baseline or {})
        self._canary_baseline = canary_baseline_ns
        self._dedicated = dedicated_hardware
        self._build = build or self._refuse_build

    async def _refuse_build(self, plan: ExecutionPlan, files: Mapping[str, bytes]) -> int:
        raise PerformanceRefused(
            "no build callable was supplied; compile time must be measured, not assumed"
        )

    # ------------------------------------------------------------------ validation

    def validate(
        self,
        plan: PerformancePlan | None,
        reference_digest_expected: str | None,
    ) -> tuple[PerformancePlan, SpeedLaneCheck, list[str]]:
        """Plan validation (Technical Spec 13.1). Raises rather than downgrading silently."""
        if plan is None:
            raise PerformanceRefused("this task declares no performance workload")
        if plan.measured_iterations < 1 or plan.warmup_iterations < 0:
            raise PerformanceRefused("iteration counts must be positive")
        if sum(workload.weight_bp for workload in plan.workloads) != 10_000:
            raise PerformanceRefused("workload weights must sum to 10000 basis points")
        if len({workload.workload_id for workload in plan.workloads}) != len(plan.workloads):
            raise PerformanceRefused("workload identifiers must be unique")
        reasons: list[str] = []
        lane = check_speed_lane(plan.iteration_plan)
        if not lane.accepted:
            reasons.append(f"speed_lane_forbidden:{','.join(lane.forbidden_tokens_found)}")
        if (
            reference_digest_expected is not None
            and plan.reference_digest is not None
            and plan.reference_digest != reference_digest_expected
        ):
            reasons.append("reference_digest_conflicts_with_task_declaration")
        declared_class = self._hardware_baseline.get("hardware_class")
        if declared_class is not None and declared_class != plan.hardware_class:
            reasons.append(f"hardware_class_mismatch:{declared_class}!={plan.hardware_class}")
        if reasons:
            raise PerformanceRefused("; ".join(reasons))
        return plan, lane, []

    # ------------------------------------------------------------------ measurement

    async def measure(
        self,
        *,
        view: FrozenTask,
        plan: PerformancePlan,
        candidate_files: Mapping[str, bytes],
        reference_files: Mapping[str, bytes],
        overlay: Mapping[str, bytes],
        config: Mapping[str, bytes],
        build_plans: Mapping[str, ExecutionPlan],
        reservation: ExclusiveGuestReservation,
        reference_digest_expected: str | None = None,
    ) -> PerformanceEvidence:
        plan, lane, _ = self.validate(plan, reference_digest_expected)

        candidate_plan = specialize_for_side(plan.iteration_plan, "candidate")
        reference_plan = specialize_for_side(plan.iteration_plan, "reference")
        invariants = side_invariants(candidate_plan)
        if invariants != side_invariants(reference_plan):  # pragma: no cover - structural guard
            raise PerformanceRefused("candidate and reference plans are not equivalent")

        reservation.files = self._stage_everything(
            candidate_plan, candidate_files, reference_files, overlay, config
        )
        raw_refs: list[RawArtifactRef] = []

        async with reservation.hold() as guest:
            reservation_record = reservation.record
            assert reservation_record is not None
            probe_record = await guest.execute(
                candidate_plan, hardware_probe_argv(), timeout_seconds=30
            )
            hardware = self._hardware_record(
                _parse_probe(probe_record.stdout_tail), candidate_plan, reservation_record
            )
            builds = await self._build_both(
                build_plans, candidate_files, reference_files, overlay, config
            )

            blocks: list[BlockRecord] = []
            # Every block's iterations are retained, including invalidated ones: they are
            # evidence of what happened, never a score input. Aggregation reads only the
            # selected block.
            retained: list[IterationRecord] = []
            chosen_iterations: list[IterationRecord] = []
            selected_block: int | None = None
            for block_index in range(MAX_BLOCKS):
                outcome = await self._run_block(
                    guest=guest,
                    plan=plan,
                    candidate_plan=candidate_plan,
                    reference_plan=reference_plan,
                    hardware=hardware,
                    raw_refs=raw_refs,
                    block_index=block_index,
                )
                retained.extend(outcome.iterations)
                blocks.append(
                    BlockRecord(
                        block_index=block_index,
                        started_first=block_index == 0,
                        status=outcome.status,
                        canary=outcome.canary,
                        reason=outcome.reason,
                        iterations=len(outcome.iterations),
                    )
                )
                if outcome.status == "valid" and selected_block is None:
                    selected_block = block_index
                    chosen_iterations = outcome.iterations

            lane_status = "complete" if selected_block is not None else "invalid"
            measurements = self._aggregate(plan, chosen_iterations, lane_status)
            efficiency = self._efficiency(plan, measurements, lane_status)
            reference_digest = digest_files(reference_files)
            if (
                reference_digest_expected is not None
                and reference_digest != reference_digest_expected
            ):
                raise PerformanceRefused(
                    "staged reference digest does not match the expected reference identity"
                )
            gate, gate_reason = self._hardware_gate(hardware)
            evidence = PerformanceEvidence(
                evaluation_id=new_entity_id(),
                task_id=view.task_id,
                task_version=view.task_version,
                plugin_id=self._plugin.language_id,
                plan_id=plan.plan_id,
                plan_digest=str(canonical_digest(plan.model_dump(mode="json"))),
                execution_tier=self._tier,
                lane="performance",
                reservation=reservation_record,
                hardware=hardware,
                speed_lane=lane,
                candidate_digest=digest_files(candidate_files),
                reference_digest=reference_digest,
                reference_digest_expected=reference_digest_expected,
                environment_digest=invariants,
                build=builds,
                blocks=tuple(blocks),
                selected_block=selected_block,
                iterations=tuple(retained),
                workloads=tuple(measurements),
                efficiency=efficiency,
                pair_order_seed=self._plan_seed,
                warmup_iterations=plan.warmup_iterations,
                measured_iterations=plan.measured_iterations,
                hardware_gate=gate,
                hardware_gate_reason=gate_reason,
                raw_artifacts=tuple(raw_refs),
            )
        digest = canonical_digest(evidence.model_dump(mode="json", exclude={"report_digest"}))
        return evidence.model_copy(update={"report_digest": digest})

    # ------------------------------------------------------------------ staging

    def _stage_everything(
        self,
        candidate_plan: ExecutionPlan,
        candidate_files: Mapping[str, bytes],
        reference_files: Mapping[str, bytes],
        overlay: Mapping[str, bytes],
        config: Mapping[str, bytes],
    ) -> dict[str, bytes]:
        """Stage both source trees plus trusted overlays in one guest."""
        files = materialize_inputs(
            candidate_plan,
            {"candidate": candidate_files, "overlay": overlay, "config": config},
        )
        candidate_inputs = [item for item in candidate_plan.inputs if item.role == "candidate"]
        for item in candidate_inputs:
            source = item.path.removeprefix(f"{CANDIDATE_ROOT}/")
            data = reference_files.get(source)
            if data is None:
                raise PerformanceRefused(f"reference is missing {source!r}")
            if item.digest is not None and sha256_bytes(data) != item.digest:
                raise PerformanceRefused(f"reference {source!r} does not match its declared digest")
            files[item.path.replace(f"{CANDIDATE_ROOT}/", f"{REFERENCE_ROOT}/", 1)] = data
        return files

    def _hardware_record(
        self, probe: Mapping[str, Any], plan: ExecutionPlan, reservation: ReservationRecord
    ) -> HardwareRecord:
        expected = self._hardware_baseline
        if expected:
            drifted = [
                key
                for key in ("cpu_model", "machine", "logical_cpus", "kernel")
                if key in expected and str(expected[key]) != str(probe.get(key))
            ]
            if drifted:
                raise PerformanceRefused(f"hardware identity drifted: {','.join(sorted(drifted))}")
        return HardwareRecord(
            cpu_model=str(probe.get("cpu_model", "unknown")),
            machine=str(probe.get("machine", "unknown")),
            logical_cpus=int(probe.get("logical_cpus", 1)),
            kernel=str(probe.get("kernel", "unknown")),
            cgroup_memory_limit_bytes=_probe_int(probe, "cgroup_memory_limit_bytes"),
            cgroup_peak_bytes=_probe_int(probe, "cgroup_peak_bytes"),
            python_runtime=str(probe.get("python", "unknown")),
            guest_image_digest=plan.image_digest,
            dedicated=self._dedicated,
            homogeneous=True,
            allocation_class=f"cpu_millis={plan.resources.cpu_millis}",
        )

    async def _build_both(
        self,
        build_plans: Mapping[str, ExecutionPlan],
        candidate_files: Mapping[str, bytes],
        reference_files: Mapping[str, bytes],
        overlay: Mapping[str, bytes],
        config: Mapping[str, bytes],
    ) -> tuple[BuildTiming, ...]:
        """Compile time is measured, recorded separately, and never part of an iteration."""
        timings: list[BuildTiming] = []
        for side, files in (("candidate", candidate_files), ("reference", reference_files)):
            plan = build_plans.get(side)
            if plan is None:
                raise PerformanceRefused(f"a {side} build plan is required before measuring")
            materialized = materialize_inputs(
                plan, {"candidate": files, "overlay": overlay, "config": config}
            )
            duration_ms = await self._build(plan, materialized)
            timings.append(
                BuildTiming(
                    side=side,  # type: ignore[arg-type]
                    duration_ms=max(0, duration_ms),
                    image_digest=plan.image_digest,
                    flags_digest=side_invariants(plan),
                )
            )
        return tuple(timings)

    # ------------------------------------------------------------------ blocks

    async def _run_block(
        self,
        *,
        guest: ReservedGuest,
        plan: PerformancePlan,
        candidate_plan: ExecutionPlan,
        reference_plan: ExecutionPlan,
        hardware: HardwareRecord,
        raw_refs: list[RawArtifactRef],
        block_index: int = 0,
    ) -> _BlockOutcome:
        canary = await self._canary(guest, reference_plan, plan, raw_refs, block_index)
        if canary.status != "valid":
            return _BlockOutcome(canary=canary, status="invalid_canary", reason=canary.reason)
        records: list[IterationRecord] = []
        for workload in plan.workloads:
            records.extend(
                await self._measure_workload(
                    guest=guest,
                    workload=workload,
                    warmups=plan.warmup_iterations,
                    measured=plan.measured_iterations,
                    candidate_plan=candidate_plan,
                    reference_plan=reference_plan,
                    environment_digest=str(canonical_digest(candidate_plan.environment)),
                    raw_refs=raw_refs,
                    block_index=block_index,
                )
            )
        unusable = [r for r in records if r.phase != "warmup" and r.outcome != "measured"]
        status: Literal["valid", "invalid_iterations"] = (
            "invalid_iterations" if unusable else "valid"
        )
        return _BlockOutcome(
            canary=canary,
            iterations=records,
            status=status,
            reason=f"iterations_not_usable:{len(unusable)}" if unusable else "",
        )

    async def _canary(
        self,
        guest: ReservedGuest,
        reference_plan: ExecutionPlan,
        plan: PerformancePlan,
        raw_refs: list[RawArtifactRef],
        block_index: int = 0,
    ) -> CanaryRecord:
        """A canary on the *reference* (trusted, image-resident) at the largest declared scale.

        The canary must itself be stable, so it runs long enough for process start-up noise to be
        negligible: at the smallest scale a run is a few milliseconds and its own relative MAD
        exceeds the stability band, which would invalidate every block for the wrong reason. It
        detects host contention and throttling drift. Its baseline is the admission-time value;
        when the caller supplies none, the first block's median is recorded as the baseline and
        flagged as such - it is never silently treated as frozen.
        """
        workload = max(plan.workloads, key=lambda item: item.scale)
        samples: list[Decimal] = []
        for index in range(CANARY_SAMPLES):
            record = await self._iterate(
                guest=guest,
                plan=reference_plan,
                workload=workload,
                side="reference",
                phase="warmup",
                iteration=index,
                order_index=index,
                label=f"canary-{block_index}-{index}",
                raw_refs=raw_refs,
                block_index=block_index,
            )
            if record.outcome == "measured" and record.elapsed_ns is not None:
                samples.append(Decimal(record.elapsed_ns))
        common = dict(
            canary_id="reference-largest-scale",
            band_ratio=str(self._canary_band),
            max_relative_mad=str(self._canary_max_mad),
            samples=len(samples),
        )
        if not samples:
            return CanaryRecord(
                **common,
                baseline_ns="0",
                median_ns="0",
                relative_mad="1",
                baseline_source=(
                    "frozen" if self._canary_baseline is not None else "measured_first_run"
                ),
                status="invalid",
                reason="canary_produced_no_usable_sample",
            )
        center = median(samples)
        spread = relative_mad(samples, TIME_FLOOR_NS)
        stable = spread <= self._canary_max_mad
        if self._canary_baseline is None:
            return CanaryRecord(
                **common,
                baseline_ns=str(center),
                median_ns=str(center),
                relative_mad=str(spread),
                baseline_source="measured_first_run",
                status="valid" if stable else "invalid",
                reason="" if stable else "canary_noise_above_band",
            )
        baseline = self._canary_baseline
        inside = baseline / self._canary_band <= center <= baseline * self._canary_band
        return CanaryRecord(
            **common,
            baseline_ns=str(baseline),
            median_ns=str(center),
            relative_mad=str(spread),
            baseline_source="frozen",
            status="valid" if inside and stable else "invalid",
            reason=""
            if inside and stable
            else ("canary_median_outside_band" if not inside else "canary_noise_above_band"),
        )

    async def _measure_workload(
        self,
        *,
        guest: ReservedGuest,
        workload: Any,
        warmups: int,
        measured: int,
        candidate_plan: ExecutionPlan,
        reference_plan: ExecutionPlan,
        environment_digest: str,
        raw_refs: list[RawArtifactRef],
        block_index: int = 0,
    ) -> list[IterationRecord]:
        records: list[IterationRecord] = []
        plans = {"candidate": candidate_plan, "reference": reference_plan}
        order_index = 0
        for index in range(warmups):
            # The first warmup runs with no prior state: it is the cold-start metric.
            phase = "cold_start" if index == 0 else "warmup"
            for side in ("candidate", "reference"):
                records.append(
                    await self._iterate(
                        guest=guest,
                        plan=plans[side],
                        workload=workload,
                        side=side,  # type: ignore[arg-type]
                        phase=phase,  # type: ignore[arg-type]
                        iteration=index,
                        order_index=order_index,
                        environment_digest=environment_digest,
                        label=f"{workload.workload_id}-{side}-w{index}",
                        raw_refs=raw_refs,
                        block_index=block_index,
                    )
                )
                order_index += 1
        for index in range(measured):
            sides = ["candidate", "reference"]
            if not paired_order(self._plan_seed, workload.input_seed, index):
                sides.reverse()
            for side in sides:
                records.append(
                    await self._iterate(
                        guest=guest,
                        plan=plans[side],
                        workload=workload,
                        side=side,  # type: ignore[arg-type]
                        phase="measured",
                        iteration=index,
                        order_index=order_index,
                        environment_digest=environment_digest,
                        label=f"{workload.workload_id}-{side}-m{index}",
                        raw_refs=raw_refs,
                        block_index=block_index,
                    )
                )
                order_index += 1
        return records

    async def _iterate(
        self,
        *,
        guest: ReservedGuest,
        plan: ExecutionPlan,
        workload: Any,
        side: Literal["candidate", "reference"],
        phase: Literal["warmup", "cold_start", "measured"],
        iteration: int,
        order_index: int,
        label: str,
        environment_digest: str = "",
        raw_refs: list[RawArtifactRef] | None = None,
        block_index: int = 0,
    ) -> IterationRecord:
        output_path = f"out/perf/{label}.json"
        argv = tuple(
            part.replace("{scale}", str(workload.scale)).replace("{seed}", str(workload.input_seed))
            for part in plan.argv
        )
        argv = tuple(
            part
            if not part.endswith("out/perf.json")
            else part[: -len("out/perf.json")] + output_path
            for part in argv
        )
        input_digest = str(
            canonical_digest(
                {
                    "scale": workload.scale,
                    "seed": workload.input_seed,
                    "workload": workload.workload_id,
                }
            )
        )

        def record_timed_out() -> IterationRecord:
            return IterationRecord(
                workload_id=workload.workload_id,
                side=side,
                iteration=iteration,
                phase=phase,
                block_index=block_index,
                scale=workload.scale,
                input_seed=workload.input_seed,
                input_digest=input_digest,
                elapsed_ns=None,
                peak_rss_kb=None,
                cgroup_peak_bytes=None,
                outcome="timed_out",
                verified=False,
                exit_code=None,
                duration_ms=0,
                order_index=order_index,
                environment_digest=environment_digest,
            )

        record = await guest.execute(plan, argv, timeout_seconds=plan.resources.timeout_seconds)
        if record.timed_out:
            return IterationRecord(
                record_timed_out().model_dump() | {"duration_ms": record.duration_ms}
            )
        # The guest writes one small JSON record per iteration; read it back rather than
        # parsing it out of stdout, so a chatty tool cannot be mistaken for a measurement.
        raw_record = (await guest.read_files([output_path])).get(output_path, b"")
        if raw_record and raw_refs is not None:
            raw_refs.append(
                RawArtifactRef(
                    stage=f"performance:{side}:{phase}:{workload.workload_id}:{iteration}",
                    path=output_path,
                    digest=sha256_bytes(raw_record),
                    size_bytes=len(raw_record),
                    format="json",
                )
            )
        document = _iteration_json(raw_record)
        verified = bool(document.get("verified"))
        elapsed = document.get("elapsed_ns")
        peak = document.get("peak_rss_kb")
        exit_code = record.exit_code
        if exit_code is not None and exit_code in plan.exit_semantics.findings:
            outcome: Literal["measured", "rejected_output", "failed", "timed_out"] = (
                "rejected_output" if not verified else "measured"
            )
        elif exit_code in plan.exit_semantics.success:
            outcome = "measured" if verified else "rejected_output"
        else:
            outcome = "failed"
        return IterationRecord(
            workload_id=workload.workload_id,
            side=side,
            iteration=iteration,
            phase=phase,
            block_index=block_index,
            scale=workload.scale,
            input_seed=workload.input_seed,
            input_digest=input_digest,
            elapsed_ns=int(elapsed) if isinstance(elapsed, int) else None,
            peak_rss_kb=int(peak) if isinstance(peak, int) else None,
            cgroup_peak_bytes=None,
            outcome=outcome,
            verified=verified,
            exit_code=exit_code,
            duration_ms=record.duration_ms,
            order_index=order_index,
            environment_digest=environment_digest,
        )

    # ------------------------------------------------------------------ aggregation

    def _aggregate(
        self,
        plan: PerformancePlan,
        records: Sequence[IterationRecord],
        lane_status: str,
    ) -> list[WorkloadMeasurement]:
        measurements: list[WorkloadMeasurement] = []
        for workload in plan.workloads:
            sides = {
                side: [
                    record
                    for record in records
                    if record.workload_id == workload.workload_id
                    and record.side == side
                    and record.phase == "measured"
                ]
                for side in ("candidate", "reference")
            }
            usable = {
                side: [r for r in side_records if r.outcome == "measured"]
                for side, side_records in sides.items()
            }
            candidate_times = [r.elapsed_ns for r in usable["candidate"] if r.elapsed_ns]
            reference_times = [r.elapsed_ns for r in usable["reference"] if r.elapsed_ns]
            status: Literal["measured", "censored", "invalid", "incomplete"] = "incomplete"
            time_ratio: str | None = None
            memory_ratio: str | None = None
            censored: Literal["zero_by_lower_bound", "insufficient_information"] | None = None
            if lane_status != "complete":
                status = "invalid"
            elif candidate_times and reference_times:
                time_ratio = str(
                    floored_ratio(
                        Decimal(str(median(candidate_times))),
                        Decimal(str(median(reference_times))),
                        TIME_FLOOR_NS,
                    )
                )
                candidate_memory = [
                    r.peak_rss_kb for r in usable["candidate"] if r.peak_rss_kb is not None
                ]
                reference_memory = [
                    r.peak_rss_kb for r in usable["reference"] if r.peak_rss_kb is not None
                ]
                if candidate_memory and reference_memory:
                    memory_ratio = str(
                        floored_ratio(
                            Decimal(str(median(candidate_memory))),
                            Decimal(str(median(reference_memory))),
                            MEMORY_FLOOR_KB,
                        )
                    )
                status = "measured"
            elif any(r.outcome == "timed_out" for r in sides["candidate"]) and reference_times:
                outcome, _bound = censored_time_outcome(
                    Decimal(plan.iteration_plan.resources.timeout_seconds),
                    Decimal(str(median(reference_times))),
                    TIME_BREAKPOINT,
                )
                censored = outcome
                status = "censored" if outcome == "zero_by_lower_bound" else "incomplete"
            growth = scale_growth_diagnostic(
                [
                    (record.scale, Decimal(record.elapsed_ns))  # type: ignore[arg-type]
                    for record in records
                    if record.side == "candidate"
                    and record.phase == "measured"
                    and record.elapsed_ns is not None
                    # each row reports its own growth against the lane's smallest scale
                    and record.scale <= workload.scale
                ]
            )
            measurements.append(
                WorkloadMeasurement(
                    workload_id=workload.workload_id,
                    scale=workload.scale,
                    weight_bp=workload.weight_bp,
                    input_seed=workload.input_seed,
                    memory_metric=GUEST_MEMORY_METRIC,
                    candidate=_summarize(sides["candidate"]),
                    reference=_summarize(sides["reference"]),
                    time_ratio=time_ratio,
                    memory_ratio=memory_ratio,
                    time_censored=censored,
                    status=status,
                    scale_growth={
                        key: (None if value is None else str(value))
                        for key, value in growth.items()
                    },
                )
            )
        return measurements

    def _efficiency(
        self,
        plan: PerformancePlan,
        measurements: Sequence[WorkloadMeasurement],
        lane_status: str,
    ) -> EfficiencyResult:
        if lane_status != "complete":
            return _incomplete_efficiency("no_valid_measurement_block")
        time_pairs: list[tuple[Decimal, int]] = []
        memory_pairs: list[tuple[Decimal, int]] = []
        censored_any = False
        for measurement in measurements:
            if measurement.status != "measured":
                if measurement.status == "censored":
                    censored_any = True
                else:
                    return _incomplete_efficiency("workload_without_usable_measurements")
            elif measurement.time_ratio is None or measurement.memory_ratio is None:
                return _incomplete_efficiency("missing_component_not_scored")
            else:
                time_pairs.append((Decimal(measurement.time_ratio), measurement.weight_bp))
                memory_pairs.append((Decimal(measurement.memory_ratio), measurement.weight_bp))
        if censored_any:
            time_pairs = []
        try:
            time_geomean = (
                weighted_geometric_mean(time_pairs, quantization="six_places")
                if time_pairs
                else None
            )
            memory_geomean = (
                weighted_geometric_mean(memory_pairs, quantization="six_places")
                if memory_pairs
                else None
            )
            if censored_any and time_geomean is None and memory_geomean is None:
                score = efficiency_score(
                    time_ratio=None,
                    memory_ratio=None,
                    time_censored="zero_by_lower_bound",
                    memory_censored="zero_by_lower_bound",
                )
            elif time_geomean is None or memory_geomean is None:
                return _incomplete_efficiency("missing_component_not_scored")
            else:
                score = efficiency_score(time_ratio=time_geomean, memory_ratio=memory_geomean)
        except EfficiencyError as error:
            return _incomplete_efficiency(f"efficiency_error:{error}")
        return EfficiencyResult(
            status="complete" if score is not None else "incomplete",
            time_geomean_ratio=None if time_geomean is None else str(time_geomean),
            memory_geomean_ratio=None if memory_geomean is None else str(memory_geomean),
            time_censored="zero_by_lower_bound" if censored_any else None,
            memory_censored=None,
            score=None if score is None else str(score),
            reasons=(),
        )

    def _hardware_gate(self, hardware: HardwareRecord) -> tuple[str, str]:
        if not hardware.dedicated:
            return (
                "blocked_shared_ci",
                "measurements ran on shared CI hardware; ordinary shared timings cannot establish "
                "a production comparison",
            )
        return "satisfied", "dedicated hardware with a homogeneous allocation class"


def _probe_int(probe: Mapping[str, Any], key: str) -> int | None:
    value = probe.get(key)
    return int(value) if isinstance(value, int) else None


def _incomplete_efficiency(reason: str) -> EfficiencyResult:
    return EfficiencyResult(
        status="incomplete",
        time_geomean_ratio=None,
        memory_geomean_ratio=None,
        time_censored=None,
        memory_censored=None,
        score=None,
        reasons=(reason,),
    )


def _summarize(records: Sequence[IterationRecord]) -> SeriesSummary:
    usable = [r for r in records if r.outcome == "measured" and r.elapsed_ns is not None]
    times = [Decimal(r.elapsed_ns) for r in usable]  # type: ignore[arg-type]
    memory = [Decimal(r.peak_rss_kb) for r in usable if r.peak_rss_kb is not None]  # type: ignore[arg-type]
    rejected = sum(1 for r in records if r.outcome != "measured")
    if not times:
        return SeriesSummary(
            samples=0,
            median_ns=None,
            median_peak_rss_kb=None,
            stdev_ns=None,
            mad_ns=None,
            relative_mad=None,
            min_ns=None,
            max_ns=None,
            rejected=rejected,
        )
    center = median(times)
    return SeriesSummary(
        samples=len(times),
        median_ns=str(center),
        median_peak_rss_kb=str(median(memory)) if memory else None,
        stdev_ns=str(stdev(times)),
        mad_ns=str(mad(times, center)),
        relative_mad=str(relative_mad(times, TIME_FLOOR_NS)),
        min_ns=str(min(times)),
        max_ns=str(max(times)),
        rejected=rejected,
    )
