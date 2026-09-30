"""Run inspection: a read-only account of what a solve session did and what it cost.

Everything here is derived from committed events and the latest checkpoint, so it is exactly
what a recovery would see. Raw tool output stays in archived artifacts; only bounded metadata and
artifact references are reported.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any
from uuid import UUID

from polycodebench_persistence.solve_state import PostgresSolveRepository

from polycodebench_orchestration.gateway.service import ResponseStore


def inspect_attempt(
    repository: PostgresSolveRepository, store: ResponseStore, attempt_id: UUID
) -> dict[str, Any]:
    events = repository.events(attempt_id)
    payloads = [json.loads(store.get(event.payload_artifact_id)) for event in events]
    report: dict[str, Any] = {
        "attempt_id": str(attempt_id),
        "event_count": len(events),
        "events_by_kind": dict(Counter(event.kind for event in events)),
        "protocol": None,
        "turns": [],
        "tool_calls": [],
        "recoveries": [],
        "budget_exhausted": None,
        "terminal": None,
    }
    turn_rows: dict[int, dict[str, Any]] = {}
    for event, payload in zip(events, payloads, strict=True):
        if event.kind == "session_started":
            report["protocol"] = {
                key: payload[key]
                for key in (
                    "mode",
                    "protocol_digest",
                    "effective_protocol_digest",
                    "tools",
                    "base_digest",
                    "budget",
                )
            }
        elif event.kind == "model_turn":
            response = payload["response"]
            row = {
                "turn": payload["turn_index"],
                "event_seq": event.event_seq,
                "source": payload["source"],
                "finish_reason": response["finish_reason"],
                "tool_calls_requested": [
                    b["name"] for b in response["blocks"] if b["kind"] == "tool_call"
                ],
                "usage": payload["usage"],
                "usage_basis": payload["usage_basis"],
                "estimated_input_tokens": payload["request"]["estimated_input_tokens"],
                "reported_input_tokens": payload["usage"]["input_tokens"],
                "elapsed_ms": payload["elapsed_ms"],
                "context": {
                    k: payload["context"].get(k, [])
                    for k in ("retained_full", "compacted_turns", "dropped_turns")
                },
                "intent_id": payload["intent_id"],
            }
            turn_rows[payload["turn_index"]] = row
            report["turns"].append(row)
        elif event.kind == "tool_result":
            result = payload["result"]
            report["tool_calls"].append(
                {
                    "event_seq": event.event_seq,
                    "turn": payload["turn_index"],
                    "tool_call_id": payload["tool_call_id"],
                    "name": payload["name"],
                    "status": result["status"],
                    "error_code": result["error_code"],
                    "truncated": result["truncated"],
                    "original_bytes": result["original_bytes"],
                    "returned_bytes": result["returned_bytes"],
                    "elapsed_ms": result["elapsed_ms"],
                    "replayed": payload["replayed"],
                    "artifact_id": result["artifact_id"],
                    "removed_unsafe": payload["removed_unsafe"],
                    "protected_violations": payload["protected_violations"],
                }
            )
        elif event.kind == "recovery":
            report["recoveries"].append(
                {
                    "event_seq": event.event_seq,
                    "restored_from_seq": payload["restored_from_seq"],
                    "pending_call_ids": payload["pending_call_ids"],
                    "replay_candidate_call_ids": payload["replay_candidate_call_ids"],
                }
            )
        elif event.kind == "budget_exhausted":
            report["budget_exhausted"] = payload["dimension"]
        elif event.kind in {"candidate_frozen", "model_failure"}:
            report["terminal"] = {
                "event_seq": event.event_seq,
                "kind": event.kind,
                **payload["outcome"],
            }
    checkpoint = repository.latest_checkpoint(attempt_id)
    if checkpoint is not None:
        report["checkpoint"] = {
            "event_seq": checkpoint.event_seq,
            "protocol_digest": checkpoint.protocol_digest,
            "workspace_digest": checkpoint.workspace_digest,
            "transcript_digest": checkpoint.transcript_digest,
            "binding_digest": checkpoint.binding_digest,
            "pending_call_ids": checkpoint.pending_call_ids,
        }
        report["budget_consumed"] = checkpoint.accumulated_budget
    candidate = repository.candidate_for(attempt_id)
    if candidate is not None:
        report["candidate"] = {
            "payload_digest": candidate.payload_digest,
            "submission_kind": candidate.submission_kind,
            "validity": candidate.payload.get("validity"),
            "reasons": candidate.payload.get("reasons"),
            "files": candidate.payload.get("files"),
            "protocol_digest": candidate.payload.get("protocol_digest"),
            "artifact_id": str(candidate.canonical_artifact_id),
        }
    return report
