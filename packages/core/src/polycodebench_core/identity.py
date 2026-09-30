"""Stable IDs, exact bytes, monotonic durations, safe paths and deterministic seeds."""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from polycodebench_core.canonical import canonical_json_bytes
from polycodebench_core.errors import UnsafePathError
from polycodebench_core.models import (
    SAFE_JSON_INTEGER_MAX,
    UNSIGNED_INT64_MAX,
    BundleFile,
    BundleFileManifest,
    Digest,
)


def new_entity_id() -> str:
    """Return an application-generated, canonical lowercase UUIDv4."""
    return str(uuid.uuid4())


def utc_timestamp(value: datetime | None = None) -> str:
    moment = value or datetime.now(UTC)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("timestamp input must be timezone-aware")
    utc = moment.astimezone(UTC)
    return utc.isoformat(timespec="microseconds").replace("+00:00", "Z")


def validate_relative_path(path: str) -> str:
    """Validate without normalizing so hashes retain the supplied path spelling."""
    if type(path) is not str or not path:
        raise UnsafePathError("path must be a non-empty string")
    if "\x00" in path:
        raise UnsafePathError("path contains NUL")
    if any(0xD800 <= ord(char) <= 0xDFFF for char in path):
        raise UnsafePathError("path contains a lone Unicode surrogate")
    if "\\" in path:
        raise UnsafePathError("path must use POSIX separators")
    if path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        raise UnsafePathError("absolute and drive-prefixed paths are forbidden")
    components = path.split("/")
    if any(component in ("", ".", "..") for component in components):
        raise UnsafePathError("empty, dot and parent path components are forbidden")
    try:
        path.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise UnsafePathError("path is not valid UTF-8") from exc
    return path


@dataclass(slots=True)
class MonotonicTimer:
    """Measure elapsed duration from monotonic nanoseconds, never wall time."""

    start_ns: int

    @classmethod
    def start(cls) -> MonotonicTimer:
        return cls(time.monotonic_ns())

    def elapsed_ns(self, end_ns: int | None = None) -> int:
        end = time.monotonic_ns() if end_ns is None else end_ns
        if type(self.start_ns) is not int or type(end) is not int:
            raise TypeError("monotonic clock values must be integers")
        if self.start_ns < 0 or end < self.start_ns:
            raise ValueError("monotonic duration cannot be negative")
        return end - self.start_ns


def derive_sample_seed(master_seed: str, task_version_digest: str, sample_index: int) -> str:
    """Derive the spec's first unsigned big-endian 64 bits as a decimal string."""
    if not re.fullmatch(r"(?:0|[1-9][0-9]{0,19})", master_seed):
        raise ValueError("master_seed must be a canonical unsigned decimal string")
    if int(master_seed) > UNSIGNED_INT64_MAX:
        raise ValueError("master_seed exceeds unsigned 64-bit range")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", task_version_digest):
        raise ValueError("task_version_digest must be a lowercase SHA-256 digest")
    if type(sample_index) is not int or not 0 <= sample_index <= SAFE_JSON_INTEGER_MAX:
        raise ValueError("sample_index must be a nonnegative safe JSON integer")
    tuple_bytes = canonical_json_bytes([master_seed, task_version_digest, sample_index])
    return str(int.from_bytes(hashlib.sha256(tuple_bytes).digest()[:8], "big", signed=False))


def file_manifest(files: list[BundleFile]) -> BundleFileManifest:
    """Validate uniqueness and return entries sorted by UTF-8 path bytes."""
    for item in files:
        validate_relative_path(item.path)
        if item.symlink_target is not None:
            validate_relative_path(item.symlink_target)
    paths = [item.path for item in files]
    if len(paths) != len(set(paths)):
        raise ValueError("bundle manifest paths must be unique")
    ordered = sorted(files, key=lambda item: item.path.encode("utf-8"))
    return BundleFileManifest(schema_version=1, kind="bundle_file_manifest", files=ordered)


def bundle_digest(manifest: BundleFileManifest) -> Digest:
    from polycodebench_core.canonical import canonical_document_digest

    return canonical_document_digest(manifest)
