"""Canary GUIDs that mark an evaluation item as not for training.

BIG-bench embeds one fixed GUID in every task file so that web-crawl filters can drop the file and
a model probe can later check whether it reproduces the string (Google, README of
github.com/google/BIG-bench). A fixed public GUID is only a filter and detection hook, not a
guarantee, so this module derives a distinct GUID per item from a private secret. The GUID is
then only recognisable to someone holding the secret or the registry of issued canaries.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import uuid

CANARY_SENTENCE = "This evaluation item must not be used as training data."
MIN_SECRET_BYTES = 32
_CANARY_RE = re.compile(
    r"CANARY GUID ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)


def derive_canary(secret: bytes, *, family_id: str, candidate_id: str) -> str:
    """Return a stable GUID for one item. The same inputs always give the same canary."""
    if len(secret) < MIN_SECRET_BYTES:
        raise ValueError(f"canary secret must be at least {MIN_SECRET_BYTES} bytes")
    message = f"polycodebench-canary-v1\x00{family_id}\x00{candidate_id}".encode()
    digest = hmac.new(secret, message, hashlib.sha256).digest()
    return str(uuid.UUID(bytes=digest[:16]))


def canary_block(canary: str) -> str:
    """The exact text embedded in an item. Keep this wording stable: detectors match it."""
    return f"CANARY GUID {canary}\n{CANARY_SENTENCE}"


def embed_canary(text: str, canary: str) -> str:
    """Append the canary block unless the text already carries this canary."""
    if contains_canary(text, canary):
        return text
    separator = "" if text.endswith("\n") else "\n"
    return f"{text}{separator}\n{canary_block(canary)}\n"


def contains_canary(text: str, canary: str) -> bool:
    return canary in find_canaries(text)


def find_canaries(text: str) -> tuple[str, ...]:
    """Every canary GUID marked in the text, in order, duplicates kept."""
    return tuple(_CANARY_RE.findall(text))
