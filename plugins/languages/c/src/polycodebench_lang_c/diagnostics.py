"""Shared diagnostic text parsers for the C toolchain.

clang, clang-tidy and cppcheck all emit a line-oriented diagnostic that names a file, a line, a
column, a severity and a rule. Parsing that one shape once means a check that fires under two tools
produces the same canonical diagnostic, which is what lets the profile merge them into one issue.

Only the *text* form is used. clang 14 has no ``-fdiagnostics-format=``, and depending on a version
gated output format would make the plugin's behaviour depend on the image's compiler instead of the
frozen rules bundle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Severity = Literal["critical", "high", "medium", "low"]

#: ``path:line:col: severity: message [-Wflag]`` (clang, clang-tidy) and the same without the column
#: (some cppcheck versions). The rule id may arrive as ``[-Wflag]`` or ``[id]``.
_DIAGNOSTIC = re.compile(
    r"^(?P<path>[^:\n]+):(?P<line>\d+)(?::(?P<column>\d+))?:\s*"
    r"(?P<severity>fatal error|error|warning|note|information|performance|portability|"
    r"style|debug|unusedFunction)\s*:\s*(?P<message>.+?)"
    r"(?:\s*\[(?P<rule>[^\]]+)\])?\s*$"
)
#: A bare ``error:`` or ``warning:`` with no file position: a whole-translation-unit failure.
_BARE = re.compile(r"^(?P<severity>fatal error|error|warning):\s*(?P<message>.+)$")
#: ``note:`` lines explain the line above and belong to it; they are evidence, not findings.
_SUMMARY = re.compile(
    r"^(?:Suppressed|Enabled|Checks|files? checked|Total|Checking|Project|Detected|"
    r"Active|C-\w+)\b",
    re.IGNORECASE,
)
#: ``Checking src/topwords.c...`` - cppcheck's own statement that it read a file. The only evidence a
#: cppcheck scan gives that it looked at anything at all.
CHECKING = re.compile(r"^\s*(?:Checking|1/(\d+)) files?\s+(checked|skipped)\s+\((\d+)%\)")
_SEVERITY_MAP = {
    "critical": "critical",
    "fatal error": "high",
    "error": "high",
    "warning": "medium",
    "portability": "medium",
    "performance": "low",
    "style": "low",
    "unusedfunction": "low",
    "note": "low",
    "information": "low",
    "debug": "low",
}


@dataclass(frozen=True)
class Diagnostic:
    """One normalized compiler or analyzer diagnostic."""

    path: str
    line: int
    column: int | None
    severity: str
    message: str
    rule: str | None
    kind: Literal["compiler", "tidy", "cppcheck"]

    @property
    def blocks_build(self) -> bool:
        """True only for a hard error. A warning is evidence, never a build failure on its own."""
        return self.severity in {"high", "critical"}

    @property
    def is_note(self) -> bool:
        return self.message.lower().startswith("note:") or self.severity == "note"


def normalize_rule(rule: str | None, message: str) -> str | None:
    """The rule id a check is filed under.

    ``-Wunused-variable`` becomes ``unused-variable``; a cppcheck ``[uninitvar]`` stays ``uninitvar``;
    a compiler diagnostic with no flag falls back to the message's leading words so two identical
    compiler errors at different sites still merge rather than becoming unique keys.
    """
    if rule:
        cleaned = rule.strip()
        if cleaned.startswith("-W"):
            cleaned = cleaned[2:]
        return re.sub(r"[^a-z0-9._-]+", "-", cleaned.lower()).strip("-.") or None
    lowered = message.lower()
    named = re.match(r"^\s*(?:use of|incompatible|passing|unused|no member|call to)\b\s*([\w.']+)", lowered)
    if named:
        return re.sub(r"[^a-z0-9._-]+", "-", named.group(1).strip("'"))[:60]
    return "compiler-diagnostic"


def parse_diagnostics(
    text: str, *, kind: Literal["compiler", "tidy", "cppcheck"]
) -> tuple[Diagnostic | None, ...]:
    """Parse a whole capture. A trailing summary line becomes the ``None`` slot's owner.

    The returned tuple contains only positioned diagnostics; bare (unpositioned) errors are dropped
    here and handled by the build parser, which must fail the build rather than file a finding at an
    unknown site.
    """
    found: list[Diagnostic] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or _SUMMARY.match(line.strip()):
            continue
        match = _DIAGNOSTIC.match(line.strip())
        if match is None:
            continue
        message = match.group("message").strip()
        severity = _SEVERITY_MAP.get(match.group("severity").lower(), "medium")
        found.append(
            Diagnostic(
                path=match.group("path").strip(),
                line=int(match.group("line")),
                column=int(match.group("column")) if match.group("column") else None,
                severity=severity,
                message=message,
                rule=normalize_rule(match.group("rule"), message),
                kind=kind,
            )
        )
    return tuple(found)


def checked_files(text: str) -> tuple[str, ...]:
    """Files cppcheck states it examined.

    cppcheck 2.10 prints one ``Checking <path>...`` line per input and, on input it cannot parse,
    prints *nothing* and still exits 0. A scan over uncompilable code is therefore indistinguishable from
    a clean one unless the progress lines are kept - which is why the frozen argument vector does not
    pass ``--quiet`` and why this function exists.
    """
    found: list[str] = []
    for raw in text.splitlines():
        match = re.match(r"^\s*Checking\s+(?P<path>\S+?)\s*\.{0,3}\s*$", raw)
        if match:
            found.append(match.group("path"))
    return tuple(found)


def bare_errors(text: str) -> tuple[str, ...]:
    """Errors with no file position: a failed translation unit, not a locatable finding."""
    messages: list[str] = []
    for raw in text.splitlines():
        match = _BARE.match(raw.strip())
        if match is not None and match.group("severity") in {"error", "fatal error"}:
            messages.append(match.group("message").strip()[:300])
    return tuple(messages)


def first_bare_error(text: str) -> str:
    errors = bare_errors(text)
    return errors[0] if errors else "the compiler reported an error"


def clang_tidy_rule(check_id: str) -> str | None:
    """``c.tidy.bugprone.use-after-move`` -> ``bugprone.use-after-move``; None for a scan marker."""
    parts = check_id.split(".")
    if len(parts) < 4 or parts[0] != "c" or parts[1] != "tidy":
        return None
    return ".".join(parts[2:4])


def cppcheck_rule(check_id: str) -> str | None:
    """``c.cppcheck.uninitvar`` -> ``uninitvar``; None for a scan marker."""
    parts = check_id.split(".")
    if len(parts) != 3 or parts[0] != "c" or parts[1] != "cppcheck":
        return None
    return parts[2]


def tidy_severity(rule: str | None) -> Severity:
    """Severity from the check family, not from the tool's exit status.

    A ``clang-analyzer`` finding is an executed static path; a ``portability`` finding is a design
    observation. Mapping them to the same severity would make a portability nit look like a memory
    bug.
    """
    if not rule:
        return "medium"
    family = rule.split(".")[0]
    return {
        "clang-analyzer": "high",
        "cert": "medium",
        "bugprone": "medium",
        "performance": "low",
        "portability": "low",
    }.get(family, "medium")


def cppcheck_severity(rule: str | None) -> Severity:
    if not rule:
        return "medium"
    return (
        "high"
        if rule
        in {
            "nullPointer",
            "arrayIndexOutOfBounds",
            "bufferAccessOutOfBounds",
            "useAfterFree",
            "doubleFree",
            "memleak",
            "resourceLeak",
            "uninitvar",
            "zerodiv",
            "integerOverflow",
            "invalidPrintfArgType_sint",
        }
        else "low"
    )