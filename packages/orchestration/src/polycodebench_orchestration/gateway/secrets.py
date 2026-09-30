"""Secret references resolve to values only inside the gateway, never into stored documents."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import NoReturn, Protocol

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
