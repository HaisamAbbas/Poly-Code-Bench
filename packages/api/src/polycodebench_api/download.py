"""Signed, short-lived, artifact-scoped download paths (Technical Specification 20.4).

A public artifact response carries a download path minted by this API, never a storage URL. The
token is bound to one artifact inside one release and expires quickly, so it is not a usable token
for anything else, and a private artifact never gets one.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from datetime import UTC, datetime
from urllib.parse import quote

from polycodebench_api.context import ApiServices

_SEPARATOR = "."


def mint_download_token(services: ApiServices, artifact_id: str, release_id: str) -> tuple[str, str]:
    """Return ``(download_path, expires_at)`` for one public artifact of one release."""
    expires = int(time.time()) + services.artifact_ttl_seconds
    body = f"{artifact_id}|{release_id}|{expires}"
    encoded = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    signature = (
        base64.urlsafe_b64encode(
            hmac.new(services.secret_key, encoded.encode(), hashlib.sha256).digest()
        )
        .decode()
        .rstrip("=")
    )
    token = f"{encoded}{_SEPARATOR}{signature}"
    expires_at = datetime.fromtimestamp(expires, tz=UTC).isoformat()
    quoted = quote(artifact_id, safe="")
    pinned = quote(release_id, safe="")
    return f"/v1/artifacts/{quoted}/download?release={pinned}&token={token}", expires_at


def verify_download_token(
    services: ApiServices, artifact_id: str, release_id: str, token: str
) -> bool:
    """True only for an unexpired token minted for exactly this artifact of this release."""
    if token.count(_SEPARATOR) != 1:
        return False
    encoded, signature = token.split(_SEPARATOR)
    expected = (
        base64.urlsafe_b64encode(
            hmac.new(services.secret_key, encoded.encode(), hashlib.sha256).digest()
        )
        .decode()
        .rstrip("=")
    )
    if not hmac.compare_digest(signature, expected):
        return False
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        decoded = base64.urlsafe_b64decode(padded).decode()
        token_artifact, token_release, raw_expires = decoded.rsplit("|", 2)
    except (ValueError, UnicodeDecodeError):
        return False
    if not raw_expires.isdigit() or int(raw_expires) < int(time.time()):
        return False
    return token_artifact == artifact_id and token_release == release_id


__all__ = ["mint_download_token", "verify_download_token"]
