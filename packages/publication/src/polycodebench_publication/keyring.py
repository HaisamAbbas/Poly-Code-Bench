"""Versioned public verification keys for signed release manifests (T 19.5).

Rotation never removes a public key: a release keeps verifying with the key that signed it.
States:

* ``active``  - the one key new releases are signed with;
* ``retired`` - no longer signs; still verifies every release it signed;
* ``revoked`` - compromised; verification fails closed and the affected releases must be
  re-signed as successors (runbook: signing-key-rotation.md, compromise path).

The keyring document is public (served at ``/keys/keyring.json``) and canonical-JSON
digested, so a verifier can pin the digest it trusted.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import Any, Literal

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from polycodebench_publication.releases import SigningKey, digest, verify_manifest

KeyState = Literal["active", "retired", "revoked"]
_KEY_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,62}$")


class KeyringError(ValueError):
    pass


@dataclass(frozen=True)
class KeyEntry:
    key_id: str
    public_key_b64: str
    state: KeyState
    activated_at: str
    retired_at: str | None = None
    revocation_reason: str | None = None

    def public_key(self) -> Ed25519PublicKey:
        return Ed25519PublicKey.from_public_bytes(base64.b64decode(self.public_key_b64))


def public_key_b64(signer: SigningKey) -> str:
    raw = signer.private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


class Keyring:
    def __init__(self, entries: tuple[KeyEntry, ...] = ()) -> None:
        self._entries = {entry.key_id: entry for entry in entries}
        if len(self._entries) != len(entries):
            raise KeyringError("duplicate key IDs")
        if sum(1 for entry in entries if entry.state == "active") > 1:
            raise KeyringError("at most one active signing key")
        for entry in entries:
            if not _KEY_ID.fullmatch(entry.key_id):
                raise KeyringError(f"invalid key ID {entry.key_id!r}")
            entry.public_key()  # raises on malformed key bytes

    @property
    def entries(self) -> tuple[KeyEntry, ...]:
        return tuple(sorted(self._entries.values(), key=lambda entry: entry.activated_at))

    @property
    def active(self) -> KeyEntry | None:
        return next((entry for entry in self._entries.values() if entry.state == "active"), None)

    def rotate(self, signer: SigningKey, *, at: str) -> Keyring:
        """Make ``signer`` the active key; the previous active key becomes retired."""

        if signer.key_id in self._entries:
            raise KeyringError("key IDs are never reused")
        entries = [
            KeyEntry(**{**entry.__dict__, "state": "retired", "retired_at": at})
            if entry.state == "active"
            else entry
            for entry in self._entries.values()
        ]
        entries.append(KeyEntry(signer.key_id, public_key_b64(signer), "active", at))
        return Keyring(tuple(entries))

    def revoke(self, key_id: str, *, at: str, reason: str) -> Keyring:
        if key_id not in self._entries or not reason.strip():
            raise KeyringError("revocation requires a known key and a reason")
        entries = [
            KeyEntry(
                **{
                    **entry.__dict__,
                    "state": "revoked",
                    "retired_at": entry.retired_at or at,
                    "revocation_reason": reason,
                }
            )
            if entry.key_id == key_id
            else entry
            for entry in self._entries.values()
        ]
        return Keyring(tuple(entries))

    def verify(self, manifest: dict[str, Any]) -> tuple[bool, str]:
        """Verify a manifest with the key it names; returns (valid, reason)."""

        key_id = manifest.get("key_id")
        entry = self._entries.get(key_id) if isinstance(key_id, str) else None
        if entry is None:
            return False, "unknown signing key"
        if entry.state == "revoked":
            return False, "signing key revoked"
        if not verify_manifest(manifest, entry.public_key()):
            return False, "signature invalid"
        return True, entry.state

    def document(self) -> dict[str, Any]:
        body = {
            "schema_version": 1,
            "algorithm": "Ed25519",
            "keys": [entry.__dict__ for entry in self.entries],
        }
        return {**body, "keyring_digest": digest(body)}

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> Keyring:
        body = {key: value for key, value in document.items() if key != "keyring_digest"}
        if document.get("keyring_digest") != digest(body):
            raise KeyringError("keyring digest mismatch")
        if body.get("schema_version") != 1 or body.get("algorithm") != "Ed25519":
            raise KeyringError("unsupported keyring document")
        return cls(tuple(KeyEntry(**entry) for entry in body["keys"]))


__all__ = ["KeyEntry", "Keyring", "KeyringError", "public_key_b64"]
