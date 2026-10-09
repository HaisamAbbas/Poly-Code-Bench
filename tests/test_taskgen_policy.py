"""Shipped screening policy and split modes (packages/taskgen/screening.py)."""

from __future__ import annotations

from pathlib import Path

import pytest
from polycodebench_taskgen.contracts import CandidateTask, ScreeningDecision
from polycodebench_taskgen.overlap import build_reference_index
from polycodebench_taskgen.screening import CandidateScreener, ScreeningPolicy
from polycodebench_taskgen.splits import SplitRatios, assign_split

REPO = Path(__file__).resolve().parents[1]
POLICY_PATH = REPO / "config" / "task-generation" / "screening-policy-v1.json"
RATIOS = SplitRatios(public_development_bp=3000, public_validation_bp=1000, private_heldout_bp=6000)


# Distinct problems: a template repeated with new constants would look like one problem to the
# structural view, and the batch screen would (correctly) reject the copies.
_DISTINCT = (
    (
        "Count occurrences of a value in a stream of readings.",
        "def count_value(stream, value):\n    return sum(1 for item in stream if item == value)\n",
        "assert count_value([1, 2, 1], 1) == 2\n",
    ),
    (
        "Reverse the order of the words in a sentence.",
        "def flip_words(text):\n    return ' '.join(reversed(text.split()))\n",
        "assert flip_words('a bc d') == 'd bc a'\n",
    ),
    (
        "List every prime below a limit using a sieve.",
        "def primes_below(limit):\n    flags = [True] * limit\n    found = []\n"
        "    for n in range(2, limit):\n        if flags[n]:\n            found.append(n)\n"
        "            for m in range(n * n, limit, n):\n                flags[m] = False\n"
        "    return found\n",
        "assert primes_below(10) == [2, 3, 5, 7]\n",
    ),
    (
        "Decide whether brackets in a string are balanced.",
        "def balanced(text):\n    depth = 0\n    for ch in text:\n"
        "        depth += (ch == '(') - (ch == ')')\n"
        "        if depth < 0:\n            return False\n    return depth == 0\n",
        "assert balanced('(()())') and not balanced(')(')\n",
    ),
)


def _candidates(count: int) -> list[CandidateTask]:
    return [
        CandidateTask(
            candidate_id=f"item-{index:03d}",
            family_id="mixed-problems",
            generator_id="local-review-1",
            generator_is_external=False,
            statement=statement,
            reference_solution=solution,
            hidden_tests=tests,
        )
        for index, (statement, solution, tests) in enumerate(_DISTINCT[:count])
    ]


def _screener(tmp_path: Path, policy: ScreeningPolicy) -> CandidateScreener:
    corpus = tmp_path / "seen"
    corpus.mkdir()
    (corpus / "placeholder.txt").write_text("nothing relevant here", encoding="utf-8")
    index, digest = build_reference_index({"seen": corpus}, ngram=policy.ngram)
    return CandidateScreener(
        index=index,
        policy=policy,
        canary_secret=b"c" * 32,
        split_secret=b"p" * 32,
        corpus_digest=digest,
    )


def test_shipped_policy_loads_and_is_held_out_by_default() -> None:
    policy = ScreeningPolicy.model_validate_json(POLICY_PATH.read_bytes())
    assert policy.split_mode == "private_heldout_only"
    # Owner decision, 2026-10-08. Changing this list must be a reviewed change.
    assert policy.approved_external_generators == ("glm-5.3-flash",)
    assert policy.digest() == ScreeningPolicy.model_validate_json(POLICY_PATH.read_bytes()).digest()


def test_generated_candidates_stay_private_under_the_default_mode(tmp_path: Path) -> None:
    policy = ScreeningPolicy.model_validate_json(POLICY_PATH.read_bytes())
    reports = _screener(tmp_path, policy).screen_batch(_candidates(4))
    assert all(
        report.decision is ScreeningDecision.READY_FOR_EXECUTABLE_ADMISSION for report in reports
    )
    assert {report.split for report in reports} == {"private_heldout"}


def test_keyed_ratios_split_each_cluster_by_its_keyed_hash(tmp_path: Path) -> None:
    policy = ScreeningPolicy(
        policy_id="keyed-test",
        split_mode="keyed_ratios",
        split_ratios=RATIOS,
    )
    reports = _screener(tmp_path, policy).screen_batch(_candidates(4))
    assert reports, "the batch should produce reports"
    for report in reports:
        assert report.cluster_id is not None
        assert report.split == assign_split(report.cluster_id, secret=b"p" * 32, ratios=RATIOS)


def test_split_mode_is_part_of_the_policy_digest() -> None:
    held = ScreeningPolicy(policy_id="x", split_ratios=RATIOS)
    keyed = ScreeningPolicy(policy_id="x", split_ratios=RATIOS, split_mode="keyed_ratios")
    assert held.digest() != keyed.digest()


@pytest.mark.parametrize("value", ["public", "heldout", ""])
def test_unknown_split_modes_are_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        ScreeningPolicy.model_validate(
            {"policy_id": "x", "split_ratios": RATIOS.model_dump(), "split_mode": value}
        )
