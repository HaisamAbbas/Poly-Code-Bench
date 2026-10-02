"""``pcb-judge`` operator surface: what a judge sees, and what is refused without real inputs.

EVIDENCE LABEL: every command here is offline and deterministic: the frozen rubric, panel and
calibration policy from ``config/judging``, plus fixture judge responses. No database, no model
call, no human label. The stored commands (``run``, ``result``, ``show``, ``adjudicate``) are
exercised against real PostgreSQL and SeaweedFS in ``tests/test_judging_postgres.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from judging_support import (
    INJECTION_COMMENT,
    JUDGE_CONFIG_ID,
    SOLUTION,
    InputComment,
    code_span,
    packet_input,
)
from polycodebench_orchestration.judge.cli import (
    EXIT_BLOCKED,
    EXIT_OK,
    EXIT_PERMISSION,
    EXIT_VALIDATION,
    main,
)

ROOT = Path(__file__).resolve().parents[1]
RUBRIC = ROOT / "config" / "judging" / "rubric-v1.yaml"
PANEL = ROOT / "config" / "judging" / "panel-v1.yaml"
CALIBRATION = ROOT / "config" / "judging" / "calibration-v1.yaml"


def run_cli(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *args: str
) -> tuple[int, Any]:
    monkeypatch.setattr("sys.argv", ["pcb-judge", *args])
    code = main()
    captured = capsys.readouterr()
    payload = None
    if captured.out.strip():
        payload = json.loads(captured.out)
    return code, payload


def write(tmp_path: Path, name: str, document: Any) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_rubric_command_lists_the_frozen_items(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    code, payload = run_cli(monkeypatch, capsys, "rubric", "--rubric", str(RUBRIC))
    assert code == EXIT_OK
    assert payload is not None
    assert payload["rubric_id"] == "judge-rubric-v1"
    assert payload["digest"].startswith("sha256:")
    item_ids = {item["item_id"] for item in payload["items"]}
    assert {"decomposition", "minimal_relevant_scope", "idiomatic_design"} <= item_ids
    assert all(item["anchors"] == ["0.000000", "0.500000", "1.000000"] for item in payload["items"])


def test_panel_command_reports_the_missing_judge_access_as_blocked(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    code, payload = run_cli(
        monkeypatch, capsys, "panel", "--panel", str(PANEL), "--rubric", str(RUBRIC)
    )
    assert code == EXIT_BLOCKED
    assert payload is not None
    assert payload["ready"] is False
    assert payload["provisioned"] is False
    assert payload["blocker"] == "JUDGE_PANEL_UNAVAILABLE"
    assert payload["votes_required"] == 3
    assert payload["tools"] == []
    assert payload["blinded_fields"] == [
        "candidate_identity",
        "provider",
        "rank",
        "cost",
        "expected_score",
    ]
    assert payload["effective_for_scoring"] is False
    assert payload["calibration_status"] == "pending"


def test_packet_command_writes_a_canonical_packet_with_untrusted_comments(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    document = packet_input(
        comments=(InputComment(path="solution.py", text=INJECTION_COMMENT),)
    ).model_dump(mode="json")
    source = write(tmp_path, "packet_input.json", document)
    out = tmp_path / "packet.json"
    code, payload = run_cli(
        monkeypatch,
        capsys,
        "packet",
        "--input",
        str(source),
        "--rubric",
        str(RUBRIC),
        "--panel",
        str(PANEL),
        "--out",
        str(out),
    )
    assert code == EXIT_OK
    assert payload is not None
    assert payload["tools"] == []
    assert payload["packet_role"] == "scored"
    assert payload["untrusted_comments"] and payload["untrusted_comments"][0]["instruction_attempt"]
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["kind"] == "judge_packet"
    assert written["payload"]["packet_id"] == payload["packet_id"]
    assert "expected_score" not in json.dumps(written["payload"]["items"])


def test_validate_vote_accepts_a_fixture_vote_and_rejects_a_false_citation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    document = packet_input().model_dump(mode="json")
    source = write(tmp_path, "packet_input.json", document)
    packet_path = tmp_path / "packet.json"
    run_cli(
        monkeypatch,
        capsys,
        "packet",
        "--input",
        str(source),
        "--rubric",
        str(RUBRIC),
        "--panel",
        str(PANEL),
        "--out",
        str(packet_path),
    )
    packet = json.loads(packet_path.read_text(encoding="utf-8"))["payload"]
    good = write(tmp_path, "good.json", vote_document_from(packet))
    code, payload = run_cli(
        monkeypatch, capsys, "validate-vote", "--packet", str(packet_path), "--response", str(good)
    )
    assert code == EXIT_OK
    assert payload is not None and payload["accepted"] is True

    bad = write(
        tmp_path,
        "bad.json",
        vote_document_from(
            packet,
            anchors={
                "decomposition": "sp-00000000000000ff",
                "minimal_relevant_scope": "sp-00000000000000ff",
            },
        ),
    )
    code, payload = run_cli(
        monkeypatch, capsys, "validate-vote", "--packet", str(packet_path), "--response", str(bad)
    )
    assert code == EXIT_VALIDATION
    assert payload is not None
    assert payload["accepted"] is False
    assert payload["reason"] == "citation_not_in_packet"


def test_calibration_command_writes_a_blocked_report_without_human_labels(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    packets = [
        {
            "packet_id": "11111111-1111-4111-8111-111111111111",
            "packet_digest": "sha256:" + f"{index:064x}",
            "language": "python",
            "strata": ["representative", "adversarial_comment", "stylistic_alternative"],
            "instruction_attempt_count": 1,
        }
        for index in range(30)
    ]
    packets_path = write(tmp_path, "packets.json", packets)
    report_path = tmp_path / "calibration.json"
    code, payload = run_cli(
        monkeypatch,
        capsys,
        "calibration",
        "--policy",
        str(CALIBRATION),
        "--rubric",
        str(RUBRIC),
        "--panel",
        str(PANEL),
        "--packets",
        str(packets_path),
        "--report",
        str(report_path),
    )
    assert code == EXIT_BLOCKED
    assert payload is not None
    assert payload["status"] == "blocked"
    assert payload["exact_agreement_bp"] is None
    assert payload["promotion_target_met"] is None
    assert "no qualified polycodebench_reviewer_v1 human label file" in payload["missing_inputs"][0]
    assert report_path.is_file()
    stored = json.loads(report_path.read_text(encoding="utf-8"))
    assert stored["payload"]["status"] == "blocked"
    assert stored["kind"] == "calibration_report"


def test_calibration_command_refuses_unqualified_labels(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    packets = [
        {
            "packet_id": "11111111-1111-4111-8111-111111111111",
            "packet_digest": "sha256:" + f"{index:064x}",
            "language": "python",
            "strata": ["representative", "adversarial_comment", "stylistic_alternative"],
        }
        for index in range(30)
    ]
    packets_path = write(tmp_path, "packets.json", packets)
    labels_path = write(
        tmp_path,
        "labels.json",
        [
            {
                "packet_id": packets[0]["packet_id"],
                "packet_digest": packets[0]["packet_digest"],
                "item_id": "decomposition",
                "score": "1.000000",
                "labeler_subject": "someone",
                "qualification": "self_reported",
                "rationale": "A reviewer read the span and applied the anchors.",
                "cited_anchor_ids": ["sp-0000000000000000"],
                "labeled_at": "2026-10-02T00:00:00.000000Z",
            }
        ],
    )
    code, _ = run_cli(
        monkeypatch,
        capsys,
        "calibration",
        "--policy",
        str(CALIBRATION),
        "--rubric",
        str(RUBRIC),
        "--panel",
        str(PANEL),
        "--packets",
        str(packets_path),
        "--labels",
        str(labels_path),
    )
    assert code == EXIT_VALIDATION


def test_stored_commands_refuse_to_start_without_database_configuration(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PCB_DATABASE_URL", raising=False)
    monkeypatch.delenv("PCB_OBJECT_STORE_ENDPOINT", raising=False)
    code, _ = run_cli(
        monkeypatch,
        capsys,
        "review-queue",
    )
    assert code == EXIT_VALIDATION


def test_result_requires_the_restricted_evidence_permission(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from uuid import uuid4

    monkeypatch.setenv("PCB_DATABASE_URL", "postgresql://unused/unused")
    monkeypatch.setenv("PCB_OBJECT_STORE_ENDPOINT", "http://127.0.0.1:1")
    monkeypatch.setenv("PCB_SERVICE_IDENTITY", "someone")
    monkeypatch.setenv("PCB_ROLES", "submitter")
    code, _ = run_cli(monkeypatch, capsys, "result", str(uuid4()))
    assert code == EXIT_PERMISSION


def test_review_commands_require_the_adjudication_permission(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Permission is enforced in the command, not only in the database role grants."""
    from uuid import uuid4

    monkeypatch.setenv("PCB_DATABASE_URL", "postgresql://unused/unused")
    monkeypatch.setenv("PCB_OBJECT_STORE_ENDPOINT", "http://127.0.0.1:1")
    monkeypatch.setenv("PCB_SERVICE_IDENTITY", "someone")
    monkeypatch.setenv("PCB_ROLES", "submitter")
    code, _ = run_cli(monkeypatch, capsys, "show", str(uuid4()))
    assert code == EXIT_PERMISSION


def vote_document_from(
    packet: dict[str, Any], anchors: dict[str, str] | None = None
) -> dict[str, Any]:
    """Rebuild a valid vote document for a packet the CLI wrote to disk."""
    anchors = anchors or {}
    items = {
        item["item_id"]: {
            "score": "1.000000",
            "citations": [
                {"anchor_id": anchors.get(item["item_id"], packet["spans"][0]["anchor_id"])}
            ],
            "rationale": "The cited span shows the behaviour the item asks for.",
        }
        for item in packet["items"]
    }
    return {"packet_digest": _packet_digest(packet), "items": items}


def _packet_digest(packet: dict[str, Any]) -> str:
    from polycodebench_core.canonical import canonical_document_bytes, sha256_bytes
    from polycodebench_core.judge_contracts import JudgePacket

    document = JudgePacket.model_validate(packet, strict=False)
    return sha256_bytes(canonical_document_bytes(document))


def test_judge_configuration_id_is_a_fixture_not_a_registered_endpoint() -> None:
    """The frozen panel names no judge configuration; that is the recorded blocker."""
    from polycodebench_services.judging import load_panel

    panel = load_panel(PANEL)
    assert panel.judge_model_config_id is None
    assert panel.judge_revision == "unprovisioned"
    assert panel.provisioned is False
    assert JUDGE_CONFIG_ID not in PANEL.read_text(encoding="utf-8")
    assert SOLUTION.splitlines()[0] in code_span().text
