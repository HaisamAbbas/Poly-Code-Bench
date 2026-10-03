"""Expand/contract migration checks and the compatibility rehearsal (T 22.4).

``check_expand_only`` statically inspects every revision newer than the released schema and
refuses destructive operations (drop/rename table or column, narrowing type changes,
TRUNCATE/DELETE) unless the revision is listed as an approved contract migration with a
retention plan. ``rehearse`` applies the migrations for real:

* an empty database upgraded to head, then ``alembic check`` against the models;
* a database at the previous released revision upgraded to head;
* both resulting schemas compared column-by-column - they must be identical.

Downgrades are not part of rollback: revisions refuse ``downgrade`` to preserve evidence, and
rollback returns services to the previous compatible code while the expanded schema stays.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.pool import NullPool

REPO_ROOT = Path(__file__).resolve().parents[4]
PERSISTENCE = REPO_ROOT / "packages" / "persistence"
VERSIONS = PERSISTENCE / "src" / "polycodebench_persistence" / "migrations" / "versions"
ALEMBIC_INI = PERSISTENCE / "alembic.ini"
POLICY_PATH = REPO_ROOT / "config" / "operations" / "migration-policy.yaml"

_DESTRUCTIVE_OPS = {
    "drop_table": "drops a table",
    "drop_column": "drops a column",
    "rename_table": "renames a table",
    "drop_constraint": "drops a constraint",
}
_DESTRUCTIVE_SQL = re.compile(
    r"\b(DROP\s+TABLE|DROP\s+COLUMN|TRUNCATE|DELETE\s+FROM|ALTER\s+TABLE\s+\S+\s+RENAME)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Revision:
    revision: str
    down_revision: str | None
    path: Path


@dataclass
class ExpandReport:
    released_revision: str
    head: str
    checked: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    policy = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(policy, dict) or policy.get("schema_version") != 1:
        raise ValueError("migration policy must be a schema_version 1 mapping")
    return policy


def revisions(directory: Path = VERSIONS) -> dict[str, Revision]:
    found: dict[str, Revision] = {}
    for path in sorted(directory.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        values: dict[str, Any] = {}
        for node in tree.body:
            targets: list[ast.expr] = []
            value: ast.expr | None = None
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            for target in targets:
                if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}:
                    values[target.id] = ast.literal_eval(value) if value is not None else None
        if "revision" in values:
            found[values["revision"]] = Revision(
                values["revision"], values.get("down_revision"), path
            )
    return found


def linear_chain(found: dict[str, Revision]) -> list[str]:
    children: dict[str | None, list[str]] = {}
    for item in found.values():
        children.setdefault(item.down_revision, []).append(item.revision)
    if any(len(kids) > 1 for kids in children.values()):
        raise ValueError("migration history has branches; merge before release")
    chain: list[str] = []
    cursor: str | None = None
    while cursor in children:
        cursor = children[cursor][0]
        chain.append(cursor)
    if len(chain) != len(found):
        raise ValueError("migration history is not a single linear chain")
    return chain


def _upgrade_violations(path: Path, provenance: set[str]) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    upgrade = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "upgrade"
        ),
        None,
    )
    if upgrade is None:
        return [f"{path.name}: no upgrade()"]
    problems: list[str] = []
    for node in ast.walk(upgrade):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        name = node.func.attr
        if name in _DESTRUCTIVE_OPS:
            problems.append(f"{path.name}:{node.lineno}: upgrade {_DESTRUCTIVE_OPS[name]}")
        if name == "alter_column":
            keywords = {keyword.arg for keyword in node.keywords}
            if {"type_", "new_column_name"} & keywords:
                problems.append(f"{path.name}:{node.lineno}: upgrade changes a column type/name")
            if any(
                keyword.arg == "nullable"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is False
                for keyword in node.keywords
            ):
                problems.append(
                    f"{path.name}:{node.lineno}: upgrade makes an existing column NOT NULL "
                    "(needs backfill release first)"
                )
        if name in {"execute", "exec_driver_sql"}:
            for argument in node.args:
                literal = argument.value if isinstance(argument, ast.Constant) else None
                if isinstance(literal, str):
                    match = _DESTRUCTIVE_SQL.search(literal)
                    if match:
                        problems.append(
                            f"{path.name}:{node.lineno}: upgrade SQL contains "
                            f"{match.group(1).upper()}"
                        )
                    for table in provenance:
                        if re.search(
                            rf"\bDROP\s+TABLE\s+(IF\s+EXISTS\s+)?{table}\b", literal, re.I
                        ):
                            problems.append(
                                f"{path.name}:{node.lineno}: drops provenance table {table}"
                            )
    return problems


def check_expand_only(
    policy: dict[str, Any] | None = None, directory: Path = VERSIONS
) -> ExpandReport:
    policy = policy or load_policy()
    found = revisions(directory)
    chain = linear_chain(found)
    released = policy["released_revision"]
    if released not in chain:
        raise ValueError(f"released revision {released} is not in the migration chain")
    report = ExpandReport(released_revision=released, head=chain[-1])
    approved = policy.get("approved_contract_migrations") or {}
    provenance = set(policy.get("provenance_tables") or ())
    for revision in chain[chain.index(released) + 1 :]:
        report.checked.append(revision)
        problems = _upgrade_violations(found[revision].path, provenance)
        if problems and not (
            isinstance(approved.get(revision), dict) and approved[revision].get("retention_plan")
        ):
            report.violations.extend(problems)
    return report


# ------------------------------------------------------------------ live rehearsal


def _admin_url(url: str) -> URL:
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql":
        raise ValueError("migration rehearsal requires PostgreSQL")
    return parsed.set(drivername="postgresql+psycopg")


@contextmanager
def scratch_database(admin_url: str, label: str) -> Iterator[str]:
    """Create a uniquely named database and always drop it afterwards."""

    admin = _admin_url(admin_url)
    name = f"pcb_ops_{label}_{uuid.uuid4().hex[:10]}_test"
    engine = create_engine(admin, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        yield admin.set(database=name).render_as_string(hide_password=False)
    finally:
        with engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        engine.dispose()


def run_sql_file(url: str, path: Path) -> None:
    engine = create_engine(_admin_url(url), poolclass=NullPool, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            # Raw driver cursor without parameters: the SQL files use format('%I') literally.
            raw = connection.connection.driver_connection
            assert raw is not None
            with raw.cursor() as cursor:
                cursor.execute(path.read_text(encoding="utf-8").encode("utf-8"))
    finally:
        engine.dispose()


def alembic(
    url: str, *arguments: str, persistence_root: Path = PERSISTENCE
) -> subprocess.CompletedProcess[str]:
    """Run Alembic for ``persistence_root`` (default: this checkout).

    A different root (for example a read-only worktree of a release commit) is put first on
    ``PYTHONPATH`` so its migrations and models are the ones exercised.
    """
    env = {**os.environ, "PCB_MIGRATION_DATABASE_URL": url}
    if persistence_root != PERSISTENCE:
        env["PYTHONPATH"] = os.pathsep.join(
            [str(persistence_root / "src"), env.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep)
    argv = [
        sys.executable,
        "-c",
        "import sys; from alembic.config import main; sys.exit(main(argv=sys.argv[1:]))",
        "-c",
        str(persistence_root / "alembic.ini"),
        *arguments,
    ]
    result = _run(argv, env)
    # Observed on the Windows rehearsal host: interpreter start-up intermittently dies with
    # 0xC000070A inside the OS thread pool before Alembic runs. Alembic commits one
    # transaction per revision, so re-running the same command is safe; retry only that status.
    for _ in range(2):
        if result.returncode != _WINDOWS_THREADPOOL_CRASH:
            break
        result = _run(argv, env)
    return result


_WINDOWS_THREADPOOL_CRASH = 0xC000070A


def _run(argv: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv
        argv,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )


def schema_fingerprint(url: str) -> list[tuple[str, ...]]:
    engine = create_engine(_admin_url(url), poolclass=NullPool)
    try:
        with engine.connect() as connection:
            columns = connection.execute(
                text(
                    "SELECT table_name, column_name, data_type, is_nullable, "
                    "coalesce(column_default, '') FROM information_schema.columns "
                    "WHERE table_schema = 'public' ORDER BY 1, 2"
                )
            ).all()
            constraints = connection.execute(
                text(
                    "SELECT conrelid::regclass::text, conname, contype::text, "
                    "pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE connamespace = 'public'::regnamespace ORDER BY 1, 2"
                )
            ).all()
            triggers = connection.execute(
                text(
                    "SELECT event_object_table, trigger_name, action_timing, event_manipulation "
                    "FROM information_schema.triggers WHERE trigger_schema = 'public' "
                    "ORDER BY 1, 2, 4"
                )
            ).all()
            head = connection.execute(text("SELECT version_num FROM alembic_version")).all()
    finally:
        engine.dispose()
    return [
        *(("column", *map(str, row)) for row in columns),
        *(("constraint", *map(str, row)) for row in constraints),
        *(("trigger", *map(str, row)) for row in triggers),
        *(("head", *map(str, row)) for row in head),
    ]


def _step(
    steps: list[dict[str, Any]],
    name: str,
    result: subprocess.CompletedProcess[str],
    *,
    fatal: bool = True,
) -> bool:
    detail = [line[:300] for line in result.stderr.strip().splitlines() if "ERROR" in line][:3]
    steps.append(
        {
            "step": name,
            "returncode": result.returncode,
            "passed": result.returncode == 0,
            "errors": detail if result.returncode else [],
        }
    )
    if result.returncode != 0 and fatal:
        tail = " | ".join(result.stderr.strip().splitlines()[-3:])
        raise RuntimeError(f"{name} failed (exit {result.returncode}): {tail}")
    return result.returncode == 0


def rehearse(
    admin_url: str,
    policy: dict[str, Any] | None = None,
    *,
    persistence_root: Path = PERSISTENCE,
) -> dict[str, Any]:
    policy = policy or load_policy()
    previous = policy["previous_revision"]
    root = persistence_root
    steps: list[dict[str, Any]] = []
    linear_chain(revisions(root / "src" / "polycodebench_persistence" / "migrations" / "versions"))
    with scratch_database(admin_url, "empty") as empty, scratch_database(admin_url, "prev") as prev:
        run_sql_file(empty, root / "sql" / "provision_roles.sql")
        _step(steps, "empty -> head", alembic(empty, "upgrade", "head", persistence_root=root))
        run_sql_file(empty, root / "sql" / "grant_permissions.sql")
        # A model/schema drift is a finding to report, not a reason to skip the upgrade paths.
        models_match = _step(
            steps,
            "alembic check (models == schema)",
            alembic(empty, "check", persistence_root=root),
            fatal=False,
        )
        run_sql_file(prev, root / "sql" / "provision_roles.sql")
        _step(
            steps, f"empty -> {previous}", alembic(prev, "upgrade", previous, persistence_root=root)
        )
        _step(steps, f"{previous} -> head", alembic(prev, "upgrade", "head", persistence_root=root))
        run_sql_file(prev, root / "sql" / "grant_permissions.sql")
        fresh, upgraded = schema_fingerprint(empty), schema_fingerprint(prev)
    differences = sorted(set(fresh) ^ set(upgraded))
    return {
        "persistence_root": str(root),
        "previous_revision": previous,
        "head": next((row[1] for row in fresh if row[0] == "head"), None),
        "steps": steps,
        "schema_objects": len(fresh),
        "schema_identical": not differences,
        "models_match_schema": models_match,
        "passed": not differences and models_match,
        "differences": [list(item) for item in differences[:50]],
    }
