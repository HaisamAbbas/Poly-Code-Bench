"""Startup identity guard: derive environment/role/tier from the verified principal.

Every control-service container runs ``pcb-ops identity verify --exec -- <command>`` as its
entrypoint (infra/terraform/modules/control_services). The guard:

1. loads the reviewed manifest named by ``PCB_ENV_MANIFEST`` (or ``--manifest``);
2. asks the trusted identity service (AWS STS) who this process is - never the request;
3. resolves environment, role and isolation tier with
   :func:`polycodebench_core.deployment.resolve_deployment`, refusing on any mismatch;
4. only then replaces itself with the service command, exporting the *verified* values as
   ``PCB_VERIFIED_ENVIRONMENT``/``PCB_VERIFIED_ROLE``/``PCB_VERIFIED_ISOLATION_TIER``.

IAM remains the primary control (a staging role cannot touch production resources at all);
this guard makes a mis-deployed container fail closed instead of running with the wrong policy.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from polycodebench_core.deployment import (
    CallerPrincipal,
    DeploymentRefused,
    EnvironmentManifest,
    VerifiedDeployment,
    resolve_deployment,
)


def sts_caller(*, region: str | None = None, client: Any = None) -> CallerPrincipal:
    """The STS caller identity of this process's ambient credentials."""

    if client is None:
        import boto3

        client = boto3.client("sts", region_name=region)
    response = client.get_caller_identity()
    return CallerPrincipal(
        account_id=response["Account"], arn=response["Arn"], verified_by="aws-sts"
    )


def _has_ambient_aws_credentials(environ: Mapping[str, str]) -> bool:
    return any(
        key in environ
        for key in (
            "AWS_ACCESS_KEY_ID",
            "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
            "AWS_CONTAINER_CREDENTIALS_FULL_URI",
            "AWS_WEB_IDENTITY_TOKEN_FILE",
            "AWS_PROFILE",
        )
    )


def verify(
    manifest: EnvironmentManifest,
    *,
    claimed_environment: str,
    claimed_role: str,
    environ: Mapping[str, str] | None = None,
    caller_source: Callable[[], CallerPrincipal] | None = None,
) -> VerifiedDeployment:
    """Resolve this process's deployment authority or raise :class:`DeploymentRefused`."""

    env = os.environ if environ is None else environ
    caller: CallerPrincipal | None = None
    if manifest.identity.provider == "aws":
        source = caller_source or (lambda: sts_caller(region=manifest.identity.region))
        try:
            caller = source()
        except DeploymentRefused:
            raise
        except Exception as error:  # noqa: BLE001 - any failure to verify is a refusal
            raise DeploymentRefused(
                f"could not verify the cloud principal ({type(error).__name__})"
            ) from None
    elif _has_ambient_aws_credentials(env) and caller_source is not None:
        caller = caller_source()
    return resolve_deployment(
        manifest,
        claimed_environment=claimed_environment,
        claimed_role=claimed_role,
        caller=caller,
    )


def verified_environment(deployment: VerifiedDeployment) -> dict[str, str]:
    return {
        "PCB_VERIFIED_ENVIRONMENT": deployment.environment,
        "PCB_VERIFIED_ROLE": deployment.role,
        "PCB_VERIFIED_ISOLATION_TIER": deployment.isolation_tier,
        "PCB_VERIFIED_BY": deployment.verified_by,
    }


def exec_service(command: Sequence[str], deployment: VerifiedDeployment) -> None:
    """Replace this process with the service command (POSIX exec)."""

    if not command:
        raise DeploymentRefused("no service command to execute after verification")
    environ = {**os.environ, **verified_environment(deployment)}
    os.execvpe(command[0], list(command), environ)  # noqa: S606 - fixed argv from IaC
