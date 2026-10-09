"""Request and reply handling for AI-drafted candidates.

This module never calls a model. ``build_generation_request`` writes the text an operator sends
through an approved generator path, and ``parse_generator_output`` turns the reply into
``CandidateTask`` records. The reply is untrusted: it must be strict JSON with exactly the three
content fields, and candidate identifiers are assigned here, never taken from the model.
"""

from __future__ import annotations

import hashlib
import json
import re

from polycodebench_core.canonical import parse_json_strict
from pydantic import Field

from polycodebench_taskgen.contracts import SLUG, CandidateTask, TaskgenModel

MAX_REPLY_BYTES = 2_000_000
MAX_ITEMS = 50
_FENCE = re.compile(r"\A\s*```(?:json)?\s*\n(?P<body>.*)\n\s*```\s*\Z", re.DOTALL)
_CONTENT_FIELDS = frozenset({"statement", "reference_solution", "hidden_tests"})

_RULES = (
    "Write original problems. Do not reproduce, paraphrase or lightly edit a known contest, "
    "textbook, benchmark or open-source problem, and do not name one.",
    "Do not include URLs, dataset names, author names, or any text that looks like a canary "
    "or identifier string.",
    "The reference solution must be self-contained and must not read the hidden tests.",
    "The hidden tests must be deterministic, use only the standard library, and check exact "
    "outputs for at least five distinct inputs, including edge cases.",
    "Vary the surface form: avoid stock names such as foo, bar, widget or sample.",
)


class FamilySpec(TaskgenModel):
    """What a family of candidates should measure. Written by a reviewer, not by a model."""

    family_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,79}$")
    language: str = Field(pattern=r"^[a-z][a-z0-9-]{0,31}$")
    skill: str = Field(min_length=10, max_length=600)
    constraints: tuple[str, ...] = Field(default=(), max_length=10)


def build_generation_request(
    spec: FamilySpec, *, count: int, must_avoid: tuple[str, ...] = ()
) -> str:
    if not 1 <= count <= MAX_ITEMS:
        raise ValueError(f"count must be between 1 and {MAX_ITEMS}")
    lines = [
        f"You are drafting {count} evaluation problems for a {spec.language} code benchmark.",
        f"Skill measured: {spec.skill}",
        "",
        "Rules:",
        *(f"- {rule}" for rule in _RULES),
        *(f"- {constraint}" for constraint in spec.constraints),
    ]
    if must_avoid:
        lines.append("Do not resemble any of these reference descriptions:")
        lines.extend(f"- {entry}" for entry in must_avoid)
    lines += [
        "",
        "Reply with only a JSON array. Each element is an object with exactly these string keys:",
        '"statement" (the task for a solver), "reference_solution" (a correct solution),',
        '"hidden_tests" (a test script that exits non-zero on a wrong answer).',
        "Do not add other keys and do not wrap the array in prose.",
    ]
    return "\n".join(lines) + "\n"


def _unwrap(raw: str) -> str:
    match = _FENCE.match(raw)
    return match.group("body") if match else raw


def parse_generator_output(
    raw: str,
    *,
    spec: FamilySpec,
    generator_id: str,
    generator_is_external: bool,
) -> tuple[CandidateTask, ...]:
    """Validate a model reply strictly and return candidates with assigned identifiers."""
    if len(raw.encode("utf-8")) > MAX_REPLY_BYTES:
        raise ValueError("generator reply exceeds the size limit")
    items = parse_json_strict(_unwrap(raw).encode("utf-8"))
    if not isinstance(items, list) or not items:
        raise ValueError("generator reply must be a non-empty JSON array")
    if len(items) > MAX_ITEMS:
        raise ValueError(f"generator reply exceeds {MAX_ITEMS} items")
    batch = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    candidates: list[CandidateTask] = []
    for position, item in enumerate(items, start=1):
        if not isinstance(item, dict) or set(item) != _CONTENT_FIELDS:
            raise ValueError(f"item {position} must have exactly the content fields")
        if not all(isinstance(item[key], str) for key in _CONTENT_FIELDS):
            raise ValueError(f"item {position} fields must be strings")
        candidate_id = f"{spec.family_id}-{batch}-{position:03d}"
        if not re.fullmatch(SLUG, candidate_id):
            raise ValueError("assigned candidate identifier is not a valid slug")
        candidates.append(
            CandidateTask(
                candidate_id=candidate_id,
                family_id=spec.family_id,
                generator_id=generator_id,
                generator_is_external=generator_is_external,
                statement=item["statement"],
                reference_solution=item["reference_solution"],
                hidden_tests=item["hidden_tests"],
            )
        )
    return tuple(candidates)


def dumps_candidates(candidates: tuple[CandidateTask, ...]) -> str:
    return json.dumps([c.model_dump(mode="json") for c in candidates], indent=2, sort_keys=True)
