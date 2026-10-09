"""One-off GLM 5.3 Flash task-drafting batch with a hard spend cap.

This is NOT routed through the model gateway, which only accepts solve and evaluation scopes. It is
an owner-authorised, one-off batch for screened task drafting. Calls go directly to Zhipu's
international endpoint, the same host the Data Processing Addendum for API Services covers.

Safety properties:
- The API key is read from ZHIPU_API_KEY in the environment and is never written to disk or logs.
- Before each call the worst-case cost (all input bytes as tokens plus the full output cap) must fit
  in the remaining budget, otherwise the batch stops. Spend is then settled from the usage reported.
- Raw replies, drafts, exposure events and the run manifest are written under the --out directory,
  which must be inside .protected/ so that drafts stay hidden until reviewed.
- Drafts are screened, never admitted. Nothing here executes candidate code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from polycodebench_taskgen import (
    ExposureEvent,
    FamilySpec,
    build_generation_request,
    parse_generator_output,
)
from polycodebench_taskgen.generation import dumps_candidates

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "https://api.z.ai/api/paas/v4/chat/completions"
MODEL = "glm-5.3-flash"
GENERATOR_ID = "glm-5.3-flash"
RECIPIENT_ID = "provider:zhipu/glm-5.3-flash"
# Official list prices from https://docs.z.ai/guides/overview/pricing (read 2026-10-08), USD.
INPUT_MICRO_USD_PER_MILLION = 150_000
OUTPUT_MICRO_USD_PER_MILLION = 500_000
RETENTION_REFERENCE = (
    "Zhipu Data Processing Addendum for API Services, linked from "
    "https://docs.z.ai/legal-agreement/privacy-policy (read 2026-10-08). Owner accepted this "
    "basis on 2026-10-08."
)


def _micro_usd(prompt_tokens: int, completion_tokens: int) -> int:
    """Round up to whole micro-USD so the cap is never under-counted."""
    numerator = (
        prompt_tokens * INPUT_MICRO_USD_PER_MILLION
        + completion_tokens * OUTPUT_MICRO_USD_PER_MILLION
    )
    return -(-numerator // 1_000_000)


def _post(api_key: str, prompt: str, max_tokens: int, temperature: float) -> dict[str, object] | None:
    """Return the parsed reply, or None when the request failed or timed out (spend uncertain)."""
    body = json.dumps(
        {
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        ENDPOINT,
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        # Reasoning-heavy replies at 64k output tokens need more than 15 minutes on this endpoint.
        with urllib.request.urlopen(request, timeout=1800) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        # Print the error class only; messages and bodies can reflect request data or headers.
        print(f"request failed with {type(error).__name__}; spend is treated as uncertain")
        return None
    return payload if isinstance(payload, dict) else None


def main() -> int:
    parser = argparse.ArgumentParser(prog="glm_generation_batch")
    parser.add_argument("--family", required=True, type=Path)
    parser.add_argument("--calls", type=int, default=1)
    parser.add_argument("--items-per-call", type=int, default=3)
    parser.add_argument("--max-tokens", type=int, default=16000)
    parser.add_argument("--temperature", type=float, default=0.8)
    parser.add_argument("--budget-micro-usd", type=int, default=2_000_000)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    out = args.out.resolve()
    if ROOT / ".protected" not in out.parents and out != ROOT / ".protected":
        raise SystemExit("--out must be inside .protected/ so drafts stay hidden")
    api_key = os.environ.get("ZHIPU_API_KEY", "")
    if not api_key:
        raise SystemExit("set ZHIPU_API_KEY in the environment for this command")
    if not 1 <= args.calls <= 50 or not 1 <= args.items_per_call <= 10:
        raise SystemExit("calls must be 1-50 and items per call 1-10")
    out.mkdir(parents=True, exist_ok=True)
    spec = FamilySpec.model_validate_json(args.family.read_bytes())
    prompt = build_generation_request(spec, count=args.items_per_call)
    prompt_bytes = len(prompt.encode("utf-8"))
    worst_case = _micro_usd(prompt_bytes, args.max_tokens)

    spent = 0
    candidates = []
    events: list[dict[str, object]] = []
    calls_log: list[dict[str, object]] = []
    for index in range(1, args.calls + 1):
        if spent + worst_case > args.budget_micro_usd:
            calls_log.append({"call": index, "stopped": "worst-case cost would exceed budget"})
            break
        occurred_at = datetime.now(UTC)
        payload = _post(api_key, prompt, args.max_tokens, args.temperature)
        if payload is None:
            # A failed or timed-out request may still have been processed, so the worst case is
            # charged and the batch stops. The exposure is recorded as uncertain, not as zero.
            spent += worst_case
            calls_log.append(
                {"call": index, "result": "uncertain_request_failed", "spent_micro_usd": spent}
            )
            break
        usage = payload.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        prompt_tokens = int(usage.get("prompt_tokens", prompt_bytes))
        completion_tokens = int(usage.get("completion_tokens", args.max_tokens))
        cost = _micro_usd(prompt_tokens, completion_tokens)
        spent += cost
        raw_bytes = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        digest = "sha256:" + hashlib.sha256(raw_bytes).hexdigest()
        (out / f"call-{index:03d}.json").write_bytes(raw_bytes)
        choices = payload.get("choices") or []
        content = ""
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message") or {}
            if isinstance(message, dict):
                content = str(message.get("content") or "")
        entry: dict[str, object] = {
            "call": index,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_micro_usd": cost,
            "spent_micro_usd": spent,
            "raw_digest": digest,
        }
        if not content.strip():
            entry["result"] = "empty_content"
        else:
            try:
                parsed = parse_generator_output(
                    content, spec=spec, generator_id=GENERATOR_ID, generator_is_external=True
                )
            except ValueError as error:
                entry["result"] = f"rejected_reply: {str(error)[:120]}"
            else:
                entry["result"] = f"{len(parsed)} drafts"
                candidates.extend(parsed)
                for item in parsed:
                    events.append(
                        ExposureEvent(
                            task_id=item.candidate_id,
                            audience="generator_model",
                            recipient_id=RECIPIENT_ID,
                            occurred_at=occurred_at,
                            evidence_digest=digest,
                        ).model_dump(mode="json")
                    )
        calls_log.append(entry)
        time.sleep(1)

    (out / "candidates.json").write_text(dumps_candidates(tuple(candidates)) + "\n", encoding="utf-8")
    (out / "exposure-events.json").write_text(json.dumps(events, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "model": MODEL,
        "generator_id": GENERATOR_ID,
        "endpoint_host": "api.z.ai",
        "retention_reference": RETENTION_REFERENCE,
        "price_micro_usd_per_million": {
            "input": INPUT_MICRO_USD_PER_MILLION,
            "output": OUTPUT_MICRO_USD_PER_MILLION,
        },
        "budget_micro_usd": args.budget_micro_usd,
        "spent_micro_usd": spent,
        "candidates": len(candidates),
        "calls": calls_log,
    }
    (out / "run-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"spent_micro_usd": spent, "candidates": len(candidates)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
