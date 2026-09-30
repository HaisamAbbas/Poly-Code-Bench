"""Build a :class:`SolveAssignment` for a claimed attempt from persisted records.

Only the *visible* bundle is ever read. The hidden bundle's identity (artifact id and digest) is
used solely to build the fail-closed leak markers the session checks every request against.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from uuid import UUID

from polycodebench_core.jobs import JobClaim
from polycodebench_core.model_contracts import CallScope
from polycodebench_core.model_planning import ModelConfig
from polycodebench_core.models import TaskVersion
from polycodebench_core.solve_contracts import SolveError, SolveProtocol, resolve_effective_protocol
from polycodebench_persistence.artifacts import ArtifactRepository
from polycodebench_persistence.models import attempt, config_document, run, task_version
from sqlalchemy import select
from sqlalchemy.engine import Engine

from polycodebench_orchestration.solve.types import (
    PUBLIC_TESTS_FILE,
    SolveAssignment,
    public_groups_from_workspace,
    workspace_from_visible_archive,
)


class DatabaseAssignmentLoader:
    """``AssignmentLoader`` backed by PostgreSQL and the artifact store."""

    def __init__(
        self,
        engine: Engine,
        artifacts: ArtifactRepository,
        protocols: Mapping[str, SolveProtocol],
    ) -> None:
        self._engine = engine
        self._artifacts = artifacts
        self._protocols = protocols

    def __call__(self, claim: JobClaim) -> SolveAssignment:
        if claim.scope_type != "attempt":
            raise SolveError("solve stages run against attempts")
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(
                        attempt.c.id,
                        attempt.c.seed,
                        attempt.c.run_id,
                        task_version.c.document,
                        config_document.c.document.label("run_config"),
                    )
                    .join(task_version, task_version.c.id == attempt.c.task_version_id)
                    .join(run, run.c.id == attempt.c.run_id)
                    .join(config_document, config_document.c.id == run.c.config_document_id)
                    .where(attempt.c.id == claim.scope_id)
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise SolveError("attempt is not resolvable")
            run_config = dict(row["run_config"])
            model_row = (
                connection.execute(
                    select(config_document.c.id, config_document.c.document).where(
                        config_document.c.kind == "model_config",
                        config_document.c.digest == run_config["model_config_digest"],
                    )
                )
                .mappings()
                .one_or_none()
            )
        if model_row is None:
            raise SolveError("the run's resolved model configuration is not registered")
        task = TaskVersion.model_validate_json(json.dumps(row["document"]))
        protocol = self._protocols.get(str(run_config["protocol_id"]))
        if protocol is None:
            raise SolveError("the run names a protocol that is not installed")
        effective = resolve_effective_protocol(protocol, task.protocol_constraints)
        bundle = task.visible_bundle
        _, archive = self._artifacts.read_verified(UUID(bundle.artifact_id))
        if "sha256:" + hashlib.sha256(archive).hexdigest() != bundle.digest:
            raise SolveError("visible bundle bytes do not match the frozen task version")
        files = workspace_from_visible_archive(archive)
        if "task.md" not in files:
            raise SolveError("visible bundle has no task.md")
        hidden = task.hidden_bundle
        return SolveAssignment(
            attempt_id=row["id"],
            scope=CallScope(kind="attempt", scope_id=row["id"]),
            effective=effective,
            config=ModelConfig.model_validate(model_row["document"], strict=False),
            config_document_id=model_row["id"],
            sample_seed=int(row["seed"]),
            instructions=files["task.md"].decode("utf-8"),
            contract=task.output_contract,
            required_outputs=list(task.acceptance.required_outputs),
            protected_paths=[*task.acceptance.protected_paths, PUBLIC_TESTS_FILE]
            if PUBLIC_TESTS_FILE in files
            else list(task.acceptance.protected_paths),
            visible_files=files,
            base_digest=bundle.digest,
            public_groups=public_groups_from_workspace(files),
            forbidden_markers=(
                hidden.digest.encode(),
                hidden.digest.removeprefix("sha256:").encode(),
                hidden.artifact_id.encode(),
            ),
        )
