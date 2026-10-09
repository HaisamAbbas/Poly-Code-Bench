"""Contamination controls for AI-generated and parametric evaluation tasks.

The package never trusts a generator. A generator (a language model, a template, or a person)
proposes candidate text; deterministic gates decide whether that text may enter the executable
admission path (``pcb task validate``), and nothing here admits, publishes or scores a task.

The controls implemented here follow ``docs/methodology/contamination-controls.md``:

- Canary GUIDs that mark each item so that later dataset filters and model probes can detect it.
- Surface and structural (identifier-normalised) n-gram overlap against every reference document
  the operator supplies, including previously used and held-out tasks.
- An exposure ledger that records every audience a task text reached (generator, evaluated model,
  reviewer, public), so "unseen by this model provider" is an auditable fact and not a guess.
- Family-level clustering and keyed split assignment, so related variants never straddle splits.
- Parametric instance generation with commit-reveal round seeds, so rotated held-out instances are
  fresh at evaluation time and verifiably not chosen after the fact.

No module here performs network I/O, calls a model, or executes candidate code.
"""

from .canary import CANARY_SENTENCE, contains_canary, derive_canary, embed_canary, find_canaries
from .contracts import (
    CandidateTask,
    ExposureEvent,
    GateResult,
    ScreeningDecision,
    ScreeningReport,
    SplitName,
    TaskgenModel,
)
from .exposure import ExposureLedger, ModelEligibility
from .generation import FamilySpec, build_generation_request, parse_generator_output
from .overlap import OverlapSummary, ReferenceIndex, ViewOverlap, build_reference_index
from .parametric import (
    ParameterRange,
    ParametricFamily,
    RoundCommitment,
    derive_instance_seed,
)
from .screening import CandidateScreener, ScreeningPolicy
from .splits import SplitRatios, assign_split, cluster_by_containment, rank_agreement_bp

__all__ = [
    "CANARY_SENTENCE",
    "CandidateScreener",
    "CandidateTask",
    "ExposureEvent",
    "ExposureLedger",
    "FamilySpec",
    "GateResult",
    "ModelEligibility",
    "OverlapSummary",
    "ParameterRange",
    "ParametricFamily",
    "ReferenceIndex",
    "RoundCommitment",
    "ScreeningDecision",
    "ScreeningPolicy",
    "ScreeningReport",
    "SplitName",
    "SplitRatios",
    "TaskgenModel",
    "ViewOverlap",
    "assign_split",
    "build_generation_request",
    "build_reference_index",
    "cluster_by_containment",
    "contains_canary",
    "derive_canary",
    "derive_instance_seed",
    "embed_canary",
    "find_canaries",
    "parse_generator_output",
    "rank_agreement_bp",
]
