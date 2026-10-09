"""``pcb-taskgen``: screen generated candidates and check exposure, splits and round commitments.

The CLI reads JSON and writes JSON. It never prints secret material, never calls a model and never
executes candidate code. Secret files hold hex text and must be created with ``new-secret``.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from polycodebench_taskgen.canary import MIN_SECRET_BYTES, derive_canary
from polycodebench_taskgen.contracts import CandidateTask, ExposureEvent
from polycodebench_taskgen.exposure import ExposureLedger
from polycodebench_taskgen.generation import (
    FamilySpec,
    build_generation_request,
    dumps_candidates,
    parse_generator_output,
)
from polycodebench_taskgen.overlap import build_reference_index
from polycodebench_taskgen.parametric import RoundCommitment
from polycodebench_taskgen.screening import CandidateScreener, ScreeningPolicy
from polycodebench_taskgen.splits import rank_agreement_bp


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pcb-taskgen")
    commands = parser.add_subparsers(dest="command", required=True)

    new_secret = commands.add_parser("new-secret", help="write a fresh random secret file")
    new_secret.add_argument("--output", required=True, type=Path)

    request = commands.add_parser(
        "request", help="write the generator request text (no model is called)"
    )
    request.add_argument("--family", required=True, type=Path, help="FamilySpec JSON")
    request.add_argument("--count", required=True, type=int)
    request.add_argument("--avoid", action="append", default=[], help="reference description")
    request.add_argument("--output", required=True, type=Path)

    parse = commands.add_parser("parse", help="validate a generator reply and emit candidate JSON")
    parse.add_argument("--family", required=True, type=Path, help="FamilySpec JSON")
    parse.add_argument("--reply", required=True, type=Path)
    parse.add_argument("--generator-id", required=True)
    parse.add_argument(
        "--external-generator",
        action="store_true",
        help="the reply came from a hosted service; screening will require approval",
    )
    parse.add_argument("--output", required=True, type=Path)

    canary = commands.add_parser("canary", help="print the canary for one item")
    canary.add_argument("--secret-file", required=True, type=Path)
    canary.add_argument("--family-id", required=True)
    canary.add_argument("--candidate-id", required=True)

    screen = commands.add_parser("screen", help="screen a JSON batch of candidates")
    screen.add_argument("--candidates", required=True, type=Path)
    screen.add_argument("--policy", required=True, type=Path)
    screen.add_argument(
        "--reference-root",
        required=True,
        action="append",
        help="LABEL=DIRECTORY; repeat for every corpus (seen tasks, held-out tasks, references)",
    )
    screen.add_argument("--canary-secret-file", required=True, type=Path)
    screen.add_argument("--split-secret-file", required=True, type=Path)
    screen.add_argument("--known-canaries", type=Path, help="JSON list of issued canaries")
    screen.add_argument("--family-registry", type=Path, help="JSON map of document id to cluster")
    screen.add_argument("--output", required=True, type=Path)

    exposure = commands.add_parser("exposure-check", help="is a task unseen by one endpoint?")
    exposure.add_argument("--ledger", required=True, type=Path)
    exposure.add_argument("--task-id", required=True)
    exposure.add_argument("--recipient", required=True)
    exposure.add_argument("--cutoff", type=datetime.fromisoformat)
    exposure.add_argument("--cutoff-confidence", required=True,
                          choices=("verified", "estimated", "unknown"))  # fmt: skip
    exposure.add_argument("--evaluation-at", required=True, type=datetime.fromisoformat)

    agreement = commands.add_parser("agreement", help="rank agreement of validation and test")
    agreement.add_argument("--validation", required=True, type=Path)
    agreement.add_argument("--test", required=True, type=Path)

    commit = commands.add_parser("round-commit", help="publish the commitment for one round")
    commit.add_argument("--round-id", required=True)
    commit.add_argument("--secret-file", required=True, type=Path)

    verify = commands.add_parser("round-verify", help="check a revealed round secret")
    verify.add_argument("--commitment", required=True, type=Path)
    verify.add_argument("--secret-file", required=True, type=Path)
    return parser


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_secret(path: Path) -> bytes:
    text = path.read_text(encoding="utf-8").strip()
    try:
        value = bytes.fromhex(text)
    except ValueError as exc:
        raise SystemExit("secret file must contain hexadecimal text") from exc
    if len(value) < MIN_SECRET_BYTES:
        raise SystemExit(f"secret must be at least {MIN_SECRET_BYTES} bytes")
    return value


def _emit(payload: Any) -> None:
    sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _parse_roots(values: list[str]) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for value in values:
        label, separator, directory = value.partition("=")
        if not separator or not label or label in roots:
            raise SystemExit(f"reference root must be LABEL=DIRECTORY with a unique label: {value}")
        roots[label] = Path(directory)
    return roots


def _run_screen(args: argparse.Namespace) -> int:
    policy = ScreeningPolicy.model_validate_json(args.policy.read_bytes())
    candidates = [CandidateTask.model_validate(item) for item in _read_json(args.candidates)]
    index, corpus_digest = build_reference_index(
        _parse_roots(args.reference_root), ngram=policy.ngram
    )
    known = frozenset(_read_json(args.known_canaries)) if args.known_canaries else frozenset()
    registry = _read_json(args.family_registry) if args.family_registry else {}
    screener = CandidateScreener(
        index=index,
        policy=policy,
        canary_secret=_read_secret(args.canary_secret_file),
        split_secret=_read_secret(args.split_secret_file),
        corpus_digest=corpus_digest,
        known_canaries=known,
        family_registry=registry,
    )
    reports = screener.screen_batch(candidates)
    _emit_to(args.output, [report.model_dump(mode="json") for report in reports])
    accepted = sum(1 for report in reports if report.split is not None)
    sys.stderr.write(f"screened {len(reports)} candidates; ready: {accepted}\n")
    return 0


def _emit_to(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _run_exposure(args: argparse.Namespace) -> int:
    ledger = ExposureLedger(
        ExposureEvent.model_validate_json(json.dumps(item)) for item in _read_json(args.ledger)
    )
    if args.cutoff is None and args.cutoff_confidence != "unknown":
        raise SystemExit("a cutoff is required unless --cutoff-confidence is unknown")
    decision = ledger.eligibility_for_model(
        args.task_id,
        recipient_id=args.recipient,
        cutoff_at=args.cutoff,
        cutoff_confidence=args.cutoff_confidence,
        evaluation_at=args.evaluation_at,
    )
    _emit(decision.model_dump(mode="json"))
    return 0 if decision.eligible else 3


def _run_agreement(args: argparse.Namespace) -> int:
    validation = {str(k): int(v) for k, v in _read_json(args.validation).items()}
    test = {str(k): int(v) for k, v in _read_json(args.test).items()}
    value = rank_agreement_bp(validation, test)
    _emit({"shared_models": len(set(validation) & set(test)), "spearman_bp": value})
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "new-secret":
        if args.output.exists():
            raise SystemExit("refusing to overwrite an existing secret file")
        args.output.write_text(secrets.token_hex(MIN_SECRET_BYTES) + "\n", encoding="utf-8")
        return 0
    if args.command == "request":
        spec = FamilySpec.model_validate_json(args.family.read_bytes())
        text = build_generation_request(spec, count=args.count, must_avoid=tuple(args.avoid))
        args.output.write_text(text, encoding="utf-8")
        return 0
    if args.command == "parse":
        spec = FamilySpec.model_validate_json(args.family.read_bytes())
        reply = args.reply.read_text(encoding="utf-8")
        candidates = parse_generator_output(
            reply,
            spec=spec,
            generator_id=args.generator_id,
            generator_is_external=args.external_generator,
        )
        args.output.write_text(dumps_candidates(candidates) + "\n", encoding="utf-8")
        sys.stderr.write(f"parsed {len(candidates)} candidates\n")
        return 0
    if args.command == "canary":
        canary = derive_canary(
            _read_secret(args.secret_file),
            family_id=args.family_id,
            candidate_id=args.candidate_id,
        )
        _emit({"canary": canary})
        return 0
    if args.command == "screen":
        return _run_screen(args)
    if args.command == "exposure-check":
        return _run_exposure(args)
    if args.command == "agreement":
        return _run_agreement(args)
    if args.command == "round-commit":
        commitment = RoundCommitment.commit(
            round_id=args.round_id, round_secret=_read_secret(args.secret_file)
        )
        _emit(commitment.model_dump(mode="json"))
        return 0
    if args.command == "round-verify":
        commitment = RoundCommitment.model_validate(_read_json(args.commitment))
        ok = commitment.verify(_read_secret(args.secret_file))
        _emit({"round_id": commitment.round_id, "verified": ok})
        return 0 if ok else 4
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
