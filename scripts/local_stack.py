"""Prepare and initialize the loopback-only self-hosted development stack."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
REALM_TEMPLATE = ROOT / "config" / "keycloak" / "polycodebench-local-realm.json"
REALM_IMPORT_DIR = ROOT / ".cache" / "keycloak-import"
IDENTITY_PATH = ROOT / ".cache" / "polycodebench-local-identities.json"
PUBLIC_RELEASE_STORE = ROOT / ".cache" / "polycodebench-local-verified-release-store.sqlite3"
PUBLIC_RELEASE_SIGNING_KEY = ROOT / ".cache" / "polycodebench-local-signing-key.pem"
PUBLIC_RELEASE_KEYRING = ROOT / ".cache" / "polycodebench-local-keyring.json"
LOCAL_DATABASE = "pcb_local_web_test"
LOCAL_OPS_DATABASE = "pcb_ops_rehearsal_source"
LOCAL_ADMIN_ROLE = "polycodebench"
LOCAL_API_ROLE = "pcb_local_api"
LOCAL_PUBLISHER_ROLE = "pcb_local_publisher"
LOCAL_WORKER_ROLE = "pcb_local_worker"
LOCAL_SCORER_ROLE = "pcb_local_scorer"
REQUIRED_ENV = {
    "KEYCLOAK_ADMIN_USERNAME",
    "KEYCLOAK_ADMIN_PASSWORD",
    "LOCAL_SUBMITTER_USERNAME",
    "LOCAL_SUBMITTER_EMAIL",
    "LOCAL_SUBMITTER_PASSWORD",
    "LOCAL_REVIEWER_TOKEN",
    "PCB_WEB_AUTH_SIGNING_KEY",
    "PCB_CURSOR_SIGNING_KEY",
    "PCB_LOCAL_API_PASSWORD",
    "PCB_LOCAL_WORKER_PASSWORD",
    "PCB_LOCAL_SCORER_PASSWORD",
    "PCB_WORKER_DATABASE_URL",
    "PCB_SCORER_DATABASE_URL",
    "PCB_DATABASE_URL",
    "PCB_PUBLISHER_DATABASE_URL",
    "PCB_MIGRATION_DATABASE_URL",
    "PCB_OPS_REHEARSAL_DATABASE_URL",
    "PCB_LOCAL_POSTGRES_PASSWORD",
    "PCB_LOCAL_S3_ACCESS_KEY",
    "PCB_LOCAL_S3_SECRET_KEY",
    "PCB_OBJECT_STORE_ENDPOINT",
    "PCB_BUCKET_HIDDEN",
    "PCB_BUCKET_INTERNAL",
    "PCB_BUCKET_PUBLIC",
    "PCB_PUBLIC_RELEASE_BACKEND",
    "PCB_PUBLICATION_TARGET",
    "PCB_RELEASE_STORE_PATH",
    "PCB_OIDC_ISSUER",
    "PCB_OIDC_CLIENT_ID",
    "PCB_OIDC_REDIRECT_URI",
    "PCB_WEB_ORIGIN",
    "PCB_PUBLIC_API_URL",
    "PCB_API_IDENTITY_FILE",
    "PCB_ENVIRONMENT",
    "PCB_SERVICE_IDENTITY",
    "PCB_LOCAL_WORKER_SETUP_ENABLED",
    "PCB_WORKER_DISPATCH_ENABLED",
    "PCB_LOCAL_SCORING_ENABLED",
}


def _local_admin_database_url(password: str, database: str) -> str:
    encoded_password = quote(password, safe="")
    return f"postgresql+psycopg://{LOCAL_ADMIN_ROLE}:{encoded_password}@127.0.0.1:55432/{database}"


def read_env(path: Path | None = None) -> dict[str, str]:
    values: dict[str, str] = {}
    environment_path = path or ENV_PATH
    lines = environment_path.read_text(encoding="utf-8").splitlines()
    for line_number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name, separator, value = stripped.partition("=")
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            raise ValueError(f"invalid local environment entry on line {line_number}")
        values[name] = value
    return values


def prepare() -> dict[str, str]:
    if ENV_PATH.exists():
        values = read_env()
        missing = sorted(REQUIRED_ENV - values.keys())
        generated_defaults = {
            "PCB_PUBLIC_RELEASE_BACKEND": "postgres",
            "PCB_PUBLICATION_TARGET": "local:board",
            "PCB_ENVIRONMENT": "dev",
            "PCB_SERVICE_IDENTITY": "polycodebench-local-development",
            "PCB_LOCAL_WORKER_SETUP_ENABLED": "false",
            "PCB_WORKER_DISPATCH_ENABLED": "false",
            "PCB_LOCAL_SCORING_ENABLED": "false",
            "PCB_OBJECT_STORE_ENDPOINT": "http://127.0.0.1:8333",
            "PCB_BUCKET_HIDDEN": "pcb-hidden-local",
            "PCB_BUCKET_INTERNAL": "pcb-internal-local",
            "PCB_BUCKET_PUBLIC": "pcb-public-local",
        }
        generated_secrets = {
            "PCB_LOCAL_POSTGRES_PASSWORD": secrets.token_urlsafe(32),
            "PCB_LOCAL_S3_ACCESS_KEY": secrets.token_urlsafe(24),
            "PCB_LOCAL_S3_SECRET_KEY": secrets.token_urlsafe(32),
            "PCB_LOCAL_WORKER_PASSWORD": secrets.token_urlsafe(32),
            "PCB_LOCAL_SCORER_PASSWORD": secrets.token_urlsafe(32),
        }
        allowed_generated = (
            set(generated_defaults)
            | set(generated_secrets)
            | {
                "PCB_PUBLISHER_DATABASE_URL",
                "PCB_OPS_REHEARSAL_DATABASE_URL",
                "PCB_WORKER_DATABASE_URL",
                "PCB_SCORER_DATABASE_URL",
            }
        )
        if missing and set(missing) <= allowed_generated:
            additions = {key: value for key, value in generated_defaults.items() if key in missing}
            additions.update(
                {key: value for key, value in generated_secrets.items() if key in missing}
            )
            if "PCB_LOCAL_WORKER_PASSWORD" in missing and "PCB_WORKER_DATABASE_URL" not in missing:
                worker_password = urlsplit(values["PCB_WORKER_DATABASE_URL"]).password
                if worker_password is None:
                    raise RuntimeError("existing local worker database URL is invalid")
                additions["PCB_LOCAL_WORKER_PASSWORD"] = unquote(worker_password)
            if "PCB_LOCAL_SCORER_PASSWORD" in missing and "PCB_SCORER_DATABASE_URL" not in missing:
                scorer_password = urlsplit(values["PCB_SCORER_DATABASE_URL"]).password
                if scorer_password is None:
                    raise RuntimeError("existing local scorer database URL is invalid")
                additions["PCB_LOCAL_SCORER_PASSWORD"] = unquote(scorer_password)
            if "PCB_PUBLISHER_DATABASE_URL" in missing:
                publisher_password = secrets.token_urlsafe(32)
                additions["PCB_PUBLISHER_DATABASE_URL"] = (
                    f"postgresql+psycopg://{LOCAL_PUBLISHER_ROLE}:{publisher_password}"
                    f"@127.0.0.1:55432/{LOCAL_DATABASE}"
                )
            if "PCB_OPS_REHEARSAL_DATABASE_URL" in missing:
                admin_password = additions.get(
                    "PCB_LOCAL_POSTGRES_PASSWORD", values.get("PCB_LOCAL_POSTGRES_PASSWORD")
                )
                if not admin_password:
                    raise RuntimeError("local PostgreSQL credential generation failed")
                additions["PCB_OPS_REHEARSAL_DATABASE_URL"] = _local_admin_database_url(
                    admin_password, LOCAL_OPS_DATABASE
                )
            if "PCB_WORKER_DATABASE_URL" in missing:
                worker_password = additions.get(
                    "PCB_LOCAL_WORKER_PASSWORD", values.get("PCB_LOCAL_WORKER_PASSWORD")
                )
                if not worker_password:
                    raise RuntimeError("local worker credential generation failed")
                additions["PCB_WORKER_DATABASE_URL"] = (
                    f"postgresql+psycopg://{LOCAL_WORKER_ROLE}:{quote(worker_password, safe='')}"
                    f"@127.0.0.1:55432/{LOCAL_DATABASE}"
                )
            if "PCB_SCORER_DATABASE_URL" in missing:
                scorer_password = additions.get(
                    "PCB_LOCAL_SCORER_PASSWORD", values.get("PCB_LOCAL_SCORER_PASSWORD")
                )
                if not scorer_password:
                    raise RuntimeError("local scorer credential generation failed")
                additions["PCB_SCORER_DATABASE_URL"] = (
                    f"postgresql+psycopg://{LOCAL_SCORER_ROLE}:{quote(scorer_password, safe='')}"
                    f"@127.0.0.1:55432/{LOCAL_DATABASE}"
                )
            with ENV_PATH.open("a", encoding="utf-8", newline="\n") as stream:
                stream.writelines(f"{name}={value}\n" for name, value in sorted(additions.items()))
            values.update(additions)
        elif missing:
            raise RuntimeError(
                ".env already exists and was not changed; add the missing local settings: "
                + ", ".join(missing)
            )
    else:
        api_password = secrets.token_urlsafe(32)
        publisher_password = secrets.token_urlsafe(32)
        local_postgres_password = secrets.token_urlsafe(32)
        local_worker_password = secrets.token_urlsafe(32)
        local_scorer_password = secrets.token_urlsafe(32)
        values = {
            "KEYCLOAK_ADMIN_USERNAME": "pcb-local-admin",
            "KEYCLOAK_ADMIN_PASSWORD": secrets.token_urlsafe(32),
            "LOCAL_SUBMITTER_USERNAME": "local-submitter",
            "LOCAL_SUBMITTER_EMAIL": "local-submitter@example.test",
            "LOCAL_SUBMITTER_PASSWORD": secrets.token_urlsafe(32),
            "LOCAL_REVIEWER_TOKEN": secrets.token_urlsafe(32),
            "PCB_WEB_AUTH_SIGNING_KEY": secrets.token_hex(32),
            "PCB_CURSOR_SIGNING_KEY": secrets.token_hex(32),
            "PCB_LOCAL_API_PASSWORD": api_password,
            "PCB_LOCAL_WORKER_PASSWORD": local_worker_password,
            "PCB_LOCAL_SCORER_PASSWORD": local_scorer_password,
            "PCB_DATABASE_URL": (
                f"postgresql+psycopg://{LOCAL_API_ROLE}:{api_password}"
                f"@127.0.0.1:55432/{LOCAL_DATABASE}"
            ),
            "PCB_PUBLISHER_DATABASE_URL": (
                f"postgresql+psycopg://{LOCAL_PUBLISHER_ROLE}:{publisher_password}"
                f"@127.0.0.1:55432/{LOCAL_DATABASE}"
            ),
            "PCB_MIGRATION_DATABASE_URL": _local_admin_database_url(
                local_postgres_password, LOCAL_DATABASE
            ),
            "PCB_WORKER_DATABASE_URL": (
                f"postgresql+psycopg://{LOCAL_WORKER_ROLE}:{quote(local_worker_password, safe='')}"
                f"@127.0.0.1:55432/{LOCAL_DATABASE}"
            ),
            "PCB_SCORER_DATABASE_URL": (
                f"postgresql+psycopg://{LOCAL_SCORER_ROLE}:{quote(local_scorer_password, safe='')}"
                f"@127.0.0.1:55432/{LOCAL_DATABASE}"
            ),
            "PCB_OPS_REHEARSAL_DATABASE_URL": _local_admin_database_url(
                local_postgres_password, LOCAL_OPS_DATABASE
            ),
            "PCB_LOCAL_POSTGRES_PASSWORD": local_postgres_password,
            "PCB_LOCAL_S3_ACCESS_KEY": secrets.token_urlsafe(24),
            "PCB_LOCAL_S3_SECRET_KEY": secrets.token_urlsafe(32),
            "PCB_OBJECT_STORE_ENDPOINT": "http://127.0.0.1:8333",
            "PCB_BUCKET_HIDDEN": "pcb-hidden-local",
            "PCB_BUCKET_INTERNAL": "pcb-internal-local",
            "PCB_BUCKET_PUBLIC": "pcb-public-local",
            "PCB_PUBLIC_RELEASE_BACKEND": "postgres",
            "PCB_PUBLICATION_TARGET": "local:board",
            "PCB_RELEASE_STORE_PATH": ".cache/polycodebench-local-release-store.sqlite3",
            "PCB_OIDC_ISSUER": "http://127.0.0.1:8080/realms/polycodebench-local",
            "PCB_OIDC_CLIENT_ID": "polycodebench-web",
            "PCB_OIDC_REDIRECT_URI": "http://127.0.0.1:3001/auth/callback",
            "PCB_WEB_ORIGIN": "http://127.0.0.1:3001",
            "PCB_PUBLIC_API_URL": "http://127.0.0.1:8010/v1",
            "PCB_API_IDENTITY_FILE": ".cache/polycodebench-local-identities.json",
            "PCB_ENVIRONMENT": "dev",
            "PCB_SERVICE_IDENTITY": "polycodebench-local-development",
            "PCB_LOCAL_WORKER_SETUP_ENABLED": "false",
            "PCB_WORKER_DISPATCH_ENABLED": "false",
            "PCB_LOCAL_SCORING_ENABLED": "false",
        }
        missing = sorted(REQUIRED_ENV - values.keys())
        if missing:
            raise RuntimeError("local environment generator omitted required fields")
        lines = [
            "# Local-only credentials; this ignored file is not for deployment.",
            *[f"{name}={value}" for name, value in sorted(values.items())],
            "",
        ]
        with ENV_PATH.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write("\n".join(lines))

    REALM_IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    realm = json.loads(REALM_TEMPLATE.read_text(encoding="utf-8"))
    realm["users"][0]["username"] = values["LOCAL_SUBMITTER_USERNAME"]
    realm["users"][0]["email"] = values["LOCAL_SUBMITTER_EMAIL"]
    realm["users"][0]["credentials"][0]["value"] = values["LOCAL_SUBMITTER_PASSWORD"]
    (REALM_IMPORT_DIR / "polycodebench-local-realm.json").write_text(
        json.dumps(realm, indent=2) + "\n", encoding="utf-8"
    )

    now = int(datetime.now(UTC).timestamp())
    reviewer_token = values["LOCAL_REVIEWER_TOKEN"]
    identity_document = {
        "schema_version": 1,
        "principals": [
            {
                "token_sha256": hashlib.sha256(reviewer_token.encode("utf-8")).hexdigest(),
                "principal": {
                    "subject_id": "local-reviewer",
                    "roles": ["reviewer"],
                    "mfa": True,
                    "email": "local-reviewer@example.test",
                    "email_verified": True,
                    "expires_at": int(
                        (datetime.fromtimestamp(now, UTC) + timedelta(days=30)).timestamp()
                    ),
                },
            }
        ],
    }
    IDENTITY_PATH.parent.mkdir(parents=True, exist_ok=True)
    IDENTITY_PATH.write_text(json.dumps(identity_document, indent=2) + "\n", encoding="utf-8")
    print("Prepared ignored .env and local Keycloak import files; secret values were not printed.")
    print("The local submitter username is available in .env; its password is generated there.")
    return values


def run_compose_psql(database: str, sql: str, *, args: tuple[str, ...] = ()) -> str:
    command = [
        "docker",
        "compose",
        "exec",
        "-T",
        "postgres",
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        LOCAL_ADMIN_ROLE,
        "-d",
        database,
        *args,
        "-f",
        "-",
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        input=sql,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        # Do not emit stdin or environment-derived values in diagnostics.
        raise RuntimeError(f"local PostgreSQL setup failed (exit {result.returncode})")
    if result.stdout:
        print(result.stdout, end="")
    return result.stdout


def bootstrap_database(values: dict[str, str] | None = None) -> None:
    values = values or read_env()
    db_name = LOCAL_DATABASE
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", db_name):
        raise ValueError("invalid local database name")
    check = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "postgres",
            "psql",
            "-At",
            "-U",
            LOCAL_ADMIN_ROLE,
            "-d",
            "polycodebench",
            "-c",
            f"SELECT count(*) FROM pg_database WHERE datname='{db_name}'",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if check.returncode:
        raise RuntimeError("could not inspect the local PostgreSQL database")
    if check.stdout.strip() == "0":
        created = subprocess.run(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "postgres",
                "createdb",
                "-U",
                LOCAL_ADMIN_ROLE,
                "-O",
                "polycodebench",
                db_name,
            ],
            cwd=ROOT,
            check=False,
        )
        if created.returncode:
            raise RuntimeError("could not create the isolated local website database")

    provision_sql = (ROOT / "packages/persistence/sql/provision_roles.sql").read_text(
        encoding="utf-8"
    )
    run_compose_psql(db_name, provision_sql)

    env = os.environ.copy()
    env["PCB_MIGRATION_DATABASE_URL"] = values["PCB_MIGRATION_DATABASE_URL"]
    migration = subprocess.run(
        [
            "uv",
            "run",
            "--locked",
            "--group",
            "dev",
            "python",
            "-m",
            "alembic",
            "-c",
            "packages/persistence/alembic.ini",
            "upgrade",
            "head",
        ],
        cwd=ROOT,
        env=env,
        check=False,
    )
    if migration.returncode:
        raise RuntimeError("local PostgreSQL migration failed")

    grants_sql = (ROOT / "packages/persistence/sql/grant_permissions.sql").read_text(
        encoding="utf-8"
    )
    run_compose_psql(db_name, grants_sql)

    password = values["PCB_LOCAL_API_PASSWORD"].replace("'", "''")
    admin_password = values["PCB_LOCAL_POSTGRES_PASSWORD"].replace("'", "''")
    publisher_password = urlsplit(values["PCB_PUBLISHER_DATABASE_URL"]).password
    if publisher_password is None:
        raise RuntimeError("local publisher database URL is invalid")
    publisher_password = unquote(publisher_password).replace("'", "''")
    worker_password = values["PCB_LOCAL_WORKER_PASSWORD"].replace("'", "''")
    worker_role = LOCAL_WORKER_ROLE
    worker_url = values["PCB_WORKER_DATABASE_URL"]
    worker_url_password = urlsplit(worker_url).password
    if (
        worker_url_password is None
        or unquote(worker_url_password) != values["PCB_LOCAL_WORKER_PASSWORD"]
    ):
        raise RuntimeError("local worker database URL does not match its generated credential")
    worker_url_password_sql = worker_password
    scorer_password = values["PCB_LOCAL_SCORER_PASSWORD"].replace("'", "''")
    scorer_url = values["PCB_SCORER_DATABASE_URL"]
    scorer_url_password = urlsplit(scorer_url).password
    if (
        scorer_url_password is None
        or unquote(scorer_url_password) != values["PCB_LOCAL_SCORER_PASSWORD"]
    ):
        raise RuntimeError("local scorer database URL does not match its generated credential")
    scorer_url_password_sql = scorer_password
    role_sql = f"""\
SELECT format('CREATE ROLE {LOCAL_API_ROLE} LOGIN PASSWORD %L', '{password}')
WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{LOCAL_API_ROLE}')
\\gexec
ALTER ROLE {LOCAL_API_ROLE} WITH LOGIN PASSWORD '{password}';
GRANT pcb_public_reader, pcb_submitter, pcb_submission_reviewer,
      pcb_submission_approver, pcb_endpoint_administrator TO {LOCAL_API_ROLE};
REVOKE pcb_reviewer, pcb_operator, pcb_administrator FROM {LOCAL_API_ROLE};
GRANT CONNECT ON DATABASE {db_name} TO {LOCAL_API_ROLE};
SELECT format('CREATE ROLE {LOCAL_PUBLISHER_ROLE} LOGIN PASSWORD %L', '{publisher_password}')
WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{LOCAL_PUBLISHER_ROLE}')
\\gexec
ALTER ROLE {LOCAL_PUBLISHER_ROLE} WITH LOGIN PASSWORD '{publisher_password}';
GRANT pcb_publisher TO {LOCAL_PUBLISHER_ROLE};
GRANT CONNECT ON DATABASE {db_name} TO {LOCAL_PUBLISHER_ROLE};
SELECT format('CREATE ROLE {worker_role} LOGIN PASSWORD %L', '{worker_password}')
WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{worker_role}')
\\gexec
ALTER ROLE {worker_role} WITH LOGIN PASSWORD '{worker_url_password_sql}';
GRANT pcb_solve_worker TO {worker_role};
GRANT CONNECT ON DATABASE {db_name} TO {worker_role};
SELECT format('CREATE ROLE {LOCAL_SCORER_ROLE} LOGIN PASSWORD %L', '{scorer_password}')
WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{LOCAL_SCORER_ROLE}')
\\gexec
ALTER ROLE {LOCAL_SCORER_ROLE} WITH LOGIN PASSWORD '{scorer_url_password_sql}';
GRANT pcb_scorer, pcb_artifact_finalizer TO {LOCAL_SCORER_ROLE};
GRANT CONNECT ON DATABASE {db_name} TO {LOCAL_SCORER_ROLE};
INSERT INTO artifact_quota (visibility, encryption_domain, max_bytes)
VALUES ('internal', 'worker-config', 67108864), ('internal', 'solve-session', 1073741824),
       ('internal', 'evaluation-evidence', 1073741824),
       ('internal', 'scoring-outcomes', 67108864)
ON CONFLICT (visibility, encryption_domain) DO NOTHING;
ALTER ROLE {LOCAL_ADMIN_ROLE} WITH LOGIN PASSWORD '{admin_password}';
"""
    run_compose_psql(db_name, role_sql)
    _persist_env_values(
        {
            "PCB_MIGRATION_DATABASE_URL": _local_admin_database_url(
                values["PCB_LOCAL_POSTGRES_PASSWORD"], LOCAL_DATABASE
            ),
            "PCB_OPS_REHEARSAL_DATABASE_URL": _local_admin_database_url(
                values["PCB_LOCAL_POSTGRES_PASSWORD"], LOCAL_OPS_DATABASE
            ),
        }
    )
    print(f"Local API database is migrated and scoped roles are configured: {db_name}.")


def _persist_env_values(updates: dict[str, str]) -> None:
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    found: set[str] = set()
    rewritten: list[str] = []
    for line in lines:
        name, separator, _ = line.partition("=")
        if separator and name in updates:
            rewritten.append(f"{name}={updates[name]}")
            found.add(name)
        else:
            rewritten.append(line)
    missing = set(updates) - found
    if missing:
        raise RuntimeError("local .env is missing generated database settings")
    temporary_path = ENV_PATH.with_name(".env.local-update")
    temporary_path.write_text("\n".join(rewritten) + "\n", encoding="utf-8", newline="\n")
    try:
        os.chmod(temporary_path, 0o600)
    except OSError:
        # On Windows, the ignored workspace file inherits the user's directory ACL.
        pass
    temporary_path.replace(ENV_PATH)


def seed_release_store(values: dict[str, str] | None = None) -> None:
    values = values or read_env()
    from polycodebench_publication.releases import ReleaseStore

    store_path = ROOT / values["PCB_RELEASE_STORE_PATH"]
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store = ReleaseStore(store_path)
    existing = store.list_public()
    if existing:
        print(f"Synthetic local release store already has {len(existing)} public releases.")
        return
    fixture = subprocess.run(
        [
            "uv",
            "run",
            "--locked",
            "--group",
            "dev",
            "python",
            "-m",
            "polycodebench_api.dev_fixture",
            "--store",
            str(store_path),
            "--count",
            "2",
        ],
        cwd=ROOT,
        check=False,
    )
    if fixture.returncode:
        raise RuntimeError("could not create the synthetic local release fixture")


def seed_public_release_catalog(values: dict[str, str] | None = None) -> None:
    """Create local signing material and sync verified synthetic releases to PostgreSQL."""
    values = values or read_env()
    if values.get("PCB_PUBLIC_RELEASE_BACKEND", "sqlite") != "postgres":
        return
    if not PUBLIC_RELEASE_STORE.exists():
        PUBLIC_RELEASE_STORE.parent.mkdir(parents=True, exist_ok=True)
        fixture = subprocess.run(
            [
                "uv",
                "run",
                "--locked",
                "--group",
                "dev",
                "python",
                "-m",
                "polycodebench_api.dev_fixture",
                "--store",
                str(PUBLIC_RELEASE_STORE),
                "--count",
                "2",
                "--signing-key",
                str(PUBLIC_RELEASE_SIGNING_KEY),
                "--keyring",
                str(PUBLIC_RELEASE_KEYRING),
            ],
            cwd=ROOT,
            check=False,
        )
        if fixture.returncode:
            raise RuntimeError("could not create signed local synthetic release projections")

    env = os.environ.copy()
    # The web API uses a reader-scoped DSN. Only this local operator command receives the
    # publisher-scoped credential, and the credential itself remains in the ignored .env file.
    env["PCB_DATABASE_URL"] = values["PCB_PUBLISHER_DATABASE_URL"]
    target = values.get("PCB_PUBLICATION_TARGET", "local:board")
    sync = subprocess.run(
        [
            "uv",
            "run",
            "--locked",
            "--all-packages",
            "pcb-ops",
            "releases",
            "sync-publication",
            "--store",
            str(PUBLIC_RELEASE_STORE),
            "--keyring",
            str(PUBLIC_RELEASE_KEYRING),
            "--target",
            target,
        ],
        cwd=ROOT,
        env=env,
        check=False,
    )
    if sync.returncode:
        raise RuntimeError("could not sync verified synthetic releases into local PostgreSQL")


def wait_for_keycloak(timeout_seconds: int = 120) -> None:
    values = read_env()
    endpoint = values["PCB_OIDC_ISSUER"].rstrip("/") + "/.well-known/openid-configuration"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urlopen(endpoint, timeout=3) as response:
                document = json.load(response)
            if document.get("issuer") != values["PCB_OIDC_ISSUER"]:
                raise RuntimeError("local OIDC issuer returned an unexpected identity")
            print("Local Keycloak issuer discovery is ready.")
            return
        except (OSError, ValueError):
            time.sleep(2)
    raise RuntimeError("local Keycloak did not become ready before the timeout")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("prepare", "bootstrap-db", "seed", "wait-for-keycloak"),
    )
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            prepare()
        elif args.command == "bootstrap-db":
            bootstrap_database()
        elif args.command == "seed":
            seed_release_store()
            seed_public_release_catalog()
        else:
            wait_for_keycloak()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"local stack setup failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
