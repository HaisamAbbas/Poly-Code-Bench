"""Read-only structural and evidence-link audit for the benchmark audit pack."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPORT_SECTIONS = (
    "Implemented functionality and changed files",
    "Tests/commands actually run and results",
    "Acceptance gates satisfied, pending and blocked",
    "Decisions or specification discrepancies recorded",
    "Exact next command or numbered prompt",
)
SOURCE_COUNT_WORDS = {"five": 5, "5": 5}
STATUS_VALUES = {"complete", "partial", "blocked", "pending"}


def expected_identifiers(kind: str) -> set[str]:
    """Return the closed identifier set required by the published audit spec."""
    if kind == "BREQ":
        return {f"BREQ-{number:02}" for number in range(1, 33)}
    if kind == "BWP":
        return {f"BWP-{number:02}" for number in range(1, 25)}
    if kind == "BAT":
        return {f"BAT-{number:02}-{suffix}" for number in range(1, 25) for suffix in "ABCD"}
    if kind == "BX":
        return {f"BX-{number:02}" for number in range(1, 61)}
    if kind == "BA":
        return {f"BA{number}" for number in range(8)}
    raise ValueError(f"unknown traceability identifier family: {kind}")


def rows_in_section(markdown: str, heading: str) -> list[list[str]]:
    """Read pipe-table rows belonging to a second-level Markdown section."""
    lines = markdown.splitlines()
    start = next((index for index, line in enumerate(lines) if line.strip() == heading), None)
    if start is None:
        return []

    rows: list[list[str]] = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and not all(set(cell) <= {"-", ":", " "} for cell in cells):
            rows.append(cells)
    return rows


def validate_identifier_rows(rows: list[list[str]], expected: set[str], family: str) -> list[str]:
    """Report duplicate, missing and unexpected first-column identifiers."""
    found = [row[0] for row in rows if row and row[0] in expected | {"ID"}]
    identifiers = [identifier for identifier in found if identifier != "ID"]
    counts = Counter(identifiers)
    errors: list[str] = []
    duplicates = sorted(identifier for identifier, count in counts.items() if count > 1)
    missing = sorted(expected - counts.keys())
    identifier_pattern = re.compile(r"(?:BREQ-\d{2}|BWP-\d{2}|BAT-\d{2}-[A-D]|BX-\d{2}|BA[0-7])")
    unexpected = sorted(
        row[0]
        for row in rows
        if row and identifier_pattern.fullmatch(row[0]) and row[0] not in expected
    )
    if duplicates:
        errors.append(f"{family}: duplicate identifiers: {', '.join(duplicates)}")
    if missing:
        errors.append(f"{family}: missing identifiers: {', '.join(missing)}")
    if unexpected:
        errors.append(f"{family}: unexpected identifiers: {', '.join(unexpected)}")
    return errors


def _phase_path(audit_dir: Path, number: int) -> Path:
    base = audit_dir if number < 4 else audit_dir / "reports"
    return base / f"phase-BA{number}.md"


def _section_checks(path: Path, label: str, errors: list[str]) -> None:
    if not path.is_file():
        errors.append(f"missing {label}: {path.as_posix()}")
        return
    content = path.read_text(encoding="utf-8")
    positions: list[int] = []
    for section in REPORT_SECTIONS:
        headings = list(re.finditer(rf"(?m)^## {re.escape(section)}\s*$", content))
        if len(headings) != 1:
            errors.append(f"{label} must contain exactly one '## {section}' heading")
        if headings:
            positions.append(headings[0].start())
    if len(positions) == len(REPORT_SECTIONS) and positions != sorted(positions):
        errors.append(f"{label} report fields are out of the required order")


def _check_statuses(
    rows: list[list[str]], family: str, expected: set[str], status_column: int
) -> list[str]:
    errors: list[str] = []
    for row in rows:
        if not row or row[0] not in expected:
            continue
        if len(row) <= status_column:
            errors.append(f"{family} {row[0]} has no status column")
        elif row[status_column].lower() not in STATUS_VALUES:
            errors.append(f"{family} {row[0]} has invalid status {row[status_column]!r}")
    return errors


def _source_bridge(
    spec_path: Path, ledger_path: Path, repo_root: Path
) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    missing = [path for path in (spec_path, ledger_path) if not path.is_file()]
    if missing:
        return {"declared": None, "listed": 0, "verified": 0, "mismatches": []}, [
            f"missing source-bridge input: {path.as_posix()}" for path in missing
        ]
    spec = spec_path.read_text(encoding="utf-8")
    ledger = ledger_path.read_text(encoding="utf-8")
    section_start = spec.find("### 1.1 Exact source bridge")
    if section_start < 0:
        return {"declared": None, "listed": 0, "verified": 0, "mismatches": []}, [
            "specification is missing section 1.1 Exact source bridge"
        ]
    section_end = spec.find("\n### ", section_start + 1)
    bridge = spec[section_start:] if section_end < 0 else spec[section_start:section_end]
    count_match = re.search(r"These\s+(\w+) current documents", bridge, flags=re.IGNORECASE)
    declared = SOURCE_COUNT_WORDS.get(count_match.group(1).lower()) if count_match else None
    if declared is None:
        errors.append("source bridge does not declare a recognized exact document count")

    source_rows: list[tuple[str, str]] = []
    for line in bridge.splitlines():
        match = re.match(r"\|\s*([^|]+\.md)\s*\|\s*`?([0-9a-f]{64})`?\s*\|", line)
        if match:
            source_rows.append((match.group(1).strip(), match.group(2)))

    mismatches: list[dict[str, str]] = []
    verified = 0
    for name, expected_digest in source_rows:
        path = repo_root / name
        if not path.is_file():
            mismatches.append({"source": name, "reason": "listed source file is absent"})
            continue
        actual_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_digest != expected_digest:
            mismatches.append(
                {"source": name, "reason": "SHA-256 differs from the specification pin"}
            )
            continue
        if f"`{name}`" not in ledger or expected_digest not in ledger:
            mismatches.append(
                {
                    "source": name,
                    "reason": "exact-byte identity is not retained in the implementation ledger",
                }
            )
            continue
        verified += 1

    if len(source_rows) != verified + len(mismatches):
        errors.append("source bridge parser did not account for every pinned source row")
    if declared is not None and len(source_rows) > declared:
        errors.append(
            f"source bridge lists {len(source_rows)} documents, more than its declared {declared}"
        )
    errors.extend(f"source bridge {item['source']}: {item['reason']}" for item in mismatches)
    summary = {
        "declared": declared,
        "listed": len(source_rows),
        "verified": verified,
        "mismatches": mismatches,
    }
    return summary, errors


def _status_summary(
    rows: list[list[str]], expected: set[str], status_column: int
) -> dict[str, int]:
    return dict(
        sorted(
            Counter(
                row[status_column].lower()
                for row in rows
                if row and row[0] in expected and len(row) > status_column
            ).items()
        )
    )


def _validate_evidence_refs(
    rows: list[list[str]],
    family: str,
    audit_dir: Path,
    errors: list[str],
) -> None:
    report_pattern = re.compile(r"(?:reports/)?(?:prompt-\d{2,3}|phase-BA[0-7])\.md")
    for row in rows:
        if not row or row[0] not in expected_identifiers(family):
            continue
        evidence = row[-1]
        references = report_pattern.findall(evidence)
        if not references:
            errors.append(f"{family} {row[0]} has no prompt/phase report evidence link")
            continue
        for reference in references:
            name = Path(reference).name
            prompt_match = re.fullmatch(r"prompt-(\d{2,3})\.md", name)
            phase_match = re.fullmatch(r"phase-BA([0-7])\.md", name)
            if prompt_match:
                target = audit_dir / "reports" / name
            elif phase_match:
                target = _phase_path(audit_dir, int(phase_match.group(1)))
            elif reference.startswith("reports/"):
                target = audit_dir / reference
            else:
                target = audit_dir / name
            if not target.is_file():
                errors.append(f"{family} {row[0]} references missing report {reference}")


def _prompt_numbers(value: str) -> set[int]:
    numbers: set[int] = set()
    ranges = list(re.finditer(r"(\d{2,3})\s*[–—-]\s*(\d{2,3})", value))
    for match in ranges:
        start, end = (int(part) for part in match.groups())
        numbers.update(range(start, end + 1))
    without_ranges = re.sub(r"\d{2,3}\s*[–—-]\s*\d{2,3}", "", value)
    numbers.update(int(number) for number in re.findall(r"\d{2,3}", without_ranges))
    return numbers


def audit_repository(repo_root: Path) -> dict[str, Any]:
    """Audit repository traceability without writing files or using the network."""
    repo_root = repo_root.resolve()
    audit_dir = repo_root / "docs" / "benchmark-audit"
    errors: list[str] = []
    blockers: list[str] = []
    acceptance_path = audit_dir / "acceptance.md"
    if not acceptance_path.is_file():
        return {
            "status": "blocked",
            "read_only": True,
            "structural_errors": [f"missing acceptance ledger: {acceptance_path.as_posix()}"],
            "blockers": [],
        }
    acceptance = acceptance_path.read_text(encoding="utf-8")
    sections = {
        "BREQ": ("## Requirements (BREQ)", 3),
        "BWP": ("## Work packages (BWP)", 2),
        "BAT": ("## Engineering tickets (BAT)", 2),
        "BX": ("## End-to-end gates (BX)", 2),
        "BA": ("## Phase ownership", 2),
    }
    parsed_rows: dict[str, list[list[str]]] = {}
    for family, (heading, status_column) in sections.items():
        rows = rows_in_section(acceptance, heading)
        parsed_rows[family] = rows
        expected = expected_identifiers(family)
        errors.extend(validate_identifier_rows(rows, expected, family))
        errors.extend(_check_statuses(rows, family, expected, status_column))
        _validate_evidence_refs(rows, family, audit_dir, errors)
        for row in rows:
            if not row or row[0] == "ID":
                continue
            evidence = row[-1].strip().lower()
            if not evidence or evidence in {"prompt report", "todo", "tbd", "pending"}:
                errors.append(f"{family} {row[0]} has placeholder or empty evidence")
            if "remaining prompts pending" in evidence:
                errors.append(f"{family} {row[0]} has stale remaining-prompts evidence")

    for row in parsed_rows["BREQ"]:
        if len(row) >= 2 and row[0] in expected_identifiers("BREQ"):
            prompt_numbers = _prompt_numbers(row[1])
            if any(not 83 <= number <= 106 for number in prompt_numbers):
                errors.append(f"{row[0]} references a prompt outside 83–106: {row[1]!r}")
            evidence_prompts = _prompt_numbers(" ".join(re.findall(r"prompt-\d{2,3}\.md", row[-1])))
            if not (prompt_numbers & evidence_prompts):
                errors.append(f"{row[0]} evidence does not cite one of its owning prompt reports")

    for family in ("BWP", "BAT"):
        for row in parsed_rows[family]:
            if not row or row[0] not in expected_identifiers(family):
                continue
            expected_prompt = int(row[0][4:6]) + 82
            if row[1] != str(expected_prompt):
                errors.append(f"{row[0]} should map to prompt {expected_prompt}, found {row[1]!r}")
            evidence_prompts = _prompt_numbers(" ".join(re.findall(r"prompt-\d{2,3}\.md", row[-1])))
            if expected_prompt not in evidence_prompts:
                errors.append(
                    f"{row[0]} evidence must cite its owner prompt {expected_prompt} report"
                )

    for row in parsed_rows["BX"]:
        if not row or row[0] not in expected_identifiers("BX"):
            continue
        owner_prompts = _prompt_numbers(row[1])
        evidence_prompts = _prompt_numbers(" ".join(re.findall(r"prompt-\d{2,3}\.md", row[-1])))
        if not (owner_prompts & evidence_prompts):
            errors.append(f"{row[0]} evidence does not cite one of its owning prompt reports")

    for row in parsed_rows["BA"]:
        phase_match = re.fullmatch(r"BA([0-7])", row[0]) if row else None
        if not phase_match:
            continue
        number = int(phase_match.group(1))
        expected_prompt_range = [83 + 3 * number, 85 + 3 * number]
        mapped_prompts = [int(value) for value in re.findall(r"\d+", row[1])]
        if mapped_prompts != expected_prompt_range:
            errors.append(
                f"BA{number} should map to prompts "
                f"{expected_prompt_range[0]}–{expected_prompt_range[1]}, "
                f"found {row[1]!r}"
            )
        report_path = _phase_path(audit_dir, number)
        _section_checks(report_path, f"phase BA{number} report", errors)
        report_cell = row[3] if len(row) > 3 else ""
        relative = report_path.relative_to(audit_dir).as_posix()
        if relative not in report_cell.replace("`", ""):
            errors.append(f"BA{number} ownership row must map to {relative}")

    prompt_report_paths = [
        audit_dir / "reports" / f"prompt-{prompt}.md" for prompt in range(83, 107)
    ]
    for prompt, report_path in zip(range(83, 107), prompt_report_paths, strict=True):
        _section_checks(report_path, f"prompt {prompt} report", errors)
    phase_report_paths = [_phase_path(audit_dir, number) for number in range(8)]

    source_summary, source_errors = _source_bridge(
        repo_root
        / "PolyCodeBench-Benchmark-Audit-and-Contamination-Firewall-Implementation-Spec-v1.md",
        audit_dir / "implementation-ledger.md",
        repo_root,
    )
    errors.extend(source_errors)
    declared = source_summary["declared"]
    listed = source_summary["listed"]
    if isinstance(declared, int) and declared > listed:
        missing_count = declared - listed
        blockers.append(
            f"Historical-source owner must supply {missing_count} source MD(s): the specification "
            f"says {declared}, but lists only {listed}; the listed "
            f"{source_summary['verified']} exact-byte sources verify."
        )

    status_columns = {"BREQ": 3, "BWP": 2, "BAT": 2, "BX": 2, "BA": 2}
    status_counts = {
        family: _status_summary(parsed_rows[family], expected_identifiers(family), column)
        for family, column in status_columns.items()
    }
    incomplete_packages = [
        f"{row[0]}:{row[2]}"
        for row in parsed_rows["BWP"]
        if len(row) > 2 and row[0] in expected_identifiers("BWP") and row[2].lower() != "complete"
    ]
    if incomplete_packages:
        blockers.append(
            "Capability work packages remain partial or blocked pending the per-row live, source, "
            "rights, "
            "reviewer, database, calibration, crypto, runtime and modality evidence: "
            + ", ".join(incomplete_packages)
            + "."
        )

    complete = (
        not errors
        and not blockers
        and all(
            counts.get("complete", 0) == expected
            for family, counts in status_counts.items()
            for expected in [len(expected_identifiers(family))]
        )
    )
    return {
        "status": "complete" if complete else "partial" if not errors else "structural_error",
        "read_only": True,
        "counts": {
            family: sum(
                row and row[0] in expected_identifiers(family) for row in parsed_rows[family]
            )
            for family in sections
        },
        "prompt_reports": sum(path.is_file() for path in prompt_report_paths),
        "phase_reports": sum(path.is_file() for path in phase_report_paths),
        "status_counts": status_counts,
        "source_bridge": source_summary,
        "structural_errors": errors,
        "blockers": blockers,
    }


def main() -> int:
    report = audit_repository(Path(__file__).resolve().parents[1])
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report["structural_errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
