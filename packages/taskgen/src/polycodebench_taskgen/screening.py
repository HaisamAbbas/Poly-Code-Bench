"""The pre-execution screen for generator-proposed tasks.

A candidate passes only if every gate passes. A passing candidate is
``ready_for_executable_admission``: it still has to build, fail its known faults, pass its
alternative solution and survive the repository's executable admission (``pcb task validate``).
Nothing in this module admits or publishes a task, and nothing executes candidate code.

Gates, in order:

1. ``components``: statement, reference solution and hidden tests are non-empty and pairwise
   distinct.
2. ``generator_approval``: an external generator must be on the operator's approved list.
3. ``canary_isolation``: the candidate does not carry another item's canary or a known canary.
4. ``surface_union`` / ``surface_document``: n-gram overlap of verbatim text against the corpus.
5. ``structural_union`` / ``structural_document``: the same against identifier-normalised text.

The index is extended with each accepted candidate inside a batch, so two generated candidates that
copy each other are rejected even though neither appears in the corpus.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

from polycodebench_core.canonical import canonical_document_digest
from pydantic import Field, model_validator

from polycodebench_taskgen.canary import derive_canary, find_canaries
from polycodebench_taskgen.contracts import (
    CandidateTask,
    GateResult,
    ScreeningDecision,
    ScreeningReport,
    SplitName,
    TaskgenModel,
)
from polycodebench_taskgen.overlap import OverlapSummary, ReferenceIndex
from polycodebench_taskgen.splits import SplitRatios, assign_split, cluster_by_containment


class ScreeningPolicy(TaskgenModel):
    """Thresholds are in basis points. The defaults are pilot values, not calibrated ones.

    ``union_threshold_bp`` follows the PaLM-style 70%-of-8-grams rule as summarised by a secondary
    source. The document thresholds and the link threshold have no published source; calibrate them
    on known clean and known contaminated pairs before relying on the decisions.
    """

    kind: Literal["screening_policy"] = "screening_policy"
    schema_version: Literal[1] = 1
    policy_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$")
    ngram: int = Field(default=8, ge=3, le=64)
    union_threshold_bp: int = Field(default=7000, ge=0, le=10000)
    document_threshold_bp: int = Field(default=5000, ge=0, le=10000)
    link_threshold_bp: int = Field(default=3000, ge=0, le=10000)
    approved_external_generators: tuple[str, ...] = ()
    # Generated tasks have not been reviewed, so by default they stay in the private held-out split.
    # ``keyed_ratios`` spreads them across all splits by cluster, for a reviewed family only.
    split_mode: Literal["private_heldout_only", "keyed_ratios"] = "private_heldout_only"
    split_ratios: SplitRatios

    @model_validator(mode="after")
    def _approved_generators_unique(self) -> ScreeningPolicy:
        if len(set(self.approved_external_generators)) != len(self.approved_external_generators):
            raise ValueError("approved external generators must be unique")
        return self

    def digest(self) -> str:
        return canonical_document_digest(self)


def _candidate_text(candidate: CandidateTask) -> str:
    return "\n".join((candidate.statement, candidate.reference_solution, candidate.hidden_tests))


def _gate(name: str, failures: list[str], metrics: Sequence[tuple[str, int]] = ()) -> GateResult:
    return GateResult(
        gate=name, passed=not failures, reasons=tuple(failures), metrics=tuple(metrics)
    )


class CandidateScreener:
    def __init__(
        self,
        *,
        index: ReferenceIndex,
        policy: ScreeningPolicy,
        canary_secret: bytes,
        split_secret: bytes,
        corpus_digest: str,
        known_canaries: frozenset[str] = frozenset(),
        family_registry: Mapping[str, str] | None = None,
    ) -> None:
        if index.ngram != policy.ngram:
            raise ValueError("reference index n-gram size must match the screening policy")
        self._index = index
        self._policy = policy
        self._canary_secret = canary_secret
        self._split_secret = split_secret
        self._corpus_digest = corpus_digest
        self._known_canaries = known_canaries
        self._family_registry = dict(family_registry or {})

    def screen_candidate(self, candidate: CandidateTask) -> ScreeningReport:
        """Screen one candidate against the current index without extending it."""
        report, _ = self._evaluate(candidate)
        return report

    def screen_batch(self, candidates: Sequence[CandidateTask]) -> tuple[ScreeningReport, ...]:
        """Screen candidates in order. Accepted ones are added to the index as they pass."""
        identifiers = [candidate.candidate_id for candidate in candidates]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("candidate identifiers must be unique within a batch")
        provisional: list[tuple[ScreeningReport, OverlapSummary | None]] = []
        accepted_texts: dict[str, str] = {}
        for candidate in candidates:
            report, overlap = self._evaluate(candidate)
            if report.decision is ScreeningDecision.READY_FOR_EXECUTABLE_ADMISSION:
                text = _candidate_text(candidate)
                self._index.add(f"candidate:{candidate.candidate_id}", text)
                accepted_texts[candidate.candidate_id] = text
            provisional.append((report, overlap))

        batch_clusters = cluster_by_containment(
            accepted_texts,
            ngram=self._policy.ngram,
            link_threshold_bp=self._policy.link_threshold_bp,
        )
        finished: list[ScreeningReport] = []
        for report, overlap in provisional:
            if report.decision is not ScreeningDecision.READY_FOR_EXECUTABLE_ADMISSION:
                finished.append(report)
                continue
            cluster = self._cluster_for(report.candidate_id, overlap, batch_clusters)
            if self._policy.split_mode == "private_heldout_only":
                split: SplitName = "private_heldout"
            else:
                split = assign_split(
                    cluster,
                    secret=self._split_secret,
                    ratios=self._policy.split_ratios,
                )
            finished.append(report.model_copy(update={"cluster_id": cluster, "split": split}))
        return tuple(finished)

    def _cluster_for(
        self,
        candidate_id: str,
        overlap: OverlapSummary | None,
        batch_clusters: Mapping[str, str],
    ) -> str:
        # A candidate related to a previously registered family joins that family's cluster, so a
        # later variant cannot land in a different split from its relatives.
        if overlap is not None:
            surface = overlap.view("surface")
            linked = surface.max_document_id
            if (
                linked is not None
                and surface.max_document_bp >= self._policy.link_threshold_bp
                and linked in self._family_registry
            ):
                return self._family_registry[linked]
        return batch_clusters[candidate_id]

    def _evaluate(self, candidate: CandidateTask) -> tuple[ScreeningReport, OverlapSummary | None]:
        canary = derive_canary(
            self._canary_secret,
            family_id=candidate.family_id,
            candidate_id=candidate.candidate_id,
        )
        gates: list[GateResult] = [
            self._components_gate(candidate),
            self._generator_gate(candidate),
            self._canary_gate(candidate, canary),
        ]
        text = _candidate_text(candidate)
        overlap: OverlapSummary | None = None
        if all(gate.passed for gate in gates):
            overlap = self._index.screen(text)
            gates.extend(self._overlap_gates(overlap))
        decision = (
            ScreeningDecision.READY_FOR_EXECUTABLE_ADMISSION
            if all(gate.passed for gate in gates)
            else ScreeningDecision.REJECTED
        )
        report = ScreeningReport(
            candidate_id=candidate.candidate_id,
            family_id=candidate.family_id,
            generator_id=candidate.generator_id,
            policy_digest=self._policy.digest(),
            reference_corpus_digest=self._corpus_digest,
            canary=canary,
            gates=tuple(gates),
            decision=decision,
        )
        return report, overlap

    @staticmethod
    def _components_gate(candidate: CandidateTask) -> GateResult:
        failures: list[str] = []
        parts = {
            "statement": candidate.statement,
            "reference_solution": candidate.reference_solution,
            "hidden_tests": candidate.hidden_tests,
        }
        for name, value in parts.items():
            if not value.strip():
                failures.append(f"{name}_blank")
        if len(set(parts.values())) != len(parts):
            failures.append("components_not_distinct")
        return _gate("components", failures)

    def _generator_gate(self, candidate: CandidateTask) -> GateResult:
        failures: list[str] = []
        if candidate.generator_is_external and (
            candidate.generator_id not in self._policy.approved_external_generators
        ):
            failures.append("external_generator_not_approved")
        return _gate("generator_approval", failures)

    def _canary_gate(self, candidate: CandidateTask, canary: str) -> GateResult:
        text = _candidate_text(candidate)
        failures: list[str] = []
        foreign = [marker for marker in find_canaries(text) if marker != canary]
        if foreign:
            failures.append("foreign_canary_marker_present")
        if any(known in text for known in self._known_canaries):
            failures.append("known_canary_present")
        return _gate("canary_isolation", failures)

    def _overlap_gates(self, overlap: OverlapSummary) -> list[GateResult]:
        policy = self._policy
        results: list[GateResult] = []
        for view in overlap.views:
            union_failures = (
                [f"{view.view}_union_overlap_at_or_above_threshold"]
                if view.union_bp >= policy.union_threshold_bp
                else []
            )
            document_failures = (
                [
                    f"{view.view}_single_document_overlap_at_or_above_threshold:{view.max_document_id}"
                ]
                if view.max_document_bp >= policy.document_threshold_bp
                else []
            )
            metrics = (("ngrams", view.candidate_ngrams), ("union_bp", view.union_bp))
            results.append(_gate(f"{view.view}_union", union_failures, metrics))
            results.append(
                _gate(
                    f"{view.view}_document",
                    document_failures,
                    (("max_document_bp", view.max_document_bp),),
                )
            )
        return results
