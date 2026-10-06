"""Short-lived SSH key files for the forced-command disposable guest channel."""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from polycodebench_core.deployment import EnvironmentManifest

MAX_CONTROL_KEY_BYTES = 64 * 1024
_PRIVATE_KEY_MARKERS = (
    b"-----BEGIN OPENSSH PRIVATE KEY-----",
    b"-----BEGIN RSA PRIVATE KEY-----",
    b"-----BEGIN EC PRIVATE KEY-----",
    b"-----BEGIN PRIVATE KEY-----",
)


@contextmanager
def temporary_control_identity(
    *, secrets_manager_client: object, manifest: EnvironmentManifest, supervisor_role: str
) -> Iterator[Path]:
    """Fetch the role's exact secret reference into a mode-0600 temporary file, then erase it."""
    reference = manifest.sandbox.control_identity_secret_refs.get(supervisor_role)
    expected = f"aws-sm:pcb/{manifest.environment}/sandbox/control-identity-{supervisor_role}"
    if reference != expected:
        raise ValueError("guest-control identity reference is absent or outside this role")
    try:
        response = secrets_manager_client.get_secret_value(  # type: ignore[attr-defined]
            SecretId=reference.removeprefix("aws-sm:")
        )
    except Exception:
        raise ValueError("guest-control identity secret is unavailable") from None
    value = response.get("SecretString")
    if not isinstance(value, str):
        raise ValueError("guest-control identity secret has no text value")
    try:
        raw = value.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError("guest-control identity secret is not an SSH private key") from None
    if (
        not 1 <= len(raw) <= MAX_CONTROL_KEY_BYTES
        or not any(raw.startswith(marker) for marker in _PRIVATE_KEY_MARKERS)
        or b"\x00" in raw
        or re.search(rb"-----END [A-Z0-9 ]+ PRIVATE KEY-----\s*$", raw) is None
    ):
        raise ValueError("guest-control identity secret is not a bounded SSH private key")

    descriptor, filename = tempfile.mkstemp(prefix="pcb-guest-control-")
    path = Path(filename)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        yield path
    finally:
        path.unlink(missing_ok=True)
