"""Archive bundle format, stratified selection and projection rebuild for recovery rehearsals.

A scorecard's replay evidence is stored as one verified internal artifact (media type
:data:`BUNDLE_MEDIA_TYPE`): the exact archive documents ``pcb-score replay`` consumes
(policy, ownership, frozen task, validated evidence manifest, language profile) plus the
archived outcome. The ``scorecard`` row references that artifact, so a database restored
without its artifacts fails replay - which is the point of T 22.6.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any

from polycodebench_core.canonical import canonical_json_bytes
from polycodebench_publication.releases import digest
from polycodebench_scoring.loader import (
    load_evidence_manifest,
    load_evidence_ownership,
    load_frozen_task,
    load_language_profile,
    load_outcome,
    load_scoring_policy,
)
from polycodebench_scoring.replay import ReplayReport, replay_outcome

BUNDLE_MEDIA_TYPE = "application/vnd.polycodebench.scoring-replay-bundle+json"
BUNDLE_KIND = "pcb_scoring_replay_bundle"
DOCUMENTS = ("policy", "ownership", "task", "manifest", "profile", "outcome")


def bundle_bytes(stratum: str, documents: Mapping[str, Any], fixture_kind: str) -> bytes:
    missing = [name for name in DOCUMENTS if name not in documents and name != "profile"]
    if missing:
        raise ValueError(f"bundle is missing {missing}")
    return canonical_json_bytes(
        {
            "kind": BUNDLE_KIND,
            "schema_version": 1,
            "stratum": stratum,
            "fixture_kind": fixture_kind,
            "documents": dict(documents),
        }
    )


def parse_bundle(body: bytes) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(body)
    if document.get("kind") != BUNDLE_KIND or document.get("schema_version") != 1:
        raise ValueError("not a schema_version 1 scoring replay bundle")
    return document


def replay_bundle(bundle: Mapping[str, Any]) -> tuple[ReplayReport, Any]:
    """Re-score the archived evidence in-process and require byte-identical output."""

    documents = bundle["documents"]
    with tempfile.TemporaryDirectory(prefix="pcb-replay-") as directory:
        root = Path(directory)
        for name, value in documents.items():
            if value is not None:
                (root / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")
        policy = load_scoring_policy(root / "policy.json")
        ownership = load_evidence_ownership(root / "ownership.json")
        task = load_frozen_task(root / "task.json")
        manifest = load_evidence_manifest(root / "manifest.json")
        profile = (
            load_language_profile(root / "profile.json")
            if (root / "profile.json").exists()
            else None
        )
        archived = load_outcome(root / "outcome.json")
    report = replay_outcome(
        task, policy, manifest, ownership=ownership, profile=profile, archived=archived
    )
    return report, archived


@dataclass(frozen=True)
class ScorecardRow:
    scorecard_id: str
    stratum: str
    gate: str
    composite: Decimal | None


def select_stratified(
    rows: Sequence[ScorecardRow], *, count: int = 10, seed: str = "pcb-restore-rehearsal-v1"
) -> list[ScorecardRow]:
    """Deterministic stratified sample: round-robin over strata, seeded order within each."""

    by_stratum: dict[str, list[ScorecardRow]] = defaultdict(list)
    for row in rows:
        by_stratum[row.stratum].append(row)
    for members in by_stratum.values():
        members.sort(key=lambda row: hashlib.sha256(f"{seed}:{row.scorecard_id}".encode()).digest())
    strata = sorted(by_stratum)
    selected: list[ScorecardRow] = []
    depth = 0
    while len(selected) < count and any(len(by_stratum[s]) > depth for s in strata):
        for stratum in strata:
            if len(selected) == count:
                break
            if len(by_stratum[stratum]) > depth:
                selected.append(by_stratum[stratum][depth])
        depth += 1
    return selected


def _decimal(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN))


def rehearsal_projection(rows: Iterable[ScorecardRow], cohort: Mapping[str, Any]) -> dict[str, Any]:
    """The public projection of a rehearsal release, computed only from scorecard rows."""

    ordered = sorted(rows, key=lambda row: row.scorecard_id)
    if not ordered:
        raise ValueError("a projection needs scorecards")
    known = [row.composite for row in ordered if row.composite is not None]
    passed = sum(1 for row in ordered if row.gate == "pass")
    mean = _decimal(sum(known, Decimal(0)) / len(known)) if known else None
    return {
        "schema_version": 1,
        "fixture_kind": "synthetic_internal",
        "scope": "exploratory",
        "cohort_digest": digest(dict(cohort)),
        "limitations": [
            "Synthetic internal recovery-rehearsal fixture; not a benchmark result.",
            "Scores come from hand-constructed scoring fixtures, not model runs.",
        ],
        "metrics": [
            {
                "metric_id": "rehearsal.mean_composite",
                "value": mean,
                "interval_low": None,
                "interval_high": None,
                "coverage": _decimal(Decimal(len(known)) / len(ordered)),
                "conditional_on_pass": False,
            },
            {
                "metric_id": "rehearsal.gate_pass_rate",
                "value": _decimal(Decimal(passed) / len(ordered)),
                "interval_low": None,
                "interval_high": None,
                "coverage": "1.000000",
                "conditional_on_pass": False,
            },
        ],
    }


def rehearsal_content(rows: Iterable[ScorecardRow]) -> dict[str, Any]:
    return {
        "kind": "recovery_rehearsal_release",
        "scorecard_ids": sorted(row.scorecard_id for row in rows),
    }
