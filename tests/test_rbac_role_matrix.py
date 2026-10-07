"""Explicit least-privilege matrix for the administrative service roles."""

import pytest
from polycodebench_services.errors import AuthorizationError
from polycodebench_services.rbac import ROLE_PERMISSIONS, Permission, Principal, Role, authorize

EXPECTED_PERMISSIONS = {
    Role.SUBMITTER: {Permission.SUBMISSION_CREATE, Permission.SUBMISSION_READ_OWN},
    Role.CURATOR: {Permission.TASK_WRITE},
    Role.OPERATOR: {Permission.RUN_PLAN, Permission.RUN_CREATE, Permission.RUN_CANCEL},
    Role.REVIEWER: {
        Permission.SUBMISSION_REVIEW,
        Permission.RESTRICTED_EVIDENCE_READ,
        Permission.EVALUATION_ADJUDICATE,
        Permission.RELEASE_REVIEW,
    },
    Role.PUBLISHER: {Permission.RELEASE_PUBLISH},
    Role.ADMINISTRATOR: set(Permission),
}


def test_every_role_has_only_its_declared_permissions() -> None:
    assert ROLE_PERMISSIONS == {
        role: frozenset(permissions) for role, permissions in EXPECTED_PERMISSIONS.items()
    }

    for role, permissions in EXPECTED_PERMISSIONS.items():
        principal = Principal(subject_id=f"matrix-{role.value}", roles=frozenset({role}))
        for permission in Permission:
            if permission in permissions:
                authorize(principal, permission)
            else:
                with pytest.raises(AuthorizationError):
                    authorize(principal, permission)
