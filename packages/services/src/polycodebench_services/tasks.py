"""Authorized task admission and split-freeze use cases."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol
from uuid import UUID

from polycodebench_core.models import AdmissionExecutionReport, TaskSet, TaskVersion

from polycodebench_services.rbac import Permission, Principal, authorize


class TaskRepository(Protocol):
    def register_task_version(
        self,
        *,
        actor_subject: str,
        document: TaskVersion,
        execution_report: AdmissionExecutionReport | Mapping[str, Any],
        manifest_digest: str,
        manifest_artifact_id: UUID,
        visible_artifact_id: UUID,
        hidden_artifact_id: UUID,
        request_id: str,
    ) -> UUID: ...

    def create_task_set_draft(
        self,
        *,
        actor_subject: str,
        document: TaskSet,
        manifest_artifact_id: UUID,
        request_id: str,
    ) -> UUID: ...

    def freeze_task_set(
        self,
        *,
        actor_subject: str,
        task_set_id: UUID,
        expected_digest: str,
        request_id: str,
    ) -> None: ...


class TaskAdmissionService:
    def __init__(self, repository: TaskRepository) -> None:
        self._repository = repository

    def freeze_task_version(
        self,
        *,
        principal: Principal,
        document: TaskVersion,
        execution_report: AdmissionExecutionReport | Mapping[str, Any],
        manifest_digest: str,
        manifest_artifact_id: UUID,
        visible_artifact_id: UUID,
        hidden_artifact_id: UUID,
        request_id: str,
    ) -> UUID:
        authorize(principal, Permission.TASK_WRITE)
        return self._repository.register_task_version(
            actor_subject=principal.subject_id,
            document=document,
            execution_report=execution_report,
            manifest_digest=manifest_digest,
            manifest_artifact_id=manifest_artifact_id,
            visible_artifact_id=visible_artifact_id,
            hidden_artifact_id=hidden_artifact_id,
            request_id=request_id,
        )

    def create_task_set(
        self,
        *,
        principal: Principal,
        document: TaskSet,
        manifest_artifact_id: UUID,
        request_id: str,
    ) -> UUID:
        authorize(principal, Permission.TASK_WRITE)
        return self._repository.create_task_set_draft(
            actor_subject=principal.subject_id,
            document=document,
            manifest_artifact_id=manifest_artifact_id,
            request_id=request_id,
        )

    def freeze_task_set(
        self,
        *,
        principal: Principal,
        task_set_id: UUID,
        expected_digest: str,
        request_id: str,
    ) -> None:
        authorize(principal, Permission.TASK_WRITE)
        self._repository.freeze_task_set(
            actor_subject=principal.subject_id,
            task_set_id=task_set_id,
            expected_digest=expected_digest,
            request_id=request_id,
        )
