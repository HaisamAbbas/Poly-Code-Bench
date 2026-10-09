"""Draft Python task packages with GLM 5.3 Flash, screen them, then run the existing pipeline.

One-off, owner-authorised batch. It reuses the call, cost and spend-guard code in
glm_generation_batch.py and is not routed through the model gateway (see that file's docstring).

Flow per run:
1. Ask the model for one package draft per call: statement, starter, public and hidden tests,
   reference solution, and four fixtures (faulty, alternative, quality_defective, timeout).
2. Parse each reply strictly. Fill metadata and provenance here, never from the model.
3. Screen all drafts as one batch with the taskgen gates. Only "ready" drafts continue.
4. Hand each ready draft to scripts/generated_task_package.py, which seals, validates and
   admits it in the local sandbox. Admission reports go under the run directory.

Nothing here publishes a task or changes a score. Drafts live under .protected/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

import generated_task_package as packager  # noqa: E402
from glm_generation_batch import (  # noqa: E402
    GENERATOR_ID,
    RECIPIENT_ID,
    RETENTION_REFERENCE,
    _micro_usd,
    _post,
)
from polycodebench_taskgen import (  # noqa: E402
    CandidateScreener,
    CandidateTask,
    ExposureEvent,
    ScreeningPolicy,
    build_reference_index,
)

# Each family fixes the function name and the behaviour the hidden tests must cover. The prompt
# text is the same for every family, so only these entries differ.
FAMILIES: dict[str, dict[str, str]] = {
    "kth-distinct-keyed": {
        "prefix": "kth",
        "function": "kth_distinct_keyed",
        "signature": "kth_distinct_keyed(items, k, key)",
        "skill": (
            "selecting the k-th smallest distinct key from a list after applying a "
            "caller-supplied key function, with deterministic tie-breaking by first appearance"
        ),
        "focus": (
            "k < 1 raises ValueError, fewer distinct keys returns None, first-appearance "
            "tie-breaking, the original element is returned rather than its key"
        ),
    },
    "merged-interval-length": {
        "prefix": "interval",
        "function": "merged_interval_length",
        "signature": "merged_interval_length(intervals)",
        "skill": (
            "computing how many integers are covered by a list of closed integer intervals "
            "[start, end] after merging overlapping and touching intervals; the input may be "
            "unsorted and each interval may be a tuple or a list"
        ),
        "focus": (
            "empty input returns 0, overlapping and touching intervals merge, a point interval "
            "[x, x] covers one integer, start greater than end raises ValueError, unsorted and "
            "negative inputs"
        ),
    },
    "max-window-sum": {
        "prefix": "window",
        "function": "max_window_sum",
        "signature": "max_window_sum(values, width)",
        "skill": (
            "returning the largest sum of any contiguous window of a given width in a list of "
            "integers; width must be at least 1 and at most len(values)"
        ),
        "focus": (
            "width below 1 or above len(values) raises ValueError, all-negative input, width "
            "equal to len(values), single-element windows"
        ),
    },
}
DEFAULT_FAMILY = "kth-distinct-keyed"
POLICY_PATH = ROOT / "config" / "task-generation" / "screening-policy-v1.json"
SECRET_DIR = ROOT / ".protected" / "taskgen-secrets"

PROMPT_TEMPLATE = """You are drafting ONE Python evaluation task package for a code benchmark.

Skill measured: {skill}
Rules you must follow:
{rules}

Reply with ONLY a JSON object, no prose and no markdown fences. Keys and types:
- "statement": string, markdown task text for a solver. Name the function exactly
  `{signature}` and describe its behaviour precisely.
- "starter_solution": string, a solution.py that defines `{function}` with body
  `raise NotImplementedError`.
- "public_tests": object with exactly one key "test_public.py" whose value is pytest source
  with 2-3 simple example tests. The first line is `from solution import {function}`.
- "hidden_tests": object with exactly one key "test_hidden.py" whose value is pytest source
  with 6-10 test functions named test_*, covering edge cases ({focus}).
  The first line is `from solution import {function}`. Use only the standard library.
- "reference_solution": string, a correct solution.py.
- "fixtures": array of exactly 4 objects, in this order:
  1. {{"variant": "faulty", "solution": ..., "expectation": {{"failing_cases": [...]}}}}
     A solution.py with ONE realistic bug. "failing_cases" lists the hidden test function names
     that this bug makes fail, as strings like "test_hidden.py::test_name".
  2. {{"variant": "alternative", "solution": ..., "expectation": {{}}}}
     A DIFFERENT correct solution.py (different algorithm or structure), passing all hidden tests.
  3. {{"variant": "quality_defective", "solution": ...,
       "expectation": {{"expected_issue_families": ["bare-except"]}}}}
     A solution.py that is correct for every hidden test but contains a bare `except:` clause.
  4. {{"variant": "timeout", "solution": ..., "expectation": {{}}}}
     A solution.py that is plausible but loops forever on large inputs.
"""

RULES = (
    "- Write original code and tests for this exercise. Do not reproduce a known contest,\n"
    "  textbook or library problem, and do not name one.\n"
    "- No URLs, dataset names, author names or identifier-like strings.\n"
    "- Use only the Python standard library.\n"
    "- Keep every file under about 80 lines."
)


def _single_file(value: Any, name: str) -> str:
    """Accept {name: text} or a bare string; anything else is a malformed reply."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and len(value) == 1:
        text = next(iter(value.values()))
        if isinstance(text, str):
            return text
    raise ValueError(f"{name} must be a single-file object or string")


def _parse_reply(content: str) -> dict[str, Any]:
    text = content.strip()
    fence = re.match(r"^```(?:json)?\s*\n(.*)\n```$", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("reply must be a JSON object")
    return data


def _case_id(raw: object) -> str:
    text = str(raw).strip()
    name = text.split("::")[-1]
    if not re.fullmatch(r"test_[A-Za-z0-9_]+", name):
        raise ValueError(f"failing case is not a test function name: {text[:80]}")
    return f"tests/test_hidden.py::{name}"


def _fixtures(items: object) -> list[dict[str, Any]]:
    if not isinstance(items, list) or len(items) != 4:
        raise ValueError("fixtures must be a list of exactly four objects")
    expected = ["faulty", "alternative", "quality_defective", "timeout"]
    built: list[dict[str, Any]] = []
    for index, (item, variant) in enumerate(zip(items, expected, strict=True), start=1):
        if not isinstance(item, dict) or item.get("variant") != variant:
            raise ValueError(f"fixture {index} must have variant {variant}")
        solution = item.get("solution")
        if not isinstance(solution, str) or not solution.strip():
            raise ValueError(f"fixture {variant} has no solution text")
        if variant == "faulty":
            cases = (item.get("expectation") or {}).get("failing_cases") or []
            expectation: dict[str, Any] = {
                "failing_cases": sorted({_case_id(case) for case in cases})
            }
        elif variant == "quality_defective":
            families = (item.get("expectation") or {}).get("expected_issue_families") or []
            expectation = {"expected_issue_families": ["bare-except"] if not families else families}
        elif variant == "timeout":
            expectation = {"expected_failure": "candidate_timeout"}
        else:
            expectation = {}
        built.append(
            {
                "variant": variant,
                "name": f"{variant.replace('_', '-')}-{index}",
                "solution": solution,
                "expectation": expectation,
            }
        )
    return built


def _provenance(access_date: str) -> dict[str, Any]:
    return {
        "authorship": (
            f"AI-drafted by {GENERATOR_ID} through a one-off owner-authorised batch "
            "(scripts/glm_package_batch.py); not reviewed by a human"
        ),
        "originality_statement": (
            "Drafted from a family request that forbade reproducing known problems and was "
            "screened against local corpora only; no web-wide similarity check was performed"
        ),
        "public_exposure_review": (
            f"Sent to the external generator on {access_date}; no public release recorded; "
            "web-wide contamination was not checked"
        ),
        "access_history": [
            {
                "party": f"{GENERATOR_ID} API ({RECIPIENT_ID})",
                "access": "received the request and returned the draft",
                "date": access_date,
            },
            {
                "party": "PolyCodeBench maintainers",
                "access": "not yet reviewed by a human",
                "date": access_date,
            },
        ],
    }


def _secret(name: str) -> bytes:
    path = SECRET_DIR / f"{name}.secret"
    value = bytes.fromhex(path.read_text(encoding="utf-8").strip())
    if len(value) < 32:
        raise SystemExit(f"{name} secret is too short")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(prog="glm_package_batch")
    parser.add_argument("--family", choices=sorted(FAMILIES), default=DEFAULT_FAMILY)
    parser.add_argument("--calls", type=int, default=1)
    parser.add_argument("--max-tokens", type=int, default=96000)
    parser.add_argument("--temperature", type=float, default=0.8)
    parser.add_argument("--budget-micro-usd", type=int, required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    out = args.out.resolve()
    if ROOT / ".protected" not in out.parents:
        raise SystemExit("--out must be inside .protected/")
    api_key = os.environ.get("ZHIPU_API_KEY", "")
    if not api_key:
        raise SystemExit("set ZHIPU_API_KEY in the environment for this command")
    if not 1 <= args.calls <= 40:
        raise SystemExit("calls must be 1-40")
    out.mkdir(parents=True, exist_ok=True)

    family = FAMILIES[args.family]
    prompt = PROMPT_TEMPLATE.format(
        skill=family["skill"],
        rules=RULES,
        function=family["function"],
        signature=family["signature"],
        focus=family["focus"],
    )
    worst_case = _micro_usd(len(prompt.encode("utf-8")), args.max_tokens)
    spent = 0
    replies: list[dict[str, Any]] = []
    calls_log: list[dict[str, Any]] = []
    for index in range(1, args.calls + 1):
        if spent + worst_case > args.budget_micro_usd:
            calls_log.append({"call": index, "stopped": "worst-case cost would exceed budget"})
            break
        occurred = datetime.now(UTC)
        payload = _post(api_key, prompt, args.max_tokens, args.temperature)
        if payload is None:
            spent += worst_case
            calls_log.append({"call": index, "result": "uncertain_request_failed"})
            break
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        prompt_tokens = int(usage.get("prompt_tokens", len(prompt)))
        completion_tokens = int(usage.get("completion_tokens", args.max_tokens))
        cost = _micro_usd(prompt_tokens, completion_tokens)
        spent += cost
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        (out / f"call-{index:03d}.json").write_bytes(raw)
        entry: dict[str, Any] = {
            "call": index,
            "completion_tokens": completion_tokens,
            "cost_micro_usd": cost,
            "spent_micro_usd": spent,
            "raw_digest": digest,
        }
        choices = payload.get("choices") or []
        content = ""
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message") or {}
            if isinstance(message, dict):
                content = str(message.get("content") or "")
        if not content.strip():
            entry["result"] = "empty_content"
        else:
            try:
                fields = _parse_reply(content)
                fixtures = _fixtures(fields.get("fixtures"))
                parts = {
                    "statement": str(fields.get("statement") or ""),
                    "starter_solution": str(fields.get("starter_solution") or ""),
                    "public_tests": _single_file(fields.get("public_tests"), "public_tests"),
                    "hidden_tests": _single_file(fields.get("hidden_tests"), "hidden_tests"),
                    "reference_solution": str(fields.get("reference_solution") or ""),
                }
                if not all(parts.values()) or len(parts["statement"]) < 20:
                    raise ValueError("a required content field is empty")
            except ValueError as error:
                entry["result"] = f"rejected_reply: {str(error)[:140]}"
            else:
                entry["result"] = "parsed"
                replies.append(
                    {
                        "call": index,
                        "parts": parts,
                        "fixtures": fixtures,
                        "occurred_at": occurred,
                        "digest": digest,
                    }
                )
        calls_log.append(entry)
        time.sleep(1)

    run_id = hashlib.sha256("".join(r["digest"] for r in replies).encode()).hexdigest()[:8]
    candidates: list[CandidateTask] = []
    for number, reply in enumerate(replies, start=1):
        candidate_id = f"{family['prefix']}-{run_id}-{number:02d}"
        reply["candidate_id"] = candidate_id
        candidates.append(
            CandidateTask(
                candidate_id=candidate_id,
                family_id=args.family,
                generator_id=GENERATOR_ID,
                generator_is_external=True,
                statement=reply["parts"]["statement"],
                reference_solution=reply["parts"]["reference_solution"],
                hidden_tests=reply["parts"]["hidden_tests"],
            )
        )

    policy = ScreeningPolicy.model_validate_json(POLICY_PATH.read_bytes())
    index, corpus_digest = build_reference_index(
        {"taskpacks": ROOT / "taskpacks", "protected": ROOT / ".protected" / "taskpacks"},
        ngram=policy.ngram,
    )
    screener = CandidateScreener(
        index=index,
        policy=policy,
        canary_secret=_secret("canary"),
        split_secret=_secret("split"),
        corpus_digest=corpus_digest,
    )
    reports = screener.screen_batch(candidates) if candidates else ()
    report_by_id = {report.candidate_id: report for report in reports}
    (out / "screening.json").write_text(
        json.dumps([r.model_dump(mode="json") for r in reports], indent=2) + "\n", encoding="utf-8"
    )

    admitted: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    drafts_dir = out / "drafts"
    drafts_dir.mkdir(exist_ok=True)
    for reply in replies:
        report = report_by_id[reply["candidate_id"]]
        events.append(
            ExposureEvent(
                task_id=reply["candidate_id"],
                audience="generator_model",
                recipient_id=RECIPIENT_ID,
                occurred_at=reply["occurred_at"],
                evidence_digest=reply["digest"],
            ).model_dump(mode="json")
        )
        row: dict[str, Any] = {
            "candidate_id": reply["candidate_id"],
            "screening": report.decision.value,
        }
        if report.decision.value != "ready_for_executable_admission":
            failed = [g.gate for g in report.gates if not g.passed]
            row["screen_failures"] = failed
            admitted.append(row)
            continue
        screening_reference = f"taskgen-screen:{reply['candidate_id']}:{policy.digest()[:24]}"
        document = {
            "schema_version": 1,
            "kind": "generated_task_package_draft",
            "task_id": reply["candidate_id"],
            "screening_reference": screening_reference,
            "curated_at": reply["occurred_at"].strftime("%Y-%m-%dT%H:%M:%SZ"),
            "difficulty": "intermediate",
            "statement": reply["parts"]["statement"],
            "starter_solution": reply["parts"]["starter_solution"],
            "public_tests": {"test_public.py": reply["parts"]["public_tests"]},
            "hidden_tests": {"test_hidden.py": reply["parts"]["hidden_tests"]},
            "reference_solution": reply["parts"]["reference_solution"],
            "fixtures": reply["fixtures"],
            "provenance": _provenance(reply["occurred_at"].date().isoformat()),
        }
        draft_path = drafts_dir / f"{reply['candidate_id']}.json"
        draft_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        try:
            packager.load_draft(document)
        except ValueError as error:
            row["draft_rejected"] = str(error)[:200]
            admitted.append(row)
            continue
        report_path = out / "admission" / f"{reply['candidate_id']}.json"
        report_path.parent.mkdir(exist_ok=True)
        code = packager.main([str(draft_path), "--report", str(report_path)])
        row["admission_exit"] = code
        row["admission_report"] = str(report_path.relative_to(ROOT))
        admitted.append(row)

    (out / "exposure-events.json").write_text(json.dumps(events, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "model": GENERATOR_ID,
        "retention_reference": RETENTION_REFERENCE,
        "budget_micro_usd": args.budget_micro_usd,
        "spent_micro_usd": spent,
        "calls": calls_log,
        "drafts_parsed": len(replies),
        "rows": admitted,
    }
    (out / "run-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    passed = sum(1 for row in admitted if row.get("admission_exit") == 0)
    print(json.dumps({"spent_micro_usd": spent, "parsed": len(replies), "admitted": passed}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
