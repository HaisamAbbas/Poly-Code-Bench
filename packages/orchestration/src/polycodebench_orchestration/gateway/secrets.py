"""Secret references resolve to values only inside the gateway, never into stored documents."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Literal, NoReturn, Protocol

from polycodebench_core.endpoint_policy import parse_secret_ref
from polycodebench_core.model_contracts import EndpointPolicyViolation

MIN_SECRET_LENGTH = 8


class Secret:
    """Opaque credential: no ``repr``/``str`` exposure; ``reveal`` is the single read path."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        value = value.strip()  # env files commonly end in a newline
        if len(value) < MIN_SECRET_LENGTH:
            raise ValueError("secret value is too short to be a credential")
        if not value.isascii() or any(ord(ch) < 0x21 or ord(ch) == 0x7F for ch in value):
            # A control or space character would corrupt the header and embed the key in an
            # exception message; refuse it before it can reach a request.
            raise ValueError("secret value contains characters that are not valid in a header")
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret(****)"

    __str__ = __repr__

    def __reduce__(self) -> NoReturn:
        raise TypeError("secrets cannot be serialized")

    def scrub(self, data: bytes) -> tuple[bytes, bool]:
        """Replace accidental echoes of the credential; return the bytes and whether it hit."""
        needle = self._value.encode("utf-8")
        if needle in data:
            return data.replace(needle, b"[REDACTED]"), True
        return data, False


class SecretResolver(Protocol):
    def resolve(self, ref: str) -> Secret | None: ...


class EnvironmentSecretResolver:
    """Resolves ``secret://<namespace>/<name>`` from ``PCBSECRET__<NAMESPACE>__<NAME>``.

    The prefix deliberately avoids ``PCB_`` so the startup-config loader, which rejects
    unknown ``PCB_`` keys, never sees credential material. Only the configured namespace
    (``PCB_MODEL_SECRET_NAMESPACE``) is honoured.
    """

    def __init__(self, namespace: str, environ: Mapping[str, str] | None = None) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", namespace):
            raise ValueError("secret namespace is invalid")
        self._namespace = namespace
        self._environ = os.environ if environ is None else environ

    def resolve(self, ref: str) -> Secret | None:
        if ref == "none":
            return None
        namespace, name = parse_secret_ref(ref)
        if namespace != self._namespace:
            raise EndpointPolicyViolation("secret reference is outside the gateway namespace")
        key = f"PCBSECRET__{namespace}__{name}".upper().replace("-", "_")
        value = self._environ.get(key)
        if not value:
            raise EndpointPolicyViolation("secret reference is not provisioned")
        return Secret(value)


class SecretsManagerSecretResolver:
    """Resolve one environment's model or judge credentials from AWS Secrets Manager."""

    def __init__(
        self,
        namespace: str,
        secret_prefix: str,
        *,
        client: object,
        secret_class: Literal["model", "judge"] | None = None,
    ) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", namespace):
            raise ValueError("secret namespace is invalid")
        match = re.fullmatch(r"pcb/(integration|staging|production)/(model|judge)/", secret_prefix)
        if match is None:
            raise ValueError("AWS secret prefix must name one environment's model or judge path")
        expected_class = {"models": "model", "judges": "judge"}.get(namespace)
        selected_class = secret_class or expected_class
        if selected_class is not None and match.group(2) != selected_class:
            raise ValueError("AWS secret prefix does not match the configured namespace")
        self._namespace = namespace
        self._secret_prefix = secret_prefix
        self._client = client

    def resolve(self, ref: str) -> Secret | None:
        if ref == "none":
            return None
        namespace, name = parse_secret_ref(ref)
        if namespace != self._namespace:
            raise EndpointPolicyViolation("secret reference is outside the gateway namespace")
        try:
            response = self._client.get_secret_value(  # type: ignore[attr-defined]
                SecretId=f"{self._secret_prefix}{name}"
            )
        except Exception:
            raise EndpointPolicyViolation("secret reference is not provisioned") from None
        value = response.get("SecretString")
        if not isinstance(value, str) or not value:
            raise EndpointPolicyViolation("secret reference is not provisioned")
        try:
            return Secret(value)
        except ValueError:
            raise EndpointPolicyViolation(
                "secret value is not a valid provider credential"
            ) from None


def configured_secret_resolver(
    namespace: str,
    *,
    environ: Mapping[str, str] | None = None,
    secretsmanager_client: object | None = None,
) -> SecretResolver:
    """Select local environment secrets or the role-scoped production AWS namespace."""

    source = os.environ if environ is None else environ
    environment = source.get("PCB_ENVIRONMENT", "dev")
    verified_environment = source.get("PCB_VERIFIED_ENVIRONMENT")
    if environment not in {"staging", "production"} and verified_environment not in {
        "staging",
        "production",
    }:
        if verified_environment is not None and verified_environment != environment:
            raise EndpointPolicyViolation("verified service environment does not match its claim")
        return EnvironmentSecretResolver(namespace, source)
    if verified_environment != environment:
        raise EndpointPolicyViolation("AWS provider secrets require a verified service environment")
    role = source.get("PCB_VERIFIED_ROLE", "")
    if source.get("PCB_VERIFIED_BY") != "aws-sts" or role not in {
        "solve-supervisor",
        "model-gateway",
        "judge-gateway",
    }:
        raise EndpointPolicyViolation("AWS provider secrets require a verified gateway role")
    secret_class: Literal["model", "judge"] = "judge" if role == "judge-gateway" else "model"
    if secretsmanager_client is None:
        import boto3  # type: ignore[import-untyped]

        secretsmanager_client = boto3.client("secretsmanager", region_name=source.get("AWS_REGION"))
    return SecretsManagerSecretResolver(
        namespace,
        f"pcb/{environment}/{secret_class}/",
        client=secretsmanager_client,
        secret_class=secret_class,
    )
