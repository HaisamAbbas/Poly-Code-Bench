"""Generator request and untrusted-reply handling (packages/taskgen/generation.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from polycodebench_core.errors import DuplicateKeyError
from polycodebench_taskgen.cli import main as taskgen_main
from polycodebench_taskgen.generation import (
    MAX_REPLY_BYTES,
    FamilySpec,
    build_generation_request,
    parse_generator_output,
)


def _spec(**overrides: object) -> FamilySpec:
    values: dict[str, object] = {
        "family_id": "interval-merge",
        "language": "python",
        "skill": "merging ranges while preserving boundary semantics",
        "constraints": ("Use integer endpoints only.",),
    }
    values.update(overrides)
    return FamilySpec.model_validate(values)


def _reply(items: int = 2) -> str:
    return json.dumps(
        [
            {
                "statement": f"Task {index}: merge the ranges.",
                "reference_solution": f"def solve_{index}(r):\n    return sorted(r)\n",
                "hidden_tests": f"assert solve_{index}([[2, 3], [1, 4]]) == [[1, 4]]\n",
            }
            for index in range(items)
        ]
    )


def test_request_states_the_anti_leak_rules_and_the_reply_contract() -> None:
    text = build_generation_request(
        _spec(), count=3, must_avoid=("a contest problem about ranges",)
    )
    assert "merging ranges" in text
    assert "Do not reproduce" in text
    assert "exactly these string keys" in text
    assert "statement" in text and "reference_solution" in text and "hidden_tests" in text
    assert "a contest problem about ranges" in text
    assert "Use integer endpoints only." in text


def test_request_refuses_out_of_range_counts() -> None:
    with pytest.raises(ValueError, match="between 1 and 50"):
        build_generation_request(_spec(), count=0)


def test_family_spec_requires_a_meaningful_skill() -> None:
    with pytest.raises(ValueError):
        _spec(skill="short")


def test_reply_is_parsed_with_assigned_identifiers() -> None:
    candidates = parse_generator_output(
        _reply(3),
        spec=_spec(),
        generator_id="reviewed-generator-1",
        generator_is_external=True,
    )
    assert len(candidates) == 3
    identifiers = [candidate.candidate_id for candidate in candidates]
    assert len(set(identifiers)) == 3
    assert all(identifier.startswith("interval-merge-") for identifier in identifiers)
    assert all(candidate.generator_is_external for candidate in candidates)
    assert candidates[0].statement == "Task 0: merge the ranges."


def test_fenced_reply_is_accepted_but_prose_around_it_is_not() -> None:
    fenced = "```json\n" + _reply(1) + "\n```"
    assert (
        len(
            parse_generator_output(
                fenced, spec=_spec(), generator_id="g", generator_is_external=False
            )
        )
        == 1
    )
    prose = "Here are the tasks:\n" + _reply(1)
    with pytest.raises(ValueError):
        parse_generator_output(prose, spec=_spec(), generator_id="g", generator_is_external=False)


@pytest.mark.parametrize(
    "reply",
    [
        "{}",
        "[]",
        json.dumps([{"statement": "s", "reference_solution": "r"}]),
        json.dumps([{"statement": "s", "reference_solution": "r", "hidden_tests": "t", "id": "x"}]),
        json.dumps([{"statement": 1, "reference_solution": "r", "hidden_tests": "t"}]),
    ],
)
def test_malformed_replies_are_rejected(reply: str) -> None:
    with pytest.raises(ValueError):
        parse_generator_output(reply, spec=_spec(), generator_id="g", generator_is_external=False)


def test_duplicate_keys_in_a_reply_are_rejected() -> None:
    reply = (
        '[{"statement": "one", "statement": "two", "reference_solution": "r", "hidden_tests": "t"}]'
    )
    with pytest.raises(DuplicateKeyError):
        parse_generator_output(reply, spec=_spec(), generator_id="g", generator_is_external=False)


def test_oversized_reply_is_rejected_before_parsing() -> None:
    with pytest.raises(ValueError, match="size limit"):
        parse_generator_output(
            "x" * (MAX_REPLY_BYTES + 1), spec=_spec(), generator_id="g", generator_is_external=False
        )


def test_cli_request_and_parse_round_trip(tmp_path: Path) -> None:
    family = tmp_path / "family.json"
    family.write_text(
        json.dumps(
            {
                "family_id": "interval-merge",
                "language": "python",
                "skill": "merging ranges while preserving boundary semantics",
                "constraints": [],
            }
        ),
        encoding="utf-8",
    )
    prompt = tmp_path / "prompt.txt"
    assert taskgen_main(
        ["request", "--family", str(family), "--count", "2", "--output", str(prompt)]
    ) == 0  # fmt: skip
    assert "JSON array" in prompt.read_text(encoding="utf-8")
    reply = tmp_path / "reply.txt"
    reply.write_text(_reply(2), encoding="utf-8")
    output = tmp_path / "candidates.json"
    assert taskgen_main(
        [
            "parse",
            "--family", str(family),
            "--reply", str(reply),
            "--generator-id", "local-review-1",
            "--output", str(output),
        ]
    ) == 0  # fmt: skip
    parsed = json.loads(output.read_text(encoding="utf-8"))
    assert [item["generator_is_external"] for item in parsed] == [False, False]
    assert all(item["family_id"] == "interval-merge" for item in parsed)
