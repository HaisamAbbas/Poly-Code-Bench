"""Read completed runs' persisted scorecards for an unranked ``live_exploratory`` release.

Attempts that ended in a model failure have no scorecard; they are read with their failure class
and the run's declared scoring policy so the builder scores them as zero (Architecture v1 10.3).

This is an operator tool: it needs a DSN that can read run, attempt, evaluation and scorecard
lineage, and it writes only local JSON files that the reviewed ``pcb-release`` lifecycle consumes
(create -> validate -> review -> approve -> publish -> ``pcb-ops releases sync-publication``).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

from polycodebench_core.application_errors import InvalidState
from polycodebench_persistence.models import (
    artifact,
    attempt,
    config_document,
    evaluation,
    model_revision,
    run,
    scorecard,
    task,
    task_version,
)
from polycodebench_publication.live import (
    LiveScorecardRow,
    live_release_documents,
    live_release_evidence,
)
from polycodebench_publication.releases import receipt_document
from sqlalchemy import and_, select
from sqlalchemy.engine import Engine

#: Evaluations that may carry the attempt's current result; superseded/cancelled ones never do.
_CURRENT_EVALUATION_STATES = ("queued", "running", "ready", "failed")


def read_live_rows(engine: Engine, run_ids: Sequence[UUID]) -> list[LiveScorecardRow]:
    """Every attempt of the selected completed runs, joined to its current scorecard if any."""
    if not run_ids or len(set(run_ids)) != len(run_ids):
        raise InvalidState("select one or more distinct runs")
    evidence = artifact.alias("live_release_evidence")
    run_config = config_document.alias("live_release_run_config")
    statement = (
        select(
            run.c.id.label("run_id"),
            run.c.status.label("run_status"),
            run.c.purpose,
            model_revision.c.provider,
            model_revision.c.name,
            model_revision.c.immutable_revision,
            task.c.slug,
            task_version.c.version,
            task_version.c.language,
            task_version.c.stratum_id,
            task_version.c.cluster_id,
            attempt.c.sample_index,
            attempt.c.state.label("attempt_state"),
            attempt.c.failure_class.label("attempt_failure_class"),
            run_config.c.document["evaluation_policy_digest"].astext.label("run_policy_digest"),
            evaluation.c.id.label("evaluation_id"),
            evaluation.c.state.label("evaluation_state"),
            scorecard.c.id.label("scorecard_id"),
            scorecard.c.gate,
            scorecard.c.composite,
            scorecard.c.scorer_digest,
            scorecard.c.evidence_digest,
            config_document.c.digest.label("policy_digest"),
            evidence.c.status.label("evidence_status"),
        )
        .select_from(
            run.join(model_revision, model_revision.c.id == run.c.model_revision_id)
            .join(run_config, run_config.c.id == run.c.config_document_id)
            .outerjoin(attempt, attempt.c.run_id == run.c.id)
            .outerjoin(task_version, task_version.c.id == attempt.c.task_version_id)
            .outerjoin(task, task.c.id == task_version.c.task_id)
            .outerjoin(
                evaluation,
                and_(
                    evaluation.c.attempt_id == attempt.c.id,
                    evaluation.c.state.in_(_CURRENT_EVALUATION_STATES),
                ),
            )
            .outerjoin(scorecard, scorecard.c.evaluation_id == evaluation.c.id)
            .outerjoin(config_document, config_document.c.id == evaluation.c.policy_config_id)
            .outerjoin(evidence, evidence.c.id == evaluation.c.evidence_manifest_id)
        )
        .where(run.c.id.in_(list(run_ids)))
        .order_by(run.c.id, task.c.slug, attempt.c.sample_index, scorecard.c.id)
    )
    with engine.connect() as connection:
        records = connection.execute(statement).mappings().all()
    found = {UUID(str(record["run_id"])) for record in records}
    if found != set(run_ids):
        raise InvalidState("one or more selected runs do not exist")
    rows: list[LiveScorecardRow] = []
    for record in records:
        if record["run_status"] != "completed":
            raise InvalidState("live exploratory releases accept completed runs only")
        if record["purpose"] == "audit_diagnostic":
            raise InvalidState("audit diagnostic runs cannot be published")
        if record["slug"] is None:
            raise InvalidState("a selected run has no attempts")
        rows.append(
            LiveScorecardRow(
                run_id=str(record["run_id"]),
                model_provider=str(record["provider"]),
                model_name=str(record["name"]),
                model_revision=record["immutable_revision"],
                task_id=str(record["slug"]),
                task_version=int(record["version"]),
                language=str(record["language"]),
                stratum=str(record["stratum_id"]),
                cluster_id=str(record["cluster_id"]),
                sample_index=int(record["sample_index"]),
                attempt_state=str(record["attempt_state"]),
                evaluation_id=_text(record["evaluation_id"]),
                evaluation_state=_text(record["evaluation_state"]),
                scorecard_id=_text(record["scorecard_id"]),
                gate=_text(record["gate"]),
                composite=record["composite"],
                policy_digest=_text(record["policy_digest"]),
                scorer_digest=_text(record["scorer_digest"]),
                evidence_digest=_text(record["evidence_digest"]),
                evidence_verified=record["evidence_status"] == "verified",
                attempt_failure_class=_text(record["attempt_failure_class"]),
                run_policy_digest=_text(record["run_policy_digest"]),
            )
        )
    return rows


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def build_live_release(
    engine: Engine, run_ids: Sequence[UUID]
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Build documents from one read and validation receipts from an independent second read."""
    content, projection = live_release_documents(read_live_rows(engine, run_ids))
    receipts = live_release_evidence(content, projection, read_live_rows(engine, run_ids))
    return content, projection, [receipt_document(item) for item in receipts]


def write_live_release(
    output_dir: Path,
    content: dict[str, Any],
    projection: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    for name, document in (
        ("content", content),
        ("projection", projection),
        ("evidence", evidence),
    ):
        path = output_dir / f"{name}.json"
        path.write_text(
            json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        paths[name] = str(path)
    return paths


__all__ = ["build_live_release", "read_live_rows", "write_live_release"]
