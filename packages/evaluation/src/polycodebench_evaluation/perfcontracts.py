"""Performance measurement contracts (Technical Spec 13). Language-neutral, strict, frozen.

These models are what a *score* can safely consume: every iteration, its input identity and the
guest hardware it ran on are retained; compile time is separate from measured time; a censored
timeout is labelled as a bound, never as a duration; and native tool or task numbers are kept out
of the PolyCodeBench efficiency value.
"""

from __future__ import annotations

from typing import Literal

from polycodebench_plugins_api import PluginModel
from pydantic import Field

from polycodebench_evaluation.evidence import RawArtifactRef

IterationOutcome = Literal["measured", "rejected_output", "failed", "timed_out"]
BlockStatus = Literal["valid", "invalid_canary", "invalid_iterations"]
LaneStatus = Literal["complete", "invalid", "incomplete"]


class HardwareRecord(PluginModel):
    """What the guest itself reports about the machine it ran on (Technical Spec 13.2)."""

    kind: Literal["hardware_record"] = "hardware_record"
    cpu_model: str
    machine: str
    logical_cpus: int = Field(ge=1)
    kernel: str
    cgroup_memory_limit_bytes: int | None
    cgroup_peak_bytes: int | None
    python_runtime: str
    guest_image_digest: str
    dedicated: bool
    homogeneous: bool
    # The CPU allocation class the supervisor pinned for this guest (SandboxSpec.cpu_millis).
    allocation_class: str


class IterationRecord(PluginModel):
    """One fresh-process iteration. Retained individually: nothing is averaged away."""

    kind: Literal["iteration_record"] = "iteration_record"
    workload_id: str
    side: Literal["candidate", "reference"]
    iteration: int = Field(ge=0)
    block_index: int = Field(ge=0)
    phase: Literal["warmup", "cold_start", "measured"]
    scale: int = Field(ge=1)
    input_seed: int = Field(ge=0)
    input_digest: str
    elapsed_ns: int | None = Field(default=None, ge=0)
    peak_rss_kb: int | None = Field(default=None, ge=0)
    cgroup_peak_bytes: int | None = Field(default=None, ge=0)
    outcome: IterationOutcome
    verified: bool
    exit_code: int | None
    duration_ms: int = Field(ge=0)
    order_index: int = Field(ge=0)
    environment_digest: str


class WorkloadMeasurement(PluginModel):
    kind: Literal["workload_measurement"] = "workload_measurement"
    workload_id: str
    scale: int = Field(ge=1)
    weight_bp: int = Field(ge=1, le=10_000)
    input_seed: int = Field(ge=0)
    memory_metric: str
    candidate: SeriesSummary
    reference: SeriesSummary
    time_ratio: str | None
    memory_ratio: str | None
    time_censored: Literal["zero_by_lower_bound", "insufficient_information"] | None
    status: Literal["measured", "censored", "invalid", "incomplete"]
    scale_growth: dict[str, str | None]


class SeriesSummary(PluginModel):
    kind: Literal["series_summary"] = "series_summary"
    samples: int = Field(ge=0)
    median_ns: str | None
    median_peak_rss_kb: str | None
    stdev_ns: str | None
    mad_ns: str | None
    relative_mad: str | None
    min_ns: str | None
    max_ns: str | None
    rejected: int = Field(ge=0)


class CanaryRecord(PluginModel):
    kind: Literal["canary_record"] = "canary_record"
    canary_id: str
    baseline_ns: str
    median_ns: str
    relative_mad: str
    band_ratio: str
    max_relative_mad: str
    samples: int = Field(ge=0)
    baseline_source: Literal["frozen", "measured_first_run"]
    status: Literal["valid", "invalid"]
    reason: str = ""


class BlockRecord(PluginModel):
    kind: Literal["block_record"] = "block_record"
    block_index: int = Field(ge=0)
    started_first: bool
    status: BlockStatus
    canary: CanaryRecord
    reason: str = ""
    # How many iterations this block produced; retained even when the block is invalidated.
    iterations: int = Field(default=0, ge=0)


class BuildTiming(PluginModel):
    """Compile/build time is reported separately and never enters an iteration's elapsed time."""

    kind: Literal["build_timing"] = "build_timing"
    side: Literal["candidate", "reference"]
    duration_ms: int = Field(ge=0)
    image_digest: str
    flags_digest: str


class EfficiencyResult(PluginModel):
    kind: Literal["efficiency_result"] = "efficiency_result"
    status: LaneStatus
    time_geomean_ratio: str | None
    memory_geomean_ratio: str | None
    time_censored: Literal["zero_by_lower_bound", "insufficient_information"] | None
    memory_censored: Literal["zero_by_lower_bound", "insufficient_information"] | None
    score: str | None
    reasons: tuple[str, ...] = ()


class PerformanceEvidence(PluginModel):
    """Everything Prompt 14-16 need, and nothing that would let a later prompt invent a number."""

    kind: Literal["performance_evidence"] = "performance_evidence"
    evaluation_id: str
    task_id: str
    task_version: int = Field(gt=0)
    plugin_id: str
    plan_id: str
    plan_digest: str
    execution_tier: Literal["local_fixture", "development_sandbox", "production_worker"]
    lane: Literal["performance"]
    reservation: ReservationRecord
    hardware: HardwareRecord
    speed_lane: SpeedLaneCheck
    candidate_digest: str
    reference_digest: str
    reference_digest_expected: str | None
    environment_digest: str
    build: tuple[BuildTiming, ...]
    blocks: tuple[BlockRecord, ...]
    selected_block: int | None
    iterations: tuple[IterationRecord, ...]
    workloads: tuple[WorkloadMeasurement, ...]
    efficiency: EfficiencyResult
    pair_order_seed: int = Field(ge=0)
    warmup_iterations: int = Field(ge=0)
    measured_iterations: int = Field(ge=1)
    hardware_gate: Literal["satisfied", "blocked_shared_ci", "blocked_no_homogeneous_hardware"]
    hardware_gate_reason: str
    raw_artifacts: tuple[RawArtifactRef, ...]
    report_digest: str | None = None

    def iteration_counts(self, block: int | None = None) -> dict[str, int]:
        """Iteration counts per workload/side/phase, optionally restricted to one block."""
        counts: dict[str, int] = {}
        for record in self.iterations:
            if block is not None and record.block_index != block:
                continue
            key = f"{record.workload_id}:{record.side}:{record.phase}"
            counts[key] = counts.get(key, 0) + 1
        return counts


class ReservationRecord(PluginModel):
    kind: Literal["reservation_record"] = "reservation_record"
    kind_of_reservation: Literal["exclusive_guest", "capacity_slot"] = Field(
        default="exclusive_guest"
    )
    exclusive: bool
    worker_id: str
    sandbox_id: str
    fence: int = Field(ge=1)
    acquired_at_ms: int = Field(ge=0)
    released: bool


class SpeedLaneCheck(PluginModel):
    kind: Literal["speed_lane_check"] = "speed_lane_check"
    accepted: bool
    declared_recipe: str | None
    flags_digest: str
    forbidden_tokens_found: tuple[str, ...] = ()


WorkloadMeasurement.model_rebuild()
PerformanceEvidence.model_rebuild()
