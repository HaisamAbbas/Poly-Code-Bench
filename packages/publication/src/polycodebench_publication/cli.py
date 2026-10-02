"""Local, explicitly authorized release administration and aggregate replay."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from polycodebench_core.application_errors import (
    AuthorizationError,
    OptimisticVersionConflict,
    PersistenceConflict,
    PersistenceUnavailable,
    ServiceError,
)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def read_json(path: Path) -> Any:
    """Reject ambiguous documents before any mutation or approval."""

    def invalid_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number: {value}")

    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_pairs,
        parse_constant=invalid_constant,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pcb-release")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("aggregate", "report", "replay"):
        sub = commands.add_parser(command)
        sub.add_argument("--request", required=True, type=Path)
        sub.add_argument("--output", type=Path)
        if command == "replay":
            sub.add_argument("--archived", required=True, type=Path)
    verify = commands.add_parser("verify", help="verify a signed manifest with a public key")
    verify.add_argument("--manifest", required=True, type=Path)
    verify.add_argument("--public-key", required=True, type=Path)
    track_a = commands.add_parser(
        "track-a-draft",
        help="create a local reviewed-release draft from a complete Track A aggregate",
    )
    track_a.add_argument("--aggregate", required=True, type=Path)
    track_a.add_argument("--store", required=True, type=Path)
    track_a.add_argument("--subject", required=True)
    track_a.add_argument("--role", required=True, action="append")
    track_a.add_argument("--request-id", required=True)
    for command in ("create", "update", "validate", "review", "approve", "publish", "withdraw"):
        sub = commands.add_parser(command)
        sub.add_argument("--store", required=True, type=Path)
        sub.add_argument("--subject", required=True)
        sub.add_argument("--role", required=True, action="append")
        sub.add_argument("--request-id", required=True)
        if command != "create":
            sub.add_argument("--release-id", required=True)
            sub.add_argument("--expected-version", required=True, type=int)
        if command in ("create", "update"):
            sub.add_argument("--content", required=True, type=Path)
            sub.add_argument("--projection", required=True, type=Path)
        if command == "create":
            sub.add_argument("--predecessor")
            sub.add_argument("--correction-reason")
        if command == "validate":
            sub.add_argument("--evidence", required=True, type=Path)
        if command in ("review", "approve", "withdraw"):
            sub.add_argument("--reason", required=True)
        if command in ("publish", "withdraw"):
            sub.add_argument("--expected-generation", required=True, type=int)
        if command == "publish":
            sub.add_argument("--target", default="local:board")
            sub.add_argument("--key-file", required=True, type=Path)
            sub.add_argument("--key-id", required=True)
    return parser


def _release(args: argparse.Namespace) -> dict[str, Any]:
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    from polycodebench_publication.releases import (
        ReleasePrincipal,
        ReleaseStore,
        SigningKey,
        ValidationEvidence,
    )

    store = ReleaseStore(args.store)
    principal = ReleasePrincipal(args.subject, frozenset(args.role), mfa=True)
    common: dict[str, Any] = {"request_id": args.request_id}
    if args.command == "create":
        return store.draft(
            principal,
            read_json(args.content),
            read_json(args.projection),
            predecessor=args.predecessor,
            correction_reason=args.correction_reason,
            **common,
        )
    common.update(release_id=args.release_id, expected_version=args.expected_version)
    if args.command == "update":
        return store.update(
            principal,
            content=read_json(args.content),
            projection=read_json(args.projection),
            **common,
        )
    if args.command == "validate":
        evidence = read_json(args.evidence)
        if not isinstance(evidence, list):
            raise ValueError("validation evidence must be a JSON array")
        return store.validate(
            principal,
            evidence=tuple(ValidationEvidence(**row) for row in evidence),
            **common,
        )
    if args.command in ("review", "approve"):
        method = store.review if args.command == "review" else store.approve
        return method(principal, reason=args.reason, **common)
    common["expected_generation"] = args.expected_generation
    if args.command == "withdraw":
        return store.withdraw(principal, reason=args.reason, **common)
    key = load_pem_private_key(args.key_file.read_bytes(), password=None)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("signing requires an Ed25519 private key")
    return store.publish(
        principal,
        signer=SigningKey(args.key_id, key),
        target=args.target,
        **common,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command in ("aggregate", "report", "replay"):
            from polycodebench_publication.reporting import execute_request, render_report

            result = execute_request(args.request)
            if args.command == "replay":
                if read_json(args.archived) != result:
                    raise ValueError("fixed-seed replay differs from archived result")
                result = {"replayed": True, "result": result}
            text = (
                render_report(result)
                if args.command == "report"
                else json.dumps(
                    result,
                    indent=2,
                    sort_keys=True,
                    allow_nan=False,
                )
                + "\n"
            )
            if args.output:
                args.output.write_text(text, encoding="utf-8", newline="\n")
            else:
                print(text, end="")
        elif args.command == "verify":
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            from cryptography.hazmat.primitives.serialization import load_pem_public_key

            from polycodebench_publication.releases import verify_manifest

            key = load_pem_public_key(args.public_key.read_bytes())
            if not isinstance(key, Ed25519PublicKey):
                raise ValueError("verification requires an Ed25519 public key")
            valid = verify_manifest(read_json(args.manifest), key)
            print(json.dumps({"verified": valid}))
            return 0 if valid else 3
        elif args.command == "track-a-draft":
            from polycodebench_publication.releases import ReleasePrincipal, ReleaseStore
            from polycodebench_publication.track_a import track_a_release_documents

            content, projection = track_a_release_documents(read_json(args.aggregate))
            draft = ReleaseStore(args.store).draft(
                ReleasePrincipal(args.subject, frozenset(args.role), mfa=True),
                content,
                projection,
                args.request_id,
            )
            print(json.dumps(draft, indent=2, sort_keys=True, allow_nan=False))
        else:
            print(json.dumps(_release(args), indent=2, sort_keys=True, allow_nan=False))
        return 0
    except AuthorizationError as error:
        print(f"publication refused: {error}", file=sys.stderr)
        return 3
    except (PersistenceConflict, OptimisticVersionConflict) as error:
        print(f"publication refused: {error}", file=sys.stderr)
        return 4
    except PersistenceUnavailable as error:
        print(f"publication refused: {error}", file=sys.stderr)
        return 5
    except (OSError, ValueError, TypeError, KeyError, ServiceError) as error:
        print(f"publication refused: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
