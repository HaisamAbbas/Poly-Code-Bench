"""Recompute the language-profile source digest recorded in the scoring policy.

The scoring policy pins ``config/languages/profiles-v1.yaml`` by digest, so editing a language
profile is a *policy* change that must show up as a new policy version. This script recomputes the
digest from the file's canonical bytes and rewrites the one line in the policy that records it, so
the two can never drift apart silently.

Usage::

    python scripts/hash_scoring_profile_source.py            # verify
    python scripts/hash_scoring_profile_source.py --write    # verify and update the policy
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from polycodebench_core.canonical import sha256_bytes

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "config" / "languages" / "profiles-v1.yaml"
POLICY = ROOT / "config" / "scoring" / "pilot-v1.yaml"
FIELD = re.compile(r"^(\s*profile_source_digest:\s*)(sha256:[0-9a-f]{64}|\S+)\s*$", re.MULTILINE)


def profile_source_digest() -> str:
    """The sha256 of the profile source file's bytes.

    The file's bytes, not its parsed form: the weights are written as percentages that include
    fractional values (``31.25``), which are outside the canonical JSON domain by design. Hashing
    the bytes keeps the check reproducible with nothing but sha256sum, and any edit to the file -
    a weight change or a reformat - is honestly reported as a change to a scoring input.
    """
    return sha256_bytes(PROFILES.read_bytes())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hash_scoring_profile_source")
    parser.add_argument("--write", action="store_true", help="update the policy with the digest")
    args = parser.parse_args(argv)

    digest = profile_source_digest()
    text = POLICY.read_text(encoding="utf-8")
    match = FIELD.search(text)
    if match is None:
        print(f"{POLICY} has no profile_source_digest field", file=sys.stderr)
        return 2
    current = match.group(2)
    if args.write and current != digest:
        POLICY.write_text(
            FIELD.sub(lambda found: f"{found.group(1)}{digest}", text, count=1),
            encoding="utf-8",
            newline="\n",
        )
        print(f"updated {POLICY} profile_source_digest to {digest}")
        return 0
    if current != digest:
        print(
            f"profile source digest drifted: policy records {current}, file is {digest}\n"
            "run with --write if the profile change is intended",
            file=sys.stderr,
        )
        return 1
    print(f"profile source digest matches: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
