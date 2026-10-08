"""Bounded local parsers for approved benchmark snapshots; no network or code execution."""

from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import zipfile
import zlib
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal
from urllib.parse import unquote

from polycodebench_core.benchmark_imports import (
    BenchmarkImportPlan,
    BenchmarkImportResult,
    ImportComponent,
    ImportItem,
    ImportState,
    SourceDatePrecision,
)
from polycodebench_core.canonical import canonical_json_bytes

MAX_SOURCE_BYTES = 128 * 1024 * 1024
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_RECORD_BYTES = 4 * 1024 * 1024
MAX_COMPONENT_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 2_048
MAX_ARCHIVE_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
MAX_SOURCE_RECORDS = 200_000
_SCRIPT_SUFFIXES = frozenset(
    {".py", ".pyc", ".pyo", ".sh", ".bash", ".bat", ".cmd", ".ps1", ".exe", ".dll", ".so", ".jar"}
)
_REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$", re.ASCII)
_SHA40 = re.compile(r"^[0-9a-f]{40}$", re.ASCII)


@dataclass(frozen=True, slots=True)
class _SourceRecord:
    item_id: str
    value: dict[str, Any]
    raw: bytes


class _SourceRejected(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def parse_benchmark_snapshot(
    plan: BenchmarkImportPlan, source_bytes: bytes
) -> BenchmarkImportResult:
    """Parse a verified local snapshot and retain every position in its frozen 100-ID sample."""
    if type(source_bytes) is not bytes:
        raise TypeError("benchmark source input must be exact bytes")
    if plan.rights_state != "approved":
        return _blocked_result(plan, "rights_not_approved")
    if len(source_bytes) > MAX_SOURCE_BYTES:
        return _blocked_result(plan, "source_size_limit")
    actual_source_digest = "sha256:" + hashlib.sha256(source_bytes).hexdigest()
    if actual_source_digest != plan.source_digest:
        return _blocked_result(plan, "source_digest_mismatch", observed_digest=actual_source_digest)

    try:
        member_bytes = _read_source_member(plan.source_member, source_bytes)
        records, source_errors = _read_records(plan, member_bytes)
    except _SourceRejected as error:
        return _blocked_result(plan, error.code, observed_digest=actual_source_digest)

    grouped: dict[str, list[_SourceRecord]] = {}
    for record in records:
        grouped.setdefault(record.item_id, []).append(record)
    selected_items: list[ImportItem] = []
    for index, item_id in enumerate(plan.selected_ids):
        matches = grouped.get(item_id, [])
        if len(matches) > 1:
            selected_items.append(
                _result_item(
                    plan,
                    item_id,
                    index,
                    "blocked",
                    errors=("duplicate_source_item_id",),
                )
            )
        elif not matches:
            selected_items.append(
                _result_item(
                    plan,
                    item_id,
                    index,
                    "missing",
                    errors=("selected_id_absent_from_source",),
                )
            )
        else:
            selected_items.append(_adapt_record(plan, index, matches[0]))

    is_complete = not source_errors and all(item.state == "imported" for item in selected_items)
    result_state: Literal["complete", "partial"] = "complete" if is_complete else "partial"
    return BenchmarkImportResult(
        plan_digest=plan.plan_digest,
        source_digest=plan.source_digest,
        observed_source_digest=actual_source_digest,
        membership_digest=plan.membership_digest,
        state=result_state,
        items=tuple(selected_items),
        source_error_codes=tuple(source_errors),
    )


def _blocked_result(
    plan: BenchmarkImportPlan, error_code: str, *, observed_digest: str | None = None
) -> BenchmarkImportResult:
    items = tuple(
        _result_item(plan, item_id, index, "blocked", errors=(error_code,))
        for index, item_id in enumerate(plan.selected_ids)
    )
    return BenchmarkImportResult(
        plan_digest=plan.plan_digest,
        source_digest=plan.source_digest,
        observed_source_digest=observed_digest,
        membership_digest=plan.membership_digest,
        state="blocked",
        items=items,
        source_error_codes=(error_code,),
    )


def _result_item(
    plan: BenchmarkImportPlan,
    item_id: str,
    index: int,
    state: ImportState,
    *,
    errors: tuple[str, ...] = (),
    source_record: bytes | None = None,
    components: tuple[ImportComponent, ...] = (),
    source_date_value: str | None = None,
    source_date_precision: SourceDatePrecision | None = None,
    source_date_raw: str | None = None,
    source_urls: tuple[str, ...] = (),
) -> ImportItem:
    if not source_urls:
        source_urls = (plan.source_uri,)
    digest = (
        None if source_record is None else "sha256:" + hashlib.sha256(source_record).hexdigest()
    )
    links = tuple(link for link in plan.family_links if link.child_item_id == item_id)
    return ImportItem(
        item_id=item_id,
        membership_index=index,
        state=state,
        source_record=source_record,
        source_record_digest=digest,
        components=components,
        error_codes=errors,
        source_date_value=source_date_value,
        source_date_precision=source_date_precision,
        source_date_raw=source_date_raw,
        source_urls=source_urls,
        family_links=links,
        source_public_exposure=plan.source_visibility == "public",
    )


def _read_source_member(source_member: str, source_bytes: bytes) -> bytes:
    if not source_bytes.startswith(b"PK\x03\x04"):
        if source_member.lower().endswith(".zip"):
            raise _SourceRejected("expected_zip_archive")
        if len(source_bytes) > MAX_MEMBER_BYTES:
            raise _SourceRejected("member_size_limit")
        return source_bytes
    try:
        with zipfile.ZipFile(io.BytesIO(source_bytes)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_ENTRIES:
                raise _SourceRejected("archive_entry_limit")
            seen: set[str] = set()
            expanded_size = 0
            selected = None
            for info in infos:
                _validate_zip_info(info)
                normalized = info.filename.rstrip("/").casefold()
                if normalized in seen:
                    raise _SourceRejected("archive_duplicate_member")
                seen.add(normalized)
                if info.is_dir():
                    continue
                expanded_size += info.file_size
                if expanded_size > MAX_ARCHIVE_EXPANDED_BYTES:
                    raise _SourceRejected("archive_expansion_limit")
                if info.filename == source_member:
                    selected = info
            if selected is None:
                raise _SourceRejected("source_member_missing")
            if selected.file_size > MAX_MEMBER_BYTES:
                raise _SourceRejected("member_size_limit")
            with archive.open(selected, "r") as stream:
                chunks: list[bytes] = []
                total = 0
                while chunk := stream.read(64 * 1024):
                    total += len(chunk)
                    if total > MAX_MEMBER_BYTES:
                        raise _SourceRejected("member_size_limit")
                    chunks.append(chunk)
                member_bytes = b"".join(chunks)
            if len(member_bytes) != selected.file_size:
                raise _SourceRejected("archive_member_size_mismatch")
            return member_bytes
    except _SourceRejected:
        raise
    except (
        OSError,
        EOFError,
        ValueError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        RuntimeError,
        zlib.error,
    ):
        raise _SourceRejected("archive_invalid") from None


def _validate_zip_info(info: zipfile.ZipInfo) -> None:
    name = info.filename
    if (
        not name
        or not name.isascii()
        or name.startswith(("/", "\\"))
        or "\\" in name
        or "\x00" in name
        or ":" in name
        or any(part in {"", ".", ".."} for part in name.rstrip("/").split("/"))
        or any(ord(char) < 0x20 for char in name)
    ):
        raise _SourceRejected("archive_unsafe_path")
    try:
        decoded_name = unquote(name, errors="strict")
    except UnicodeError:
        raise _SourceRejected("archive_unsafe_path") from None
    if decoded_name != name:
        raise _SourceRejected("archive_unsafe_path")
    if info.flag_bits & 0x1:
        raise _SourceRejected("archive_encrypted_member")
    if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
        raise _SourceRejected("archive_compression_unsupported")
    mode = info.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    allowed_types = {0, stat.S_IFREG, stat.S_IFDIR}
    if file_type not in allowed_types:
        raise _SourceRejected("archive_link_or_special_file")
    if info.is_dir() != (file_type == stat.S_IFDIR and name.endswith("/")):
        raise _SourceRejected("archive_file_type_mismatch")
    suffix = name.rsplit("/", 1)[-1].lower()
    if any(suffix.endswith(script_suffix) for script_suffix in _SCRIPT_SUFFIXES):
        raise _SourceRejected("archive_script_asset")
    if info.file_size > MAX_ARCHIVE_EXPANDED_BYTES:
        raise _SourceRejected("archive_expansion_limit")
    if info.file_size and (
        info.compress_size == 0 or info.file_size / info.compress_size > MAX_COMPRESSION_RATIO
    ):
        raise _SourceRejected("archive_compression_ratio_limit")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid_json_constant:{value}")


def _parse_record(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_RECORD_BYTES:
        raise _SourceRejected("source_record_size_limit")
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise _SourceRejected("source_record_invalid") from None
    if not isinstance(value, dict):
        raise _SourceRejected("source_record_not_object")
    return value


def _read_records(
    plan: BenchmarkImportPlan, member_bytes: bytes
) -> tuple[tuple[_SourceRecord, ...], tuple[str, ...]]:
    try:
        text = member_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise _SourceRejected("source_invalid_utf8") from None
    suffix = plan.source_member.rsplit(".", 1)[-1].lower()
    expected_format = "json" if suffix == "json" else "jsonl"
    if dict(plan.parser_config).get("format") != expected_format:
        raise _SourceRejected("parser_format_config_mismatch")
    if expected_format == "json":
        raw_records, errors = _json_array_records(text)
    else:
        raw_records, errors = _jsonl_records(text)

    records: list[_SourceRecord] = []
    source_errors = list(errors)
    for raw in raw_records:
        try:
            value = _parse_record(raw.rstrip(b"\r\n"))
        except _SourceRejected as error:
            source_errors.append(error.code)
            continue
        item_id = _source_item_id(plan, value)
        if item_id is None:
            source_errors.append("source_item_id_invalid")
            continue
        records.append(_SourceRecord(item_id=item_id, value=value, raw=raw))
        if len(records) > MAX_SOURCE_RECORDS:
            raise _SourceRejected("source_record_count_limit")
    return tuple(records), tuple(sorted(set(source_errors)))


def _jsonl_records(text: str) -> tuple[tuple[bytes, ...], tuple[str, ...]]:
    records: list[bytes] = []
    errors: list[str] = []
    encoded_lines = text.encode("utf-8")
    for raw in encoded_lines.splitlines(keepends=True):
        if not raw.strip():
            continue
        if len(raw) > MAX_RECORD_BYTES:
            errors.append("source_record_size_limit")
            continue
        records.append(raw)
        if len(records) > MAX_SOURCE_RECORDS:
            raise _SourceRejected("source_record_count_limit")
    return tuple(records), tuple(errors)


def _json_array_records(text: str) -> tuple[tuple[bytes, ...], tuple[str, ...]]:
    decoder = json.JSONDecoder(parse_constant=_reject_json_constant)
    cursor = 0
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    if cursor >= len(text) or text[cursor] != "[":
        raise _SourceRejected("source_json_array_required")
    cursor += 1
    raw_records: list[bytes] = []
    try:
        while True:
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
            if cursor >= len(text):
                raise _SourceRejected("source_json_array_invalid")
            if text[cursor] == "]":
                cursor += 1
                break
            start = cursor
            _, cursor = decoder.raw_decode(text, cursor)
            raw = text[start:cursor].encode("utf-8")
            if len(raw) > MAX_RECORD_BYTES:
                raise _SourceRejected("source_record_size_limit")
            raw_records.append(raw)
            if len(raw_records) > MAX_SOURCE_RECORDS:
                raise _SourceRejected("source_record_count_limit")
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
            if cursor < len(text) and text[cursor] == ",":
                cursor += 1
                continue
            if cursor < len(text) and text[cursor] == "]":
                cursor += 1
                break
            raise _SourceRejected("source_json_array_invalid")
    except _SourceRejected:
        raise
    except (json.JSONDecodeError, ValueError, RecursionError):
        raise _SourceRejected("source_json_array_invalid") from None
    if text[cursor:].strip():
        raise _SourceRejected("source_json_trailing_content")
    return tuple(raw_records), ()


def _source_item_id(plan: BenchmarkImportPlan, value: dict[str, Any]) -> str | None:
    if plan.benchmark_slug in {"humaneval", "mbpp"}:
        raw_id = value.get("task_id")
        if type(raw_id) is int and raw_id >= 0:
            return str(raw_id) if plan.benchmark_slug == "mbpp" else None
        if plan.benchmark_slug == "humaneval" and isinstance(raw_id, str):
            return raw_id
        if plan.benchmark_slug == "mbpp" and isinstance(raw_id, str) and raw_id.isdecimal():
            return str(int(raw_id))
        return None
    raw_id = value.get("instance_id")
    return raw_id if isinstance(raw_id, str) and raw_id else None


def _adapt_record(plan: BenchmarkImportPlan, index: int, record: _SourceRecord) -> ImportItem:
    urls: tuple[str, ...]
    if plan.benchmark_slug == "humaneval":
        components, errors = _adapt_humaneval(record.value)
        urls = (plan.source_uri,)
    elif plan.benchmark_slug == "mbpp":
        components, errors = _adapt_mbpp(record.value)
        urls = (plan.source_uri,)
    else:
        components, errors, urls = _adapt_swe_bench(plan, record)
    source_date_value, source_date_precision, source_date_raw, date_error = _source_date(
        plan, record.value
    )
    if date_error is not None:
        errors.append(date_error)
    state: ImportState = "imported" if not errors else "incomplete"
    return _result_item(
        plan,
        record.item_id,
        index,
        state,
        errors=tuple(sorted(set(errors))),
        source_record=record.raw,
        components=tuple(components),
        source_date_value=source_date_value,
        source_date_precision=source_date_precision,
        source_date_raw=source_date_raw,
        source_urls=urls,
    )


def _adapt_humaneval(value: dict[str, Any]) -> tuple[list[ImportComponent], list[str]]:
    return _components(
        value,
        {
            "prompt": ("prompt", "text"),
            "entry_point": ("entry_point",),
            "canonical_solution": ("canonical_solution",),
            "tests": ("test",),
        },
    )


def _adapt_mbpp(value: dict[str, Any]) -> tuple[list[ImportComponent], list[str]]:
    components, errors = _components(
        value,
        {
            "prompt": ("prompt", "text"),
            "canonical_solution": ("code",),
            "tests": ("test_list",),
        },
    )
    for key, aliases in (
        ("test_imports", ("test_imports",)),
        ("challenge_tests", ("challenge_test_list",)),
    ):
        present, content = _first_field(value, aliases)
        if present:
            component = _component(key, content, required=False)
            if component is None:
                errors.append(f"component_invalid:{key}")
            else:
                components.append(component)
    return components, errors


def _adapt_swe_bench(
    plan: BenchmarkImportPlan, record: _SourceRecord
) -> tuple[list[ImportComponent], list[str], tuple[str, ...]]:
    value = record.value
    components, errors = _components(
        value,
        {
            "repository": ("repo",),
            "base_commit": ("base_commit",),
            "issue": ("problem_statement",),
            "gold_patch": ("patch",),
            "test_patch": ("test_patch",),
        },
        allow_empty=frozenset({"gold_patch", "test_patch"}),
    )
    repo = value.get("repo")
    base_commit = value.get("base_commit")
    instance_id = value.get("instance_id")
    if not isinstance(base_commit, str) or not _SHA40.fullmatch(base_commit):
        errors.append("base_commit_reference_invalid")
    if (
        not isinstance(repo, str)
        or not _REPO.fullmatch(repo)
        or any(
            part in {".", ".."} or part.startswith(".") or part.endswith(".") or ".." in part
            for part in repo.split("/")
        )
        or not isinstance(instance_id, str)
        or not instance_id.startswith(f"{repo}__")
    ):
        errors.append("repository_reference_invalid")
        return components, errors, (plan.source_uri,)
    issue_number = instance_id.rsplit("__", 1)[-1]
    if not issue_number.isdecimal():
        errors.append("issue_reference_invalid")
        return components, errors, (plan.source_uri, f"https://github.com/{repo}")
    return (
        components,
        errors,
        (
            plan.source_uri,
            f"https://github.com/{repo}",
            f"https://github.com/{repo}/issues/{issue_number}",
        ),
    )


def _components(
    value: dict[str, Any],
    mapping: dict[str, tuple[str, ...]],
    *,
    allow_empty: frozenset[str] = frozenset(),
) -> tuple[list[ImportComponent], list[str]]:
    components: list[ImportComponent] = []
    errors: list[str] = []
    for key, aliases in mapping.items():
        present, raw_value = _first_field(value, aliases)
        if not present:
            errors.append(f"component_missing:{key}")
            continue
        component = _component(key, raw_value, required=key not in allow_empty)
        if component is None:
            errors.append(f"component_invalid:{key}")
        else:
            components.append(component)
    return components, errors


def _first_field(value: dict[str, Any], aliases: tuple[str, ...]) -> tuple[bool, Any]:
    for alias in aliases:
        if alias in value:
            return True, value[alias]
    return False, None


def _component(key: str, value: Any, *, required: bool) -> ImportComponent | None:
    if isinstance(value, str):
        if required and not value.strip():
            return None
        content = value.encode("utf-8", errors="strict")
        media_type = "text/plain; charset=utf-8"
    elif isinstance(value, list):
        if required and not value:
            return None
        if any(not isinstance(item, str) for item in value):
            return None
        content = canonical_json_bytes(value)
        media_type = "application/json"
    else:
        return None
    if len(content) > MAX_COMPONENT_BYTES:
        return None
    return ImportComponent(
        component_key=key,
        media_type=media_type,
        content=content,
        digest="sha256:" + hashlib.sha256(content).hexdigest(),
    )


def _source_date(
    plan: BenchmarkImportPlan, value: dict[str, Any]
) -> tuple[str | None, SourceDatePrecision | None, str | None, str | None]:
    if plan.source_date_field is None or plan.source_date_field not in value:
        return None, None, None, None
    raw = value[plan.source_date_field]
    if not isinstance(raw, str):
        return None, None, str(raw), "source_date_unrecognized"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw, re.ASCII):
        try:
            date.fromisoformat(raw)
        except ValueError:
            return None, None, raw, "source_date_unrecognized"
        return raw, "day", None, None
    match = re.fullmatch(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?Z", raw, re.ASCII)
    if match is None:
        return None, None, raw, "source_date_unrecognized"
    fraction = match.group(2) or ""
    precision_by_digits: dict[int, SourceDatePrecision] = {
        0: "second",
        3: "millisecond",
        6: "microsecond",
        9: "nanosecond",
    }
    precision = precision_by_digits.get(len(fraction))
    if precision is None:
        return None, None, raw, "source_date_precision_unsupported"
    try:
        datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError:
        return None, None, raw, "source_date_unrecognized"
    return raw, precision, None, None
