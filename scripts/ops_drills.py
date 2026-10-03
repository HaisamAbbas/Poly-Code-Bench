"""Execute the local operational drills through the real CLIs and record evidence (E2E-43 local).

Drills (each runs the exact commands its runbook documents):

* ``orphan``      - a real development guest is created by the sandbox driver, its supervisor
                    is lost, and ``pcb-ops orphans sweep`` must reclaim it after TTL; timing from
                    expiry to verified reclamation is measured against the 10-minute target.
* ``publication`` - signing-key rotation, a correction successor, release withdrawal with a
                    preserved historical notice, and the compromised-key revocation path, all via
                    ``pcb-release`` and ``pcb-ops keys``.

Every command line, exit code and the fields that prove the expected state are written to the
evidence file. Data is synthetic and labelled; nothing is published outside the local store.

    uv run --offline --locked --all-packages python scripts/ops_drills.py --evidence <file>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BIN = Path(sys.executable).parent
IMAGE = (
    "docker.io/library/python:3.12-slim@sha256:"
    "44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b"
)
DIGEST = "sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b"
OWNER = [
    "--subject",
    "drill-operator",
    "--role",
    "curator",
    "--role",
    "reviewer",
    "--role",
    "publisher",
]


class Drill:
    def __init__(self, name: str) -> None:
        self.name = name
        self.commands: list[dict[str, Any]] = []
        self.assertions: list[dict[str, Any]] = []

    def run(self, tool: str, *arguments: str | Path, expect: int = 0) -> Any:
        argv = [str(BIN / tool), *map(str, arguments)]
        started = time.perf_counter()
        result = subprocess.run(argv, capture_output=True, text=True, cwd=REPO_ROOT, check=False)
        shown = [
            tool,
            *(
                str(a).replace(str(REPO_ROOT) + "\\", "").replace(str(REPO_ROOT) + "/", "")
                for a in arguments
            ),
        ]
        self.commands.append(
            {
                "command": " ".join(shown),
                "exit": result.returncode,
                "expected_exit": expect,
                "seconds": round(time.perf_counter() - started, 3),
            }
        )
        if result.returncode != expect:
            raise RuntimeError(
                f"{' '.join(shown)} exited {result.returncode}: {result.stderr[-400:]}"
            )
        try:
            return json.loads(result.stdout) if result.stdout.strip() else None
        except json.JSONDecodeError:
            return result.stdout

    def check(self, claim: str, passed: bool, **observed: Any) -> None:
        self.assertions.append({"claim": claim, "passed": bool(passed), **observed})
        if not passed:
            raise AssertionError(claim)

    def report(self, started: float, error: str | None) -> dict[str, Any]:
        return {
            "drill": self.name,
            "passed": error is None and all(item["passed"] for item in self.assertions),
            "error": error,
            "seconds": round(time.perf_counter() - started, 3),
            "commands": self.commands,
            "assertions": self.assertions,
        }


def orphan_drill(workspace: Path) -> dict[str, Any]:
    from polycodebench_runner.contracts import SandboxSpec
    from polycodebench_runner.provider import LocalDockerSandboxProvider

    drill = Drill("orphan-cleanup")
    started = time.perf_counter()
    error = None
    provider_id = f"drill{int(time.time())}"
    try:
        provider = LocalDockerSandboxProvider(
            allowed_images={IMAGE: DIGEST},
            state_dir=workspace / "sandbox-state",
            provider_id=provider_id,
        )
        ttl = 30
        handle = asyncio.run(
            provider.create(
                SandboxSpec(
                    stage_id="ops-orphan-drill",
                    fence=1,
                    lane="solve",
                    image=IMAGE,
                    image_digest=DIGEST,
                    cpu_millis=500,
                    memory_bytes=128 * 1024**2,
                    disk_bytes=32 * 1024**2,
                    pids_limit=16,
                    timeout_seconds=5,
                    ttl_seconds=ttl,
                )
            )
        )
        drill.check(
            "driver created a labelled development guest",
            bool(handle.resource_id),
            lane=handle.lane,
            isolation_tier=handle.isolation_tier,
        )
        del provider  # supervisor loss: nothing in this process will clean the guest up
        expires = handle.expires_at_epoch
        early = drill.run(
            "pcb-ops.exe",
            "orphans",
            "sweep",
            "--provider",
            "local",
            "--provider-id",
            provider_id,
            "--image",
            IMAGE,
        )
        drill.check(
            "a live (unexpired) guest is never reclaimed",
            early["reclaimed"] == 0 and early["expired_before"] == 0,
        )
        while time.time() <= expires:
            time.sleep(0.5)
        expired_at = time.time()
        swept = drill.run(
            "pcb-ops.exe",
            "orphans",
            "sweep",
            "--provider",
            "local",
            "--provider-id",
            provider_id,
            "--image",
            IMAGE,
        )
        reclaimed_after = time.time() - expires
        drill.check(
            "expired orphan reclaimed and verified gone",
            swept["reclaimed"] == 1 and swept["clean"],
            sweep=swept,
        )
        drill.check(
            "reclaimed within the 10-minute target (measured, local Docker)",
            reclaimed_after < 600,
            seconds_after_expiry=round(reclaimed_after, 3),
            sweep_seconds=swept["seconds"],
            waited_from=round(expired_at - expires, 3),
        )
        remaining = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"label=pcb.provider={provider_id}"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.split()
        drill.check("no container with the drill provider label remains", not remaining)
    except Exception as exc:  # noqa: BLE001 - recorded in evidence
        error = f"{type(exc).__name__}: {exc}"[:500]
    return drill.report(started, error)


def _receipts(content_digest: str, path: Path) -> Path:
    from polycodebench_publication.releases import REQUIRED_CHECKS, digest

    path.write_text(
        json.dumps(
            [
                {
                    "check": check,
                    "subject_digest": content_digest,
                    "expected_digest": digest({"receipt": check}),
                    "observed_digest": digest({"receipt": check}),
                    "reference": f"internal:ops-drill/{check}",
                }
                for check in sorted(REQUIRED_CHECKS)
            ]
        ),
        encoding="utf-8",
    )
    return path


def _publish(
    drill: Drill,
    store: Path,
    workspace: Path,
    label: str,
    key: Path,
    key_id: str,
    generation: int,
    predecessor: str | None = None,
) -> dict[str, Any]:
    from polycodebench_publication.releases import digest

    content = workspace / f"{label}-content.json"
    projection = workspace / f"{label}-projection.json"
    content.write_text(json.dumps({"kind": "ops_drill_release", "label": label}), "utf-8")
    projection.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "fixture_kind": "synthetic_internal",
                "scope": "exploratory",
                "cohort_digest": digest({"drill": label}),
                "limitations": ["Synthetic operations drill release; not a benchmark result."],
                "metrics": [
                    {
                        "metric_id": "drill.placeholder",
                        "value": "0.5",
                        "interval_low": None,
                        "interval_high": None,
                        "coverage": "1",
                        "conditional_on_pass": False,
                    }
                ],
            }
        ),
        "utf-8",
    )
    create_args: list[str | Path] = [
        "create",
        "--store",
        store,
        *OWNER,
        "--request-id",
        f"{label}-create",
        "--content",
        content,
        "--projection",
        projection,
    ]
    if predecessor:
        create_args += [
            "--predecessor",
            predecessor,
            "--correction-reason",
            "Drill correction: successor supersedes the predecessor",
        ]
    doc = drill.run("pcb-release.exe", *create_args)
    rid = doc["id"]
    doc = drill.run(
        "pcb-release.exe",
        "validate",
        "--store",
        store,
        *OWNER,
        "--request-id",
        f"{label}-validate",
        "--release-id",
        rid,
        "--expected-version",
        str(doc["version"]),
        "--evidence",
        _receipts(doc["content_digest"], workspace / f"{label}-receipts.json"),
    )
    doc = drill.run(
        "pcb-release.exe",
        "review",
        "--store",
        store,
        *OWNER,
        "--request-id",
        f"{label}-review",
        "--release-id",
        rid,
        "--expected-version",
        str(doc["version"]),
        "--reason",
        "Drill review",
    )
    doc = drill.run(
        "pcb-release.exe",
        "approve",
        "--store",
        store,
        *OWNER,
        "--request-id",
        f"{label}-approve",
        "--release-id",
        rid,
        "--expected-version",
        str(doc["version"]),
        "--reason",
        "Drill approval of exact digest",
    )
    doc = drill.run(
        "pcb-release.exe",
        "publish",
        "--store",
        store,
        *OWNER,
        "--request-id",
        f"{label}-publish",
        "--release-id",
        rid,
        "--expected-version",
        str(doc["version"]),
        "--expected-generation",
        str(generation),
        "--key-file",
        key,
        "--key-id",
        key_id,
    )
    (workspace / f"{label}-manifest.json").write_text(json.dumps(doc["manifest"]), "utf-8")
    return doc


def publication_drill(workspace: Path) -> dict[str, Any]:
    from polycodebench_publication.releases import ReleaseStore

    drill = Drill("key-rotation-correction-withdrawal")
    started = time.perf_counter()
    error = None
    store = workspace / "releases.db"
    keyring = workspace / "keyring.json"
    try:
        drill.run(
            "pcb-ops.exe",
            "keys",
            "rotate",
            "--keyring",
            keyring,
            "--new-key-id",
            "drill-key-1",
            "--private-key-out",
            workspace / "keys" / "drill-key-1.pem",
        )
        first = _publish(
            drill,
            store,
            workspace,
            "r1",
            workspace / "keys" / "drill-key-1.pem",
            "drill-key-1",
            generation=0,
        )
        rotated = drill.run(
            "pcb-ops.exe",
            "keys",
            "rotate",
            "--keyring",
            keyring,
            "--new-key-id",
            "drill-key-2",
            "--private-key-out",
            workspace / "keys" / "drill-key-2.pem",
        )
        states = {item["key_id"]: item["state"] for item in rotated["keys"]}
        drill.check(
            "rotation retires the old key and activates the new one",
            states == {"drill-key-1": "retired", "drill-key-2": "active"},
            keys=states,
        )
        second = _publish(
            drill,
            store,
            workspace,
            "r2",
            workspace / "keys" / "drill-key-2.pem",
            "drill-key-2",
            generation=1,
            predecessor=first["id"],
        )
        old = drill.run(
            "pcb-ops.exe",
            "keys",
            "verify",
            "--keyring",
            keyring,
            "--manifest",
            workspace / "r1-manifest.json",
        )
        new = drill.run(
            "pcb-ops.exe",
            "keys",
            "verify",
            "--keyring",
            keyring,
            "--manifest",
            workspace / "r2-manifest.json",
        )
        drill.check(
            "release signed before rotation still verifies with the retained key",
            old == {"detail": "retired", "valid": True},
        )
        drill.check(
            "release signed after rotation verifies with the active key",
            new == {"detail": "active", "valid": True},
        )
        drill.check(
            "correction successor links its predecessor",
            second["manifest"]["predecessor"] == first["id"],
        )
        drill.run(
            "pcb-release.exe",
            "withdraw",
            "--store",
            store,
            *OWNER,
            "--request-id",
            "r1-withdraw",
            "--release-id",
            first["id"],
            "--expected-version",
            str(first["version"]),
            "--expected-generation",
            "2",
            "--reason",
            "Drill withdrawal: superseded by correction",
        )
        releases = ReleaseStore(store)
        historical = releases.public(first["id"])
        drill.check(
            "withdrawn release keeps its URL, manifest and a withdrawal notice",
            historical["manifest"] == first["manifest"]
            and historical["withdrawal"]["reason"].startswith("Drill withdrawal"),
        )
        drill.check(
            "board pointer still references the published successor",
            releases.current()["release_id"] == second["id"],
            pointer=releases.current(),
        )
        drill.run(
            "pcb-release.exe",
            "withdraw",
            "--store",
            store,
            *OWNER,
            "--request-id",
            "r2-withdraw",
            "--release-id",
            second["id"],
            "--expected-version",
            str(second["version"]),
            "--expected-generation",
            "1",
            "--reason",
            "Drill: stale generation must be refused",
            expect=4,
        )
        drill.check("withdrawal with a stale pointer generation is refused (CAS)", True)
        drill.run(
            "pcb-release.exe",
            "withdraw",
            "--store",
            store,
            *OWNER,
            "--request-id",
            "r2-withdraw-current",
            "--release-id",
            second["id"],
            "--expected-version",
            str(second["version"]),
            "--expected-generation",
            "2",
            "--reason",
            "Drill withdrawal of the current release",
        )
        drill.check(
            "withdrawing the current release clears the pointer (never dangling)",
            releases.current()["release_id"] is None,
            pointer=releases.current(),
        )
        drill.run(
            "pcb-ops.exe",
            "keys",
            "revoke",
            "--keyring",
            keyring,
            "--key-id",
            "drill-key-1",
            "--reason",
            "Drill: simulated key compromise",
        )
        revoked = drill.run(
            "pcb-ops.exe",
            "keys",
            "verify",
            "--keyring",
            keyring,
            "--manifest",
            workspace / "r1-manifest.json",
            expect=1,
        )
        drill.check(
            "a revoked key fails verification closed",
            revoked == {"detail": "signing key revoked", "valid": False},
        )
        audit = [item["action"] for item in releases.audit()]
        drill.check(
            "audit trail records every publication action",
            audit.count("publish") == 2 and audit.count("withdraw") == 2,
            audit=audit,
        )
    except Exception as exc:  # noqa: BLE001 - recorded in evidence
        error = f"{type(exc).__name__}: {exc}"[:500]
    return drill.report(started, error)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--only", choices=("orphan", "publication"), action="append")
    args = parser.parse_args(argv)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    workspace = REPO_ROOT / ".local" / "ops-rehearsal" / f"drills-{stamp}"
    workspace.mkdir(parents=True)
    selected = args.only or ["orphan", "publication"]
    results = []
    if "orphan" in selected:
        results.append(orphan_drill(workspace / "orphan"))
    if "publication" in selected:
        (workspace / "publication").mkdir()
        results.append(publication_drill(workspace / "publication"))
    evidence = {
        "schema_version": 1,
        "environment_class": "local trusted development (Docker); not staging",
        "fixture_kind": "synthetic_internal",
        "started": stamp,
        "drills": results,
        "passed": all(item["passed"] for item in results),
    }
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    shutil.rmtree(workspace / "orphan" / "sandbox-state", ignore_errors=True)
    print(json.dumps({item["drill"]: item["passed"] for item in results}))
    return 0 if evidence["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
