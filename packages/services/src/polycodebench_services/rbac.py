"""Role-to-permission policy enforced before application use cases run."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from polycodebench_services.errors import AuthorizationError


class Role(StrEnum):
    SUBMITTER = "submitter"
    CURATOR = "curator"
    OPERATOR = "operator"
    REVIEWER = "reviewer"
    PUBLISHER = "publisher"
    ADMINISTRATOR = "administrator"
    JUDGE_SERVICE = "judge-service"


class Permission(StrEnum):
    SUBMISSION_CREATE = "submission:create"
    SUBMISSION_READ_OWN = "submission:read_own"
    SUBMISSION_REVIEW = "submission:review"
    TASK_WRITE = "task:write"
    RUN_PLAN = "run:plan"
    RUN_CREATE = "run:create"
    RUN_CANCEL = "run:cancel"
    RESTRICTED_EVIDENCE_READ = "evidence:read_restricted"
    EVALUATION_ADJUDICATE = "evaluation:adjudicate"
    RELEASE_REVIEW = "release:review"
    RELEASE_PUBLISH = "release:publish"
    ROLE_ASSIGN = "role:assign"
    ENDPOINT_APPROVE = "endpoint:approve"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.SUBMITTER: frozenset({Permission.SUBMISSION_CREATE, Permission.SUBMISSION_READ_OWN}),
    Role.CURATOR: frozenset({Permission.TASK_WRITE}),
    Role.OPERATOR: frozenset({Permission.RUN_PLAN, Permission.RUN_CREATE, Permission.RUN_CANCEL}),
    Role.REVIEWER: frozenset(
        {
            Permission.SUBMISSION_REVIEW,
            Permission.RESTRICTED_EVIDENCE_READ,
            Permission.EVALUATION_ADJUDICATE,
            Permission.RELEASE_REVIEW,
        }
    ),
    Role.PUBLISHER: frozenset({Permission.RELEASE_PUBLISH}),
    Role.JUDGE_SERVICE: frozenset({Permission.RUN_PLAN}),
    Role.ADMINISTRATOR: frozenset(Permission),
}


@dataclass(frozen=True, slots=True)
class Principal:
    subject_id: str
    roles: frozenset[Role]

    def __post_init__(self) -> None:
        if not self.subject_id or self.subject_id.strip() != self.subject_id:
            raise ValueError("subject_id must be non-empty and trimmed")


def authorize(principal: Principal, permission: Permission) -> None:
    if not any(permission in ROLE_PERMISSIONS[role] for role in principal.roles):
        raise AuthorizationError()
