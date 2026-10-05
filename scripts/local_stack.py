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
from urllib.parse import unquote, urlsplit
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
LOCAL_API_ROLE = "pcb_local_api"
LOCAL_PUBLISHER_ROLE = "pcb_local_publisher"
LOCAL_ADMIN_DSN = "postgresql+psycopg://polycodebench:local-development-only@127.0.0.1:55432/"
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
    "PCB_DATABASE_URL",
    "PCB_PUBLISHER_DATABASE_URL",
    "PCB_MIGRATION_DATABASE_URL",
    "PCB_PUBLIC_RELEASE_BACKEND",
    "PCB_PUBLICATION_TARGET",
    "PCB_RELEASE_STORE_PATH",
    "PCB_OIDC_ISSUER",
    "PCB_OIDC_CLIENT_ID",
    "PCB_OIDC_REDIRECT_URI",
    "PCB_WEB_ORIGIN",
    "PCB_PUBLIC_API_URL",
    "PCB_API_IDENTITY_FILE",
}


def read_env(path: Path = ENV_PATH) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
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
        }
        allowed_generated = set(generated_defaults) | {"PCB_PUBLISHER_DATABASE_URL"}
        if missing and set(missing) <= allowed_generated:
            additions = {key: value for key, value in generated_defaults.items() if key in missing}
            if "PCB_PUBLISHER_DATABASE_URL" in missing:
                publisher_password = secrets.token_urlsafe(32)
                additions["PCB_PUBLISHER_DATABASE_URL"] = (
                    f"postgresql+psycopg://{LOCAL_PUBLISHER_ROLE}:{publisher_password}"
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
            "PCB_DATABASE_URL": (
                f"postgresql+psycopg://{LOCAL_API_ROLE}:{api_password}"
                f"@127.0.0.1:55432/{LOCAL_DATABASE}"
            ),
            "PCB_PUBLISHER_DATABASE_URL": (
                f"postgresql+psycopg://{LOCAL_PUBLISHER_ROLE}:{publisher_password}"
                f"@127.0.0.1:55432/{LOCAL_DATABASE}"
            ),
            "PCB_MIGRATION_DATABASE_URL": f"{LOCAL_ADMIN_DSN}{LOCAL_DATABASE}",
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
        "polycodebench",
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
            "polycodebench",
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
                "polycodebench",
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
    publisher_password = urlsplit(values["PCB_PUBLISHER_DATABASE_URL"]).password
    if publisher_password is None:
        raise RuntimeError("local publisher database URL is invalid")
    publisher_password = unquote(publisher_password).replace("'", "''")
    role_sql = f"""\
SELECT format('CREATE ROLE {LOCAL_API_ROLE} LOGIN PASSWORD %L', '{password}')
WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{LOCAL_API_ROLE}')
\\gexec
ALTER ROLE {LOCAL_API_ROLE} WITH LOGIN PASSWORD '{password}';
GRANT pcb_public_reader, pcb_submitter, pcb_reviewer TO {LOCAL_API_ROLE};
GRANT CONNECT ON DATABASE {db_name} TO {LOCAL_API_ROLE};
SELECT format('CREATE ROLE {LOCAL_PUBLISHER_ROLE} LOGIN PASSWORD %L', '{publisher_password}')
WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{LOCAL_PUBLISHER_ROLE}')
\\gexec
ALTER ROLE {LOCAL_PUBLISHER_ROLE} WITH LOGIN PASSWORD '{publisher_password}';
GRANT pcb_publisher TO {LOCAL_PUBLISHER_ROLE};
GRANT CONNECT ON DATABASE {db_name} TO {LOCAL_PUBLISHER_ROLE};
"""
    run_compose_psql(db_name, role_sql)
    print(f"Local API database is migrated and scoped roles are configured: {db_name}.")


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
