"""Offline tests for scripts/render/{bootstrap,publish}_remote.py (no database needed)."""

from __future__ import annotations

import argparse
import importlib.util
import stat
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str) -> ModuleType:
    for extra in (ROOT / "scripts", ROOT / "scripts" / "render"):
        if str(extra) not in sys.path:
            sys.path.insert(0, str(extra))
    spec = importlib.util.spec_from_file_location(
        f"{name}_under_test", ROOT / "scripts" / "render" / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bootstrap = _load("bootstrap_remote")
publish = _load("publish_remote")

EXTERNAL = "postgresql://pcb_owner:ownerpw@dpg-abc123-a.oregon-postgres.render.com/pcb"


def test_url_forces_psycopg_and_tls_for_remote_hosts() -> None:
    url = bootstrap._url(EXTERNAL)
    assert url.drivername == "postgresql+psycopg"
    assert url.query["sslmode"] == "require"
    assert "sslmode" not in bootstrap._url("postgresql://u:p@127.0.0.1:5432/db").query


def test_non_postgres_url_is_rejected() -> None:
    with pytest.raises(SystemExit):
        bootstrap._url("mysql://u:p@h/db")


def test_internal_host_strips_the_region_suffix() -> None:
    assert bootstrap.internal_host("dpg-abc123-a.oregon-postgres.render.com") == "dpg-abc123-a"
    assert bootstrap.internal_host("127.0.0.1") == "127.0.0.1"


def test_secrets_use_scoped_roles_are_stable_and_split_hosts(tmp_path: Path) -> None:
    url = bootstrap._url(EXTERNAL)
    first = bootstrap.build_secrets(url, {})
    assert first["PCB_DATABASE_URL"].startswith("postgresql+psycopg://pcb_render_api:")
    assert "@dpg-abc123-a/pcb" in first["PCB_DATABASE_URL"]
    assert "oregon-postgres.render.com" in first["PCB_RENDER_PUBLISHER_DATABASE_URL"]
    assert "ownerpw" not in "".join(first.values())
    assert len(bytes.fromhex(first["PCB_CURSOR_SIGNING_KEY"])) == 32
    assert bootstrap.build_secrets(url, first) == first


def test_secrets_file_round_trip_and_mode(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "secrets.env"
    bootstrap.write_secrets(path, {"A": "1", "B": "x=y"})
    assert bootstrap.read_secrets(path) == {"A": "1", "B": "x=y"}
    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_main_requires_the_owner_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PCB_RENDER_DATABASE_URL", raising=False)
    assert bootstrap.main([]) == 2


def test_publish_refuses_synthetic_current_release_by_default() -> None:
    snapshots = [{"id": "r1", "projection": {"fixture_kind": "synthetic_internal"}}]
    pointer = {"generation": 1, "release_id": "r1"}
    with pytest.raises(SystemExit):
        publish.check_kind(snapshots, pointer, allow_synthetic=False)
    publish.check_kind(snapshots, pointer, allow_synthetic=True)
    live = [{"id": "r1", "projection": {"fixture_kind": "live_exploratory"}}]
    publish.check_kind(live, pointer, allow_synthetic=False)


def test_publish_dsn_resolution_prefers_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secrets_file = tmp_path / "s.env"
    secrets_file.write_text("PCB_RENDER_PUBLISHER_DATABASE_URL=from-file\n", encoding="utf-8")
    monkeypatch.delenv(publish.DSN_NAME, raising=False)
    assert publish.resolve_dsn(secrets_file) == "from-file"
    monkeypatch.setenv(publish.DSN_NAME, "from-env")
    assert publish.resolve_dsn(secrets_file) == "from-env"
    monkeypatch.delenv(publish.DSN_NAME)
    with pytest.raises(SystemExit):
        publish.resolve_dsn(tmp_path / "missing.env")


def test_publish_dry_run_verifies_the_local_store_without_a_database(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = ROOT / ".cache" / "polycodebench-local-verified-release-store.sqlite3"
    keyring = ROOT / ".cache" / "polycodebench-local-keyring.json"
    if not (store.exists() and keyring.exists()):
        pytest.skip("no local signed release store")
    monkeypatch.delenv(publish.DSN_NAME, raising=False)
    args = argparse.Namespace(
        store=store,
        keyring=keyring,
        target="render:board",
        source_target="local:board",
        secrets_file=ROOT / ".local" / "render" / "absent.env",
        dry_run=True,
        allow_synthetic=True,
    )
    result = publish.run(args)
    assert result["dry_run"] is True and "sync" not in result
    assert all(row["key_id"] for row in result["releases"])
    capsys.readouterr()
