"""Seed the local rehearsal source environment with labelled synthetic recovery data.

Creates a dedicated PostgreSQL database (migrated exactly as CI does), three dedicated buckets
and a publication store, then writes 11 strata x 2 samples = 22 scorecards. Each scorecard is
produced by the real pure scorer from the hand-constructed fixtures in tests/scoring_support.py
(the same fixtures E2E-24 uses); its archive bundle is a verified content-addressed internal
artifact, and its evaluation, attempt and run rows form the real foreign-key chain. One signed
exploratory release whose projection is computed from those scorecards is published.

Everything is labelled ``synthetic_internal``. Nothing here is a benchmark result.

    uv run --offline --locked --all-packages python scripts/seed_ops_rehearsal.py --replace
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tests"))

import scoring_support as fixtures  # noqa: E402
import yaml  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.serialization import (  # noqa: E402
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from polycodebench_core.canonical import canonical_json_bytes  # noqa: E402
from polycodebench_core.models import ScoreDimension as D  # noqa: E402
from polycodebench_operations.migrations import (  # noqa: E402
    PERSISTENCE,
    alembic,
    load_policy,
    run_sql_file,
)
from polycodebench_operations.rehearsal_data import (  # noqa: E402
    BUNDLE_MEDIA_TYPE,
    ScorecardRow,
    bundle_bytes,
    rehearsal_content,
    rehearsal_projection,
)
from polycodebench_persistence.artifacts import ArtifactRepository  # noqa: E402
from polycodebench_persistence.models import (  # noqa: E402
    artifact_quota,
    attempt,
    campaign,
    config_document,
    evaluation,
    model_revision,
    run,
    scorecard,
    task,
    task_set,
    task_set_member,
    task_version,
)
from polycodebench_persistence.object_store import S3ArtifactStore  # noqa: E402
from polycodebench_publication.keyring import Keyring  # noqa: E402
from polycodebench_publication.releases import (  # noqa: E402
    REQUIRED_CHECKS,
    ReleasePrincipal,
    ReleaseStore,
    SigningKey,
    ValidationEvidence,
    digest,
)
from polycodebench_scoring.loader import (  # noqa: E402
    archive_document,
    load_evidence_ownership,
    load_scoring_policy,
)
from polycodebench_scoring.scorer import score_evaluation  # noqa: E402
from sqlalchemy import create_engine, insert, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

DOMAIN = "ops-rehearsal"
PARTIAL = (D.CODE_QUALITY, D.ROBUSTNESS)
STRATA: dict[str, tuple[tuple[D, ...], dict[str, Any]]] = {
    "pass-golden": (fixtures.ALL_QUALITY, {}),
    "pass-high-quality": (
        fixtures.ALL_QUALITY,
        {"code_quality_bp": 9800, "idiomatic_bp": 9600, "robustness_bp": 9400},
    ),
    "pass-low-quality": (
        fixtures.ALL_QUALITY,
        {"code_quality_bp": 2500, "idiomatic_bp": 3000, "robustness_bp": 2000},
    ),
    "pass-security-high": (
        fixtures.ALL_QUALITY,
        {"issues": (fixtures.security_issue("sql-injection"),)},
    ),
    "pass-security-low": (
        fixtures.ALL_QUALITY,
        {
            "issues": (fixtures.security_issue("weak-hash", severity="low"),),
            "code_quality_bp": 7000,
        },
    ),
    "pass-efficiency-60": (
        fixtures.ALL_QUALITY,
        {"efficiency_value": fixtures.efficiency("60.000000")},
    ),
    "pass-partial-applicability": (PARTIAL, {}),
    "fail-gate-full": (fixtures.ALL_QUALITY, {"gate_status": "fail"}),
    "fail-gate-partial": (PARTIAL, {"gate_status": "fail"}),
    "unknown-gate": (fixtures.ALL_QUALITY, {"gate_status": "unknown"}),
    "incomplete-analyzer": (
        fixtures.ALL_QUALITY,
        {"required_evidence": (fixtures.analyzer(status="incomplete"),)},
    ),
}
SAMPLES = 2


def _sha(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


class Seeder:
    def __init__(self, config: dict[str, Any], replace: bool) -> None:
        self.source = config["source"]
        self.replace = replace
        access_key_name = self.source["object_store_access_key_env"]
        secret_key_name = self.source["object_store_secret_key_env"]
        access_key = os.environ.get(access_key_name)
        secret_key = os.environ.get(secret_key_name)
        if not access_key or not secret_key:
            raise SystemExit(
                "local object-store credentials are required; load the ignored .env first"
            )
        os.environ["AWS_ACCESS_KEY_ID"] = access_key
        os.environ["AWS_SECRET_ACCESS_KEY"] = secret_key
        os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
        admin_url_name = self.source["admin_database_url_env"]
        self.admin_database_url = os.environ.get(admin_url_name)
        if not self.admin_database_url:
            raise SystemExit(f"{admin_url_name} is required; load the ignored local .env first")
        self.artifacts: ArtifactRepository | None = None
        self.store = S3ArtifactStore(
            endpoint_url=self.source["object_store_endpoint"], buckets=self.source["buckets"]
        )

    # -- database
    def create_database(self) -> str:
        admin = make_url(self.admin_database_url).set(drivername="postgresql+psycopg")
        name = self.source["database"]
        engine = create_engine(admin, poolclass=NullPool, isolation_level="AUTOCOMMIT")
        with engine.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}
            ).first()
            if exists and not self.replace:
                raise SystemExit(f"{name} exists; pass --replace to rebuild the rehearsal source")
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        engine.dispose()
        url = admin.set(database=name).render_as_string(hide_password=False)
        run_sql_file(url, PERSISTENCE / "sql" / "provision_roles.sql")
        # The released schema revision, not "head": the source must look like what a deployed
        # environment holds, and in-flight unreleased revisions must not leak into it.
        result = alembic(url, "upgrade", load_policy()["released_revision"])
        if result.returncode != 0:
            raise SystemExit("alembic upgrade failed: " + result.stderr[-400:])
        run_sql_file(url, PERSISTENCE / "sql" / "grant_permissions.sql")
        return url

    # -- objects
    def reset_buckets(self) -> None:
        self.store.ensure_buckets()
        for visibility in ("hidden", "internal", "public"):
            keys = [key for key, _modified in self.store.list_objects(visibility)]
            if keys and not self.replace:
                raise SystemExit("rehearsal buckets are not empty; pass --replace")
            for key in keys:
                self.store.delete(visibility, key)

    def put(self, connection: Any, visibility: str, body: bytes, media_type: str) -> uuid.UUID:
        """Store through the real upload path: reserve, upload, verify-and-finalize.

        The repository commits on its own connection, so the caller's open transaction can
        reference the verified artifact immediately afterwards.
        """
        del connection
        assert self.artifacts is not None
        owner = "ops-rehearsal-seed"
        upload_id = self.artifacts.begin_upload(
            owner=owner,
            visibility=visibility,
            encryption_domain=DOMAIN,
            expected_digest=_sha(body),
            expected_size=len(body),
            media_type=media_type,
        )
        self.artifacts.upload(upload_id=upload_id, owner=owner, body=body)
        return self.artifacts.finalize(upload_id=upload_id, owner=owner)

    def prepare_artifacts(self, url: str) -> None:
        engine = create_engine(url, poolclass=NullPool)
        with engine.begin() as connection:
            for visibility in ("hidden", "internal", "public"):
                connection.execute(
                    insert(artifact_quota).values(
                        visibility=visibility,
                        encryption_domain=DOMAIN,
                        max_bytes=256 * 1024 * 1024,
                        used_bytes=0,
                        reserved_bytes=0,
                    )
                )
        self.artifact_engine = engine
        self.artifacts = ArtifactRepository(engine, self.store, max_upload_bytes=8 * 1024 * 1024)

    def seed(self, url: str) -> list[ScorecardRow]:
        policy = load_scoring_policy(fixtures.POLICY_PATH)
        ownership = load_evidence_ownership(fixtures.OWNERSHIP_PATH)
        profile = fixtures.python_profile()
        engine = create_engine(url, poolclass=NullPool)
        rows: list[ScorecardRow] = []
        with engine.begin() as connection:
            put_json = lambda vis, doc, media="application/json": self.put(  # noqa: E731
                connection, vis, canonical_json_bytes(doc), media
            )
            task_manifest = put_json(
                "internal", {"task": "synthetic-scoring-task", "fixture": True}
            )
            visible = put_json("internal", {"statement": "synthetic visible bundle"})
            hidden = put_json("hidden", {"oracle": "synthetic hidden bundle - rehearsal only"})
            ids = {
                name: uuid.uuid4()
                for name in (
                    "task",
                    "task_version",
                    "task_set",
                    "run_config",
                    "capabilities",
                    "policy",
                    "model",
                    "campaign",
                    "run",
                )
            }
            task_set_digest = _sha(str(ids["task_set"]).encode())
            connection.execute(
                insert(task).values(
                    id=ids["task"],
                    slug="ops-rehearsal-synthetic",
                    family="unit",
                    source_identity="fixture",
                    primary_language="python",
                )
            )
            connection.execute(
                insert(task_version).values(
                    id=ids["task_version"],
                    task_id=ids["task"],
                    version=1,
                    digest=fixtures.DIGEST_A,
                    manifest_artifact_id=task_manifest,
                    visible_artifact_id=visible,
                    hidden_artifact_id=hidden,
                    language="python",
                    family="unit",
                    cluster_id="rehearsal-cluster",
                    stratum_id="rehearsal",
                    schema_version=1,
                    document={"task": "synthetic-scoring-task"},
                )
            )
            connection.execute(
                insert(task_set).values(
                    id=ids["task_set"],
                    name="ops-rehearsal",
                    version=1,
                    digest=task_set_digest,
                    split="test",
                    status="draft",
                    manifest_artifact_id=task_manifest,
                    row_version=0,
                )
            )
            connection.execute(
                insert(task_set_member).values(
                    task_set_id=ids["task_set"],
                    task_version_id=ids["task_version"],
                    stratum_id="rehearsal",
                    sampling_weight_bp=10000,
                )
            )
            connection.execute(
                text(
                    "UPDATE task_set SET status='frozen', frozen_at=now(), row_version=1 "
                    "WHERE id=:id"
                ),
                {"id": ids["task_set"]},
            )
            for name, kind, document in (
                (
                    "run_config",
                    "run_config",
                    {
                        "sampling": {
                            "task_set_digest": task_set_digest,
                            "samples_per_task": SAMPLES * len(STRATA),
                            "master_seed": "1",
                        }
                    },
                ),
                ("capabilities", "capabilities", {"supports_tools": False}),
                ("policy", "scoring_policy", {"policy_digest": policy.content_digest()}),
            ):
                canonical = put_json("internal", document)
                connection.execute(
                    insert(config_document).values(
                        id=ids[name],
                        kind=kind,
                        version_label="ops-rehearsal-v1",
                        digest=_sha(canonical_json_bytes(document) + name.encode()),
                        canonical_artifact_id=canonical,
                        schema_version=1,
                        document=document,
                    )
                )
            connection.execute(
                insert(model_revision).values(
                    id=ids["model"],
                    provider="fixture-ops-rehearsal",
                    name="synthetic",
                    immutable_revision="rev-1",
                    capabilities_config_id=ids["capabilities"],
                )
            )
            connection.execute(
                insert(campaign).values(
                    id=ids["campaign"],
                    name="ops-rehearsal",
                    status="completed",
                    owner_subject="ops-rehearsal",
                    row_version=0,
                )
            )
            connection.execute(
                insert(run).values(
                    id=ids["run"],
                    campaign_id=ids["campaign"],
                    config_document_id=ids["run_config"],
                    task_set_id=ids["task_set"],
                    model_revision_id=ids["model"],
                    status="completed",
                    created_by="ops-rehearsal",
                    row_version=0,
                )
            )

            sample_index = 0
            for stratum, (applicable, overrides) in STRATA.items():
                for sample in range(SAMPLES):
                    candidate_id = str(
                        uuid.UUID(
                            bytes=hashlib.sha256(f"pcb-ops/{stratum}/{sample}".encode()).digest()[
                                :16
                            ],
                            version=4,
                        )
                    )
                    evidence = fixtures.replace(
                        fixtures.manifest(applicable=applicable, **overrides),
                        invocation=fixtures.invocation(candidate_id=candidate_id).model_dump(
                            mode="json"
                        ),
                    )
                    frozen = fixtures.frozen_task(applicable)
                    outcome = score_evaluation(
                        frozen, policy, evidence, ownership=ownership, profile=profile
                    )
                    documents = {
                        "policy": archive_document(policy),
                        "ownership": archive_document(ownership),
                        "task": archive_document(frozen),
                        "manifest": archive_document(evidence),
                        "profile": archive_document(profile),
                        "outcome": archive_document(outcome),
                    }
                    body = bundle_bytes(stratum, documents, "synthetic_internal")
                    bundle_id = self.put(connection, "internal", body, BUNDLE_MEDIA_TYPE)
                    manifest_id = put_json("internal", documents["manifest"])
                    attempt_id, evaluation_id, scorecard_id = (
                        uuid.uuid4(),
                        uuid.uuid4(),
                        uuid.uuid4(),
                    )
                    gate = str(outcome.scorecard.gate)
                    connection.execute(
                        insert(attempt).values(
                            id=attempt_id,
                            run_id=ids["run"],
                            task_version_id=ids["task_version"],
                            sample_index=sample_index,
                            seed=sample_index,
                            state="completed",
                            row_version=0,
                        )
                    )
                    connection.execute(
                        insert(evaluation).values(
                            id=evaluation_id,
                            attempt_id=attempt_id,
                            policy_config_id=ids["policy"],
                            oracle_digest=fixtures.DIGEST_C,
                            state="failed" if gate == "unknown" else "ready",
                            gate=gate,
                            evidence_manifest_id=manifest_id,
                            row_version=0,
                        )
                    )
                    total = outcome.scorecard.total_score
                    composite = (
                        None
                        if total is None
                        else (Decimal(str(total)) / Decimal(100)).quantize(Decimal("0.00000001"))
                    )
                    connection.execute(
                        insert(scorecard).values(
                            id=scorecard_id,
                            evaluation_id=evaluation_id,
                            scorer_digest=fixtures.SCORER_DIGEST,
                            evidence_digest=_sha(canonical_json_bytes(documents["manifest"])),
                            artifact_id=bundle_id,
                            gate=gate,
                            composite=composite,
                        )
                    )
                    rows.append(ScorecardRow(str(scorecard_id), stratum, gate, composite))
                    sample_index += 1
        engine.dispose()
        return rows

    # -- publication
    def publish(self, rows: list[ScorecardRow]) -> dict[str, Any]:
        store_path = REPO_ROOT / self.source["release_store_path"]
        keyring_path = REPO_ROOT / self.source["keyring_path"]
        key_dir = REPO_ROOT / self.source["signing_key_dir"]
        for path in (store_path, keyring_path):
            if path.exists():
                if not self.replace:
                    raise SystemExit(f"{path} exists; pass --replace")
                path.unlink()
        store_path.parent.mkdir(parents=True, exist_ok=True)
        key_dir.mkdir(parents=True, exist_ok=True)
        signer = SigningKey("local-rehearsal-ed25519-1", Ed25519PrivateKey.generate())
        (key_dir / f"{signer.key_id}.pem").write_bytes(
            signer.private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        )
        keyring = Keyring().rotate(signer, at="2026-10-04T00:00:00Z")
        keyring_path.write_text(json.dumps(keyring.document(), indent=2), encoding="utf-8")
        cohort = {"rehearsal": "ops-restore-v1", "strata": sorted(STRATA), "samples": SAMPLES}
        content = {**rehearsal_content(rows), "cohort": cohort}
        projection = rehearsal_projection(rows, cohort)
        store = ReleaseStore(store_path)
        principal = ReleasePrincipal(
            "ops-rehearsal", frozenset({"curator", "reviewer", "publisher"})
        )
        doc = store.draft(principal, content, projection, "rehearsal-draft")
        receipts = tuple(
            ValidationEvidence(
                check,
                doc["content_digest"],
                digest({"receipt": check}),
                digest({"receipt": check}),
                "internal:ops-rehearsal/" + check,
            )
            for check in sorted(REQUIRED_CHECKS)
        )
        doc = store.validate(principal, doc["id"], receipts, doc["version"], "rehearsal-validate")
        doc = store.review(
            principal, doc["id"], "Synthetic rehearsal release", doc["version"], "rehearsal-review"
        )
        doc = store.approve(
            principal,
            doc["id"],
            "Approve synthetic rehearsal release",
            doc["version"],
            "rehearsal-approve",
        )
        doc = store.publish(principal, doc["id"], signer, 0, doc["version"], "rehearsal-publish")
        return {
            "release_id": doc["id"],
            "projection_digest": doc["manifest"]["projection_digest"],
            "key_id": signer.key_id,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--config", type=Path, default=REPO_ROOT / "config" / "operations" / "rehearsal-local.yaml"
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="drop and rebuild the dedicated rehearsal database/buckets/store",
    )
    args = parser.parse_args(argv)
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    seeder = Seeder(config, args.replace)
    url = seeder.create_database()
    seeder.reset_buckets()
    seeder.prepare_artifacts(url)
    rows = seeder.seed(url)
    release = seeder.publish(rows)
    print(
        json.dumps(
            {
                "scorecards": len(rows),
                "strata": len({row.stratum for row in rows}),
                "fixture_kind": "synthetic_internal",
                **release,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
