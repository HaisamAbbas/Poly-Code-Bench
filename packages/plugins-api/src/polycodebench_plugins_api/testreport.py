"""Language-neutral test report contract and inventory reconciliation (Technical Spec 12.2, 10.4).

Adapters turn tool output into ``TestCaseRecord`` objects plus a ``GroupControl`` describing how
the run ended. ``reconcile`` then decides the acceptance gate from the *declared inventory*:
unknown required cases, missing records, skipped/xfailed mandatory tests and harness failures never
count as passes, and candidate errors are kept apart from supervisor/harness errors.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Literal

from polycodebench_core.models import Digest, Slug
from pydantic import Field

from polycodebench_plugins_api.contracts import PluginModel

CaseOutcome = Literal["pass", "fail", "error", "skipped"]
GateVerdict = Literal["pass", "fail", "incomplete"]


class TestCaseRecord(PluginModel):
    __test__ = False
    kind: Literal["test_case_record"] = "test_case_record"
    group_id: Slug
    repetition: int = Field(default=0, ge=0)
    case_id: str = Field(min_length=1, max_length=512)
    required: bool
    input_seed: str | None = None
    expected_outcome_digest: Digest | None = None
    outcome: CaseOutcome
    reason: str = Field(default="", max_length=200)
    duration_ms: int = Field(ge=0)
    max_rss_kb: int | None = None
    stdout_digest: Digest | None = None
    stderr_digest: Digest | None = None
    execution_identity: str = Field(min_length=1, max_length=200)


class GroupControl(PluginModel):
    """How a group execution ended, from supervisor and harness evidence (not only an exit code)."""

    kind: Literal["group_control"] = "group_control"
    group_id: Slug
    repetition: int = Field(ge=0)
    status: Literal[
        "finished",  # harness wrote its finish record
        "candidate_timeout",  # killed at the time limit while a candidate case was running
        "candidate_killed",  # killed by the kernel/OOM during a candidate case
        "harness_failure",  # no usable control evidence
    ]
    in_flight_case: str | None = None
    detail: str = Field(default="", max_length=300)
    unexpected_cases: tuple[str, ...] = ()
    candidate_collection_errors: tuple[str, ...] = ()
    harness_collection_errors: tuple[str, ...] = ()


class InventoryCase(PluginModel):
    kind: Literal["inventory_case"] = "inventory_case"
    case_id: str = Field(min_length=1, max_length=512)
    required: bool
    predeclared_skip: bool = False


class InventoryGroup(PluginModel):
    kind: Literal["inventory_group"] = "inventory_group"
    group_id: Slug
    required: bool
    #: Whether this group is an acceptance gate or required quality-only evidence. Suite admission
    #: needs it to decide how many repetitions to run: acceptance groups are re-run once per
    #: declared repetition of the whole evaluation, quality-only groups keep their own count so a
    #: resource leak or a race cannot be turned into a wrong-answer failure.
    classification: Literal["acceptance", "quality_only"] = "acceptance"
    cases: tuple[InventoryCase, ...] = Field(min_length=1)


class GroupVerdict(PluginModel):
    kind: Literal["group_verdict"] = "group_verdict"
    group_id: Slug
    required: bool
    verdict: GateVerdict
    reasons: tuple[str, ...] = ()
    passed_cases: int = 0
    failed_cases: int = 0
    candidate_error_cases: int = 0


class AcceptanceVerdict(PluginModel):
    kind: Literal["acceptance_verdict"] = "acceptance_verdict"
    gate: GateVerdict
    groups: tuple[GroupVerdict, ...]
    reasons: tuple[str, ...] = ()


def _verdict_rank(value: GateVerdict) -> int:
    return {"pass": 0, "fail": 1, "incomplete": 2}[value]


def reconcile(
    inventory: Sequence[InventoryGroup],
    records: Iterable[TestCaseRecord],
    controls: Iterable[GroupControl],
) -> AcceptanceVerdict:
    """Gate verdict over every repetition. Incomplete evidence outranks a candidate failure only
    when it hides whether the candidate failed; a candidate failure with complete control evidence
    is a plain fail."""
    by_group: dict[str, list[TestCaseRecord]] = {}
    for record in records:
        by_group.setdefault(record.group_id, []).append(record)
    controls_by_group: dict[str, list[GroupControl]] = {}
    for control in controls:
        controls_by_group.setdefault(control.group_id, []).append(control)
    verdicts: list[GroupVerdict] = []
    for group in inventory:
        group_records = by_group.get(group.group_id, [])
        group_controls = controls_by_group.get(group.group_id, [])
        reasons: list[str] = []
        harness = False
        candidate_fail = False
        if not group_controls:
            harness = True
            reasons.append("no_execution_evidence")
        for control in group_controls:
            if control.status == "harness_failure":
                harness = True
                reasons.append(f"harness_failure:{control.detail or 'unspecified'}")
            elif control.status in {"candidate_timeout", "candidate_killed"}:
                candidate_fail = True
                reasons.append(f"{control.status}:{control.in_flight_case or 'collection'}")
            if control.harness_collection_errors:
                harness = True
                reasons.append("harness_collection_error")
            if control.candidate_collection_errors:
                candidate_fail = True
                reasons.append("candidate_collection_error")
            if control.unexpected_cases:
                harness = True
                reasons.append("unexpected_cases_in_overlay")
        finished_runs = [c for c in group_controls if c.status == "finished"]
        explained = any(
            c.status in {"candidate_timeout", "candidate_killed"} or c.candidate_collection_errors
            for c in group_controls
        )
        passed = failed = errored = 0
        for case in group.cases:
            matching = [r for r in group_records if r.case_id == case.case_id]
            if len(matching) < len(finished_runs) and not explained:
                if case.required:
                    harness = True
                    reasons.append(f"missing_record:{case.case_id}")
                continue
            for record in matching:
                if record.outcome == "pass":
                    passed += 1
                elif record.outcome == "skipped" and case.predeclared_skip:
                    continue
                else:
                    if case.required:
                        candidate_fail = True
                        reasons.append(f"{record.outcome}:{case.case_id}:{record.reason or '-'}")
                    failed += 1
                    errored += record.outcome == "error"
        known = {case.case_id for case in group.cases}
        for record in group_records:
            if record.case_id not in known:
                harness = True
                reasons.append(f"record_outside_inventory:{record.case_id}")
        verdict: GateVerdict = "pass"
        if candidate_fail:
            verdict = "fail"
        if harness:
            verdict = "incomplete"
        verdicts.append(
            GroupVerdict(
                group_id=group.group_id,
                required=group.required,
                verdict=verdict,
                reasons=tuple(dict.fromkeys(reasons)),
                passed_cases=passed,
                failed_cases=failed,
                candidate_error_cases=errored,
            )
        )
    required = [v for v in verdicts if v.required]
    gate: GateVerdict = "pass"
    for group_verdict in required:
        if _verdict_rank(group_verdict.verdict) > _verdict_rank(gate):
            gate = group_verdict.verdict
    if not required:
        gate = "incomplete"
    return AcceptanceVerdict(
        gate=gate,
        groups=tuple(verdicts),
        reasons=tuple(r for v in required for r in v.reasons),
    )


def inventory_from_document(document: Mapping[str, object]) -> tuple[InventoryGroup, ...]:
    """Build an inventory from the ``groups`` section of a frozen oracle document."""
    groups = document.get("groups")
    if not isinstance(groups, list):
        raise ValueError("oracle inventory needs a groups list")
    result = []
    for item in groups:
        if not isinstance(item, dict):
            raise ValueError("inventory group must be an object")
        cases = tuple(
            InventoryCase(
                case_id=str(case["case_id"]),
                required=bool(case["required"]),
                predeclared_skip=bool(case.get("predeclared_skip", False)),
            )
            for case in item["cases"]
        )
        result.append(
            InventoryGroup(group_id=item["group_id"], required=bool(item["required"]), cases=cases)
        )
    return tuple(result)
