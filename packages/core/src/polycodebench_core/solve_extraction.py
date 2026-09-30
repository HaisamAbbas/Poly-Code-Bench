"""Deterministic submission extraction and candidate freezing (Technical Spec 4.3, 9.4, 6.2).

No model is ever consulted to choose a candidate. A response either matches the declared
extraction rule exactly or the candidate is marked ``contract_invalid`` with named reasons; the
original response is preserved by the caller. Submissions are scored as written: nothing is
repaired, reformatted or trimmed here. A separately valid part (for example a patch next to an
over-long findings list) stays evaluable.
"""

from __future__ import annotations

import base64
import difflib
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import Field, ValidationError

from polycodebench_core.model_contracts import Strict, stable_json_bytes
from polycodebench_core.models import TaskOutputContract
from polycodebench_core.solve_contracts import (
    PathForbidden,
    is_protected,
    normalize_workspace_path,
)

DEFAULT_MAX_FINDINGS = 20
MAX_FINDING_SPAN_LINES = 50
_FENCE = re.compile(r"^```[ \t]*([A-Za-z0-9_+.#-]*)[ \t]*\r?\n(.*?)\r?\n?```[ \t]*$", re.S | re.M)
Severity = Literal["low", "medium", "high", "critical"]


class Finding(Strict):
    local_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")]
    path: str
    start_line: Annotated[int, Field(ge=1)]
    end_line: Annotated[int, Field(ge=1)]
    symbol: Annotated[str, Field(max_length=256)] | None = None
    root_cause: Annotated[str, Field(min_length=1, max_length=4000)]
    evidence: Annotated[str, Field(max_length=4000)] = ""
    severity: Severity
    confidence: Annotated[str, Field(pattern=r"^(low|medium|high)$")] | None = None


@dataclass(frozen=True)
class Extraction:
    submission_kind: str
    validity: Literal["valid", "contract_invalid"]
    reasons: tuple[str, ...]
    payload: dict[str, Any]
    ignored: tuple[str, ...] = ()

    def content_bytes(self) -> bytes:
        """The exact bytes stored as the candidate artifact; its digest is the candidate digest."""
        return stable_json_bytes(
            {
                "submission_kind": self.submission_kind,
                "validity": self.validity,
                "reasons": list(self.reasons),
                "payload": self.payload,
            }
        )

    def digest(self) -> str:
        return "sha256:" + hashlib.sha256(self.content_bytes()).hexdigest()

    def summary(self) -> dict[str, Any]:
        """Metadata-only view stored in the candidate row (no file contents)."""
        meta: dict[str, Any] = {
            "submission_kind": self.submission_kind,
            "validity": self.validity,
            "reasons": list(self.reasons),
            "ignored_changes": list(self.ignored),
        }
        files = self.payload.get("files")
        if isinstance(files, list):
            meta["files"] = [
                {key: item[key] for key in ("path", "size", "digest")} for item in files
            ]
        for key in ("patch_digest", "answer_digest", "findings_count"):
            if key in self.payload:
                meta[key] = self.payload[key]
        return meta


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key in response envelope")
        result[key] = value
    return result


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-finite number {name}")


def _parse_envelope(text: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        value = json.loads(
            text.strip(),
            object_pairs_hook=_no_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (ValueError, RecursionError):
        return None, "envelope_not_json"
    if not isinstance(value, dict):
        return None, "envelope_not_object"
    return value, None


def _under(path: str, allowed: list[str]) -> bool:
    return any(path == item or path.startswith(item.rstrip("/") + "/") for item in allowed)


def _file_entry(path: str, data: bytes) -> dict[str, Any]:
    return {
        "path": path,
        "size": len(data),
        "digest": _sha(data),
        "content_b64": base64.b64encode(data).decode("ascii"),
    }


def _check_file_set(
    files: Mapping[str, bytes],
    contract: TaskOutputContract,
    required_outputs: list[str],
) -> list[str]:
    reasons: list[str] = []
    if not files:
        reasons.append("no_files")
    if len(files) > contract.maximum_files:
        reasons.append("too_many_files")
    if any(len(data) > contract.maximum_file_bytes for data in files.values()):
        reasons.append("file_too_large")
    if sum(len(data) for data in files.values()) > contract.maximum_artifact_bytes:
        reasons.append("artifact_too_large")
    for path in files:
        if not _under(path, contract.allowed_paths):
            reasons.append("path_not_allowed")
    for required in required_outputs:
        if required not in files:
            reasons.append("missing_required_output")
    return sorted(set(reasons))


def _files_extraction(
    files: Mapping[str, bytes],
    contract: TaskOutputContract,
    required_outputs: list[str],
    *,
    ignored: tuple[str, ...] = (),
    extra_reasons: tuple[str, ...] = (),
) -> Extraction:
    reasons = tuple(sorted({*_check_file_set(files, contract, required_outputs), *extra_reasons}))
    return Extraction(
        submission_kind="files",
        validity="contract_invalid" if reasons else "valid",
        reasons=reasons,
        payload={"files": [_file_entry(path, files[path]) for path in sorted(files)]},
        ignored=ignored,
    )


def _invalid(kind: str, *reasons: str) -> Extraction:
    return Extraction(kind, "contract_invalid", tuple(sorted(set(reasons))), {})


# ------------------------------------------------------------------ response extraction


def extract_from_response(
    text: str,
    *,
    contract: TaskOutputContract,
    rule: Literal["json_envelope", "single_fenced_block"],
    required_outputs: list[str],
    protected_paths: list[str] | None = None,
) -> Extraction:
    """Apply the declared rule to a final model response; never raises on model text."""
    try:
        return _extract(text, contract, rule, required_outputs, protected_paths or [])
    except (RecursionError, UnicodeError, ValueError, TypeError, OverflowError):
        return _invalid(contract.submission_kind, "unprocessable_response")


def _extract(
    text: str,
    contract: TaskOutputContract,
    rule: Literal["json_envelope", "single_fenced_block"],
    required_outputs: list[str],
    protected_paths: list[str],
) -> Extraction:
    kind = contract.submission_kind
    if rule == "single_fenced_block":
        if kind != "files" or len(contract.allowed_paths) != 1:
            return _invalid(kind, "fenced_rule_needs_one_output_file")
        blocks = _FENCE.findall(text)
        if not blocks:
            return _invalid(kind, "no_fenced_block")
        if len(blocks) > 1:
            # An LLM extractor must not choose the better block: ambiguity is invalid.
            return _invalid(kind, "ambiguous_multiple_blocks")
        data = blocks[0][1].encode("utf-8")
        return _files_extraction({contract.allowed_paths[0]: data}, contract, required_outputs)

    envelope, problem = _parse_envelope(text)
    if envelope is None:
        return _invalid(kind, problem or "envelope_invalid")
    if kind == "files":
        return _from_files_envelope(envelope, contract, required_outputs)
    if kind == "patch":
        return _from_patch_envelope(envelope, contract, protected_paths)
    if kind == "text":
        answer = envelope.get("answer")
        if set(envelope) != {"answer"} or not isinstance(answer, str):
            return _invalid(kind, "answer_field_missing_or_extra_fields")
        data = answer.encode("utf-8")
        if len(data) > contract.maximum_artifact_bytes:
            return _invalid(kind, "artifact_too_large")
        return Extraction(kind, "valid", (), {"answer": answer, "answer_digest": _sha(data)})
    if kind == "typed_json":
        if set(envelope) != {"answer"}:
            return _invalid(kind, "answer_field_missing_or_extra_fields")
        data = stable_json_bytes(envelope["answer"])
        if len(data) > contract.maximum_artifact_bytes:
            return _invalid(kind, "artifact_too_large")
        return Extraction(
            kind, "valid", (), {"answer": envelope["answer"], "answer_digest": _sha(data)}
        )
    return _from_findings_envelope(envelope, contract, protected_paths)


def _from_files_envelope(
    envelope: dict[str, Any], contract: TaskOutputContract, required_outputs: list[str]
) -> Extraction:
    entries = envelope.get("files")
    if set(envelope) != {"files"} or not isinstance(entries, list):
        return _invalid("files", "files_field_missing_or_extra_fields")
    files: dict[str, bytes] = {}
    reasons: list[str] = []
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"path", "content"}
            or not isinstance(entry["content"], str)
        ):
            reasons.append("malformed_file_entry")
            continue
        try:
            path = normalize_workspace_path(entry["path"])
        except PathForbidden:
            reasons.append("path_forbidden")
            continue
        if path in files:
            reasons.append("duplicate_path")
            continue
        files[path] = entry["content"].encode("utf-8")
    return _files_extraction(files, contract, required_outputs, extra_reasons=tuple(reasons))


def _header_name(raw: str) -> str | None:
    """Path named by a ``---``/``+++`` header, ``None`` for /dev/null; raises on malformed."""
    name = raw.split("\t")[0].rstrip()
    if name == "/dev/null":
        return None
    if name.startswith(("a/", "b/")):
        name = name[2:]
    return normalize_workspace_path(name)


def patch_targets(diff: str) -> tuple[list[str], list[str]] | None:
    """(touched paths, problems) from every header pair, or ``None`` if a header is malformed.

    Both sides of each pair are checked, so a rename cannot hide a source or target path.
    """
    paths: list[str] = []
    problems: list[str] = []
    lines = diff.split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]
        if (
            line.startswith("--- ")
            and index + 1 < len(lines)
            and lines[index + 1].startswith("+++ ")
        ):
            try:
                old = _header_name(line[4:])
                new = _header_name(lines[index + 1][4:])
            except PathForbidden:
                return None
            if old is not None and new is not None and old != new:
                problems.append("patch_rename")
            paths.extend(name for name in (old, new) if name is not None)
            index += 2
            continue
        index += 1
    return paths, problems


def patch_paths(diff: str) -> list[str] | None:
    """Target paths named by a unified diff, or ``None`` if a header is malformed."""
    found = patch_targets(diff)
    return None if found is None else sorted(set(found[0]))


def _patch_problems(
    diff: str, contract: TaskOutputContract, protected_paths: list[str] | None = None
) -> list[str]:
    problems: list[str] = []
    data = diff.encode("utf-8")
    if not diff.strip():
        return ["empty_patch"]
    if len(data) > contract.maximum_artifact_bytes:
        problems.append("artifact_too_large")
    if "\r" in diff:
        problems.append("patch_carriage_return")
    found = patch_targets(diff)
    if found is None:
        return sorted({*problems, "patch_path_forbidden"})
    paths, extra = found
    problems.extend(extra)
    for path in paths:
        if not _under(path, contract.allowed_paths):
            problems.append("patch_path_not_allowed")
        if is_protected(path, protected_paths or []):
            problems.append("patch_touches_protected_path")
    if re.search(r"^(GIT binary patch|Binary files )", diff, re.M):
        problems.append("binary_patch")
    if not paths:
        problems.append("patch_names_no_files")
    return sorted(set(problems))


def _from_patch_envelope(
    envelope: dict[str, Any], contract: TaskOutputContract, protected_paths: list[str]
) -> Extraction:
    diff = envelope.get("patch")
    if set(envelope) != {"patch"} or not isinstance(diff, str):
        return _invalid("patch", "patch_field_missing_or_extra_fields")
    reasons = tuple(_patch_problems(diff, contract, protected_paths))
    return Extraction(
        "patch",
        "contract_invalid" if reasons else "valid",
        reasons,
        {"patch": diff, "patch_digest": _sha(diff.encode("utf-8"))},
    )


def _from_findings_envelope(
    envelope: dict[str, Any], contract: TaskOutputContract, protected_paths: list[str]
) -> Extraction:
    extra = set(envelope) - {"findings", "patch", "associations"}
    raw = envelope.get("findings")
    if extra or not isinstance(raw, list):
        return _invalid("structured_findings", "findings_field_missing_or_extra_fields")
    limit = contract.findings_limit or DEFAULT_MAX_FINDINGS
    reasons: list[str] = []
    findings: list[dict[str, Any]] = []
    if len(raw) > limit:
        # Never silently keep the best N: the detection submission is invalid.
        reasons.append("findings_over_limit")
    else:
        seen: set[str] = set()
        for item in raw:
            try:
                finding = Finding.model_validate(item, strict=False)
                path = normalize_workspace_path(finding.path)
            except (ValidationError, PathForbidden):
                reasons.append("malformed_finding")
                continue
            if finding.end_line < finding.start_line:
                reasons.append("finding_span_inverted")
            elif finding.end_line - finding.start_line + 1 > MAX_FINDING_SPAN_LINES:
                reasons.append("finding_span_too_long")
            if finding.local_id in seen:
                reasons.append("duplicate_finding_id")
            seen.add(finding.local_id)
            findings.append({**finding.model_dump(), "path": path})
    payload: dict[str, Any] = {"findings": findings, "findings_count": len(raw)}
    ignored: list[str] = []
    patch = envelope.get("patch")
    if patch is not None:
        if not isinstance(patch, str):
            reasons.append("patch_not_a_string")
        else:
            patch_issues = _patch_problems(patch, contract, protected_paths)
            if patch_issues:
                ignored.append("patch_invalid:" + ",".join(patch_issues))
            else:
                payload["patch"] = patch
                payload["patch_digest"] = _sha(patch.encode("utf-8"))
    if isinstance(envelope.get("associations"), list):
        payload["associations"] = envelope["associations"]
    return Extraction(
        "structured_findings",
        "contract_invalid" if reasons else "valid",
        tuple(sorted(set(reasons))),
        payload,
        ignored=tuple(ignored),
    )


# ------------------------------------------------------------------ workspace freezing


def freeze_workspace_files(
    workspace: Mapping[str, bytes],
    *,
    contract: TaskOutputContract,
    required_outputs: list[str],
    protected_baseline: Mapping[str, bytes],
) -> Extraction:
    """Freeze declared output files from an agent workspace, as written."""
    selected = {
        path: data for path, data in workspace.items() if _under(path, contract.allowed_paths)
    }
    outside = tuple(
        sorted(f"outside_contract:{path}" for path in workspace if path not in selected)
    )
    violations = _protected_violations(workspace, protected_baseline)
    return _files_extraction(
        selected,
        contract,
        required_outputs,
        ignored=outside,
        extra_reasons=tuple(violations),
    )


def _protected_violations(
    workspace: Mapping[str, bytes], protected_baseline: Mapping[str, bytes]
) -> list[str]:
    changed = [path for path, data in protected_baseline.items() if workspace.get(path) != data]
    return ["protected_path_modified"] if changed else []


def protected_changes(
    workspace: Mapping[str, bytes], protected_baseline: Mapping[str, bytes]
) -> list[str]:
    return sorted(path for path, data in protected_baseline.items() if workspace.get(path) != data)


def _diff_lines(old: bytes | None, new: bytes | None, path: str) -> list[str]:
    def split(data: bytes | None) -> list[str]:
        return [] if data is None else _keep_newlines(data.decode("utf-8"))

    old_lines, new_lines = split(old), split(new)
    out: list[str] = []
    fromfile = "/dev/null" if old is None else f"a/{path}"
    tofile = "/dev/null" if new is None else f"b/{path}"
    body = list(difflib.unified_diff(old_lines, new_lines, fromfile, tofile, lineterm="\n"))
    if not body and (old is None) != (new is None):
        # Creating or deleting an empty file has no hunks; the header pair still records it.
        return [f"--- {fromfile}\n", f"+++ {tofile}\n"]
    for line in body:
        if line.endswith("\n"):
            out.append(line)
        else:
            out.append(line + "\n\\ No newline at end of file\n")
    return out


def _keep_newlines(text: str) -> list[str]:
    """Lines split on ``\\n`` only (form feeds and unicode separators stay inside a line)."""
    pieces = text.split("\n")
    lines = [piece + "\n" for piece in pieces[:-1]]
    if pieces[-1]:
        lines.append(pieces[-1])
    return lines


def workspace_patch(
    base: Mapping[str, bytes],
    workspace: Mapping[str, bytes],
    *,
    contract: TaskOutputContract,
    protected_baseline: Mapping[str, bytes],
) -> Extraction:
    """A unified diff of changes inside the output contract; other changes are recorded only."""
    changed = sorted(path for path in {*base, *workspace} if base.get(path) != workspace.get(path))
    inside = [path for path in changed if _under(path, contract.allowed_paths)]
    outside = tuple(f"outside_contract:{path}" for path in changed if path not in inside)
    reasons = list(_protected_violations(workspace, protected_baseline))
    chunks: list[str] = []
    for path in inside:
        try:
            chunks.extend(_diff_lines(base.get(path), workspace.get(path), path))
        except UnicodeDecodeError:
            reasons.append("binary_change_in_patch")
    diff = "".join(chunks)
    if not diff and not reasons:
        reasons.append("empty_patch")
    if len(diff.encode("utf-8")) > contract.maximum_artifact_bytes:
        reasons.append("artifact_too_large")
    return Extraction(
        "patch",
        "contract_invalid" if reasons else "valid",
        tuple(sorted(set(reasons))),
        {"patch": diff, "patch_digest": _sha(diff.encode("utf-8"))},
        ignored=outside,
    )
