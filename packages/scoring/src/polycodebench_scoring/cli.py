"""``pcb-score``: deterministic scoring and clean-process replay.

The commands are deliberately small and never touch a candidate, a judge or the network. ``score``
is a pure function of three frozen inputs; ``replay`` re-runs it against archived evidence and
compares canonical digests; ``explain`` prints the item-by-item chain.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from polycodebench_plugins_api import FrozenTask, LanguageProfile

from polycodebench_scoring.errors import ScoringConfigError, ScoringRefused
from polycodebench_scoring.loader import (
    archive_document,
    load_evidence_manifest,
    load_evidence_ownership,
    load_frozen_task,
    load_language_profile,
    load_outcome,
    load_scoring_policy,
    write_json,
)
from polycodebench_scoring.scorer import ScoringOutcome, score_evaluation


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pcb-score")
    commands = parser.add_subparsers(dest="command", required=True)

    score = commands.add_parser("score", help="score one validated evidence manifest")
    _common(score)
    score.add_argument("--output", type=Path, help="write the canonical outcome here")
    score.add_argument("--explain", action="store_true", help="print the item chain to stdout")

    replay = commands.add_parser(
        "replay", help="reproduce an archived scorecard from archived evidence"
    )
    _common(replay)
    replay.add_argument(
        "--archived-outcome",
        type=Path,
        help="the archived scoring outcome whose canonical bytes must be reproduced",
    )
    return parser


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--ownership", required=True, type=Path)
    parser.add_argument("--task", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument(
        "--language-profile",
        type=Path,
        help="frozen LanguageProfile JSON; required when idiomatic strength is applicable",
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        policy = load_scoring_policy(args.policy)
        ownership = load_evidence_ownership(args.ownership)
        task: FrozenTask = load_frozen_task(args.task)
        evidence = load_evidence_manifest(args.evidence)
        profile: LanguageProfile | None = (
            load_language_profile(args.language_profile) if args.language_profile else None
        )
        outcome: ScoringOutcome = score_evaluation(
            task, policy, evidence, ownership=ownership, profile=profile
        )
        payload = archive_document(outcome)
        if args.command == "replay" and args.archived_outcome:
            return _replay(payload, load_outcome(args.archived_outcome))
        if args.output:
            write_json(args.output, payload)
        if getattr(args, "explain", False):
            _print_explanation(outcome)
        elif args.output is None:
            print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 0
    except ScoringRefused as error:
        print(f"scoring refused: {error.code}: {error.detail}", file=sys.stderr)
        return 3
    except ScoringConfigError as error:
        print(f"invalid scoring configuration: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # pragma: no cover - interactive interruption
        return 130


def _replay(payload: dict[str, object], archived: ScoringOutcome) -> int:
    if payload == archive_document(archived):
        print(
            json.dumps(
                {"replayed": True, "outcome_digest": archived.content_digest()}, sort_keys=True
            )
        )
        return 0
    print(
        "replay mismatch: the recomputed outcome differs from the archived scorecard",
        file=sys.stderr,
    )
    return 4


def _print_explanation(outcome: ScoringOutcome) -> None:
    explanation = outcome.explanation
    print(f"board: {explanation.board}  gate: {explanation.gate}  status: {explanation.status}")
    print(f"policy {explanation.policy_id} ({explanation.policy_digest})")
    for row in explanation.dimensions:
        value = row.raw_value if row.raw_value is not None else "n/a"
        print(
            f"  {row.dimension.value:<12} nominal {row.nominal_weight_bp:>4}bp  "
            f"effective {row.effective_weight_bps:>4}bp  value {value:>10}  "
            f"contribution {row.contribution}"
        )
    print(f"total: {explanation.total_score if explanation.total_score else 'not publishable'}")
    print(explanation.total_formula)
    for reason in explanation.blocking:
        print(f"  blocking [{reason.reason_class}] {reason.reference}: {reason.detail}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
