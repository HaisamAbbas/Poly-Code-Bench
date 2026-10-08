from pathlib import Path

from scripts.benchmark_audit_traceability import (
    _prompt_numbers,
    audit_repository,
    validate_identifier_rows,
)


def test_repository_traceability_matches_ledgers_and_retains_blockers() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    report = audit_repository(repo_root)

    assert report["status"] == "partial"
    assert report["structural_errors"] == []
    assert report["counts"] == {"BREQ": 32, "BWP": 24, "BAT": 96, "BX": 60, "BA": 8}
    assert report["prompt_reports"] == 24
    assert report["phase_reports"] == 8
    assert report["source_bridge"] == {
        "declared": 5,
        "listed": 3,
        "verified": 3,
        "mismatches": [],
    }
    assert any("supply 2 source MD(s)" in blocker for blocker in report["blockers"])
    assert any("BWP-21:partial" in blocker for blocker in report["blockers"])


def test_identifier_validator_reports_duplicate_and_missing_rows() -> None:
    errors = validate_identifier_rows([["BREQ-01"], ["BREQ-01"]], {"BREQ-01", "BREQ-02"}, "BREQ")

    assert errors == [
        "BREQ: duplicate identifiers: BREQ-01",
        "BREQ: missing identifiers: BREQ-02",
    ]


def test_prompt_owner_ranges_expand_for_evidence_matching() -> None:
    assert _prompt_numbers("83, 104–106") == {83, 104, 105, 106}
