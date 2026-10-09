"""The audit CLI's dry-run and blocked states do not perform remote work."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from polycodebench_api import audit_cli
from polycodebench_core.benchmark_imports import BenchmarkImportPlan, freeze_sample
from polycodebench_core.canonical import canonical_digest, canonical_json_bytes


def _benchmark_import_files(tmp_path: Path, *, rights_state: str = "approved") -> tuple[Path, Path]:
    eligible_ids = tuple(f"test/{index}" for index in range(100))
    selected_ids, membership_digest = freeze_sample(
        benchmark_slug="humaneval",
        revision="6d43fb980f9fee3c892a914eda09951f772ad10d",
        split="all",
        variant="official",
        seed="7",
        eligible_item_ids=eligible_ids,
    )
    source = (
        "\n".join(
            json.dumps(
                {
                    "task_id": item_id,
                    "prompt": "Write a function.",
                    "entry_point": "solve",
                    "canonical_solution": "def solve(): return 1",
                    "test": "assert solve() == 1",
                },
                separators=(",", ":"),
            )
            for item_id in selected_ids
        )
        + "\n"
    ).encode("utf-8")
    source_digest = "sha256:" + hashlib.sha256(source).hexdigest()
    parser_config = {"format": "jsonl"}
    plan = BenchmarkImportPlan(
        benchmark_slug="humaneval",
        source_uri="https://github.com/openai/human-eval",
        revision="6d43fb980f9fee3c892a914eda09951f772ad10d",
        split="all",
        variant="official",
        source_member="data/HumanEval.jsonl",
        source_digest=source_digest,
        source_visibility="public",
        storage_visibility="private",
        rights_state=rights_state,  # type: ignore[arg-type]
        rights_evidence_digest="sha256:" + "b" * 64 if rights_state == "approved" else None,
        importer_version="benchmark-import-v1",
        parser_config=tuple(sorted(parser_config.items())),
        parser_config_digest=canonical_digest(parser_config),
        sample_seed="7",
        selected_ids=selected_ids,
        membership_digest=membership_digest,
    )
    plan_path = tmp_path / "benchmark-import-plan.json"
    source_path = tmp_path / "source.jsonl"
    plan_path.write_bytes(canonical_json_bytes(plan.model_dump(mode="json")))
    source_path.write_bytes(source)
    return plan_path, source_path


def test_registry_dry_run_is_local_and_has_a_real_read_route(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_request(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("dry-run must not make HTTP requests")

    monkeypatch.setattr(audit_cli, "_request", no_request)

    assert audit_cli.main(["audit", "registry", "list", "--dry-run"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["remote_work"] == "none"
    assert output["method"] == "GET"
    assert output["path"] == "/v1/benchmark-audit/registry"


def test_match_history_dry_run_uses_private_read_route_without_http(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_request(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("dry-run must not make HTTP requests")

    monkeypatch.setattr(audit_cli, "_request", no_request)
    candidate_id = "11111111-1111-4111-8111-111111111111"
    result = audit_cli.main(["audit", "matches", "history", candidate_id, "--dry-run"])
    output = json.loads(capsys.readouterr().out)
    assert result == 0
    assert output["command"] == "audit matches history"
    assert output["method"] == "GET"
    assert output["path"] == f"/v1/benchmark-audit/matches/{candidate_id}/reviews"
    assert output["remote_work"] == "none"


def test_match_adjudication_dry_run_validates_locally_and_uses_adjudication_route(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    def no_request(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("dry-run must not make HTTP requests")

    monkeypatch.setattr(audit_cli, "_request", no_request)
    decision = tmp_path / "adjudication.json"
    decision.write_text(
        '{"decision":"accepted","relation":"semantic_duplicate",'
        '"reason":"Independent adjudication resolves the conflicting reviews.",'
        '"evidence_refs":[{"document_id":"11111111-1111-4111-8111-111111111111",'
        '"digest":"sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",'
        '"kind":"corpus_snapshot"}],"decision_artifact_ref":'
        '{"artifact_id":"22222222-2222-4222-8222-222222222222",'
        '"digest":"sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",'
        '"visibility":"restricted","media_type":"application/json"}}',
        encoding="utf-8",
    )
    candidate_id = "33333333-3333-4333-8333-333333333333"

    result = audit_cli.main(
        [
            "audit",
            "matches",
            "adjudicate",
            candidate_id,
            "--decision",
            str(decision),
            "--dry-run",
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert result == 0
    assert output["command"] == "audit matches adjudicate"
    assert output["method"] == "POST"
    assert output["path"] == (f"/v1/benchmark-audit/matches/{candidate_id}/adjudications")
    assert output["input"]["validation_scope"] == "match_adjudicate_submission_schema"
    assert output["remote_work"] == "none"


def test_resource_plan_dry_run_is_local_and_respects_api_body_limit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    def no_request(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("dry-run must not make HTTP requests")

    monkeypatch.setattr(audit_cli, "_request", no_request)
    request = tmp_path / "resource-request.json"
    request.write_text(
        '{"task_counts":{"humaneval":1},"source_groups":["github"],'
        '"stages":["lexical"],"average_item_bytes":null}',
        encoding="utf-8",
    )
    assert audit_cli.main(["audit", "resource-plan", "--payload", str(request), "--dry-run"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["method"] == "POST"
    assert result["remote_work"] == "none"

    request.write_text("{}" + (" " * (64 * 1024)), encoding="utf-8")
    assert audit_cli.main(["audit", "resource-plan", "--payload", str(request), "--dry-run"]) == 2
    assert "64 KiB API request limit" in capsys.readouterr().err


def test_import_parses_frozen_local_snapshot_without_http_or_content_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    def no_request(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("local import parsing must not make HTTP requests")

    monkeypatch.setattr(audit_cli, "_request", no_request)
    plan_path, source_path = _benchmark_import_files(tmp_path)

    result = audit_cli.main(
        ["audit", "import", "--plan", str(plan_path), "--file", str(source_path)]
    )

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert result == 0
    assert payload["command"] == "audit import"
    assert payload["remote_work"] == "none"
    assert payload["validation_scope"] == "local_frozen_snapshot_parse"
    assert payload["state"] == "complete"
    assert payload["item_counts"] == {"imported": 100}
    assert payload["persisted"] is False
    assert payload["rights_review"] == "plan_claim_not_independently_verified"
    assert "canonical_solution" not in output
    assert "def solve()" not in output


def test_import_surfaces_rights_gate_and_source_size_limit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    plan_path, source_path = _benchmark_import_files(tmp_path, rights_state="needs_review")
    assert (
        audit_cli.main(["audit", "import", "--plan", str(plan_path), "--file", str(source_path)])
        == 3
    )
    blocked = json.loads(capsys.readouterr().out)
    assert blocked["state"] == "blocked"
    assert blocked["source_error_codes"] == ["rights_not_approved"]
    assert blocked["item_counts"] == {"blocked": 100}

    monkeypatch.setattr(audit_cli, "MAX_SOURCE_BYTES", 1)
    assert (
        audit_cli.main(["audit", "import", "--plan", str(plan_path), "--file", str(source_path)])
        == 2
    )
    assert "128 MiB parser limit" in capsys.readouterr().err


def test_unsupported_transition_is_blocked_without_http(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def no_request(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("uninstalled transitions must not make HTTP requests")

    monkeypatch.setattr(audit_cli, "_request", no_request)

    result = audit_cli.main(
        [
            "audit",
            "temporal",
            "assess",
            "11111111-1111-4111-8111-111111111111",
            "--model-context",
            "22222222-2222-4222-8222-222222222222",
        ]
    )
    assert result == 3
    assert "BLOCKED" in capsys.readouterr().err


def test_health_dry_run_does_not_claim_a_projection_adapter(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_request(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("health dry-run must remain local")

    monkeypatch.setattr(audit_cli, "_request", no_request)
    result = audit_cli.main(
        [
            "audit",
            "health",
            "11111111-1111-4111-8111-111111111111",
            "--context",
            "22222222-2222-4222-8222-222222222222",
            "--format",
            "json",
            "--dry-run",
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert result == 5
    assert output["command"] == "audit health"
    assert output["server_capability"] == "blocked"
    assert output["input"]["projection_available"] is False


def test_cli_rejects_remote_http_and_redirects_are_disabled() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        audit_cli._safe_base_url("http://example.com")
    with pytest.raises(ValueError, match="absolute"):
        audit_cli._safe_base_url("https://user:password@example.com")
    with pytest.raises(ValueError, match="absolute"):
        audit_cli._safe_base_url("https://:password@example.com")
    with pytest.raises(ValueError, match="path"):
        audit_cli._safe_base_url("https://api.example.com/proxy")
    with pytest.raises(ValueError, match="invalid port"):
        audit_cli._safe_base_url("https://api.example.com:not-a-port")

    handler = audit_cli._NoRedirectHandler()
    from urllib.request import Request

    assert (
        handler.redirect_request(
            Request("https://api.example.test"), None, 302, "Found", {}, "https://evil.test"
        )
        is None
    )
