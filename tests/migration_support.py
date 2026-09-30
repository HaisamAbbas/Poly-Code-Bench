"""Migration-level guards for integration tests that need a minimum schema revision."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Connection

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "packages" / "persistence" / "alembic.ini"


def require_migrated_through(connection: Connection, revision: str) -> None:
    """Fail unless the database is at ``revision`` or a descendant of it.

    Later prompts add migrations; pinning a test to one exact head would make every earlier
    prompt's suite fail as soon as a newer revision exists.
    """
    current = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    script = ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))
    ancestry = {item.revision for item in script.walk_revisions(base="base", head=current)}
    if revision not in ancestry:
        pytest.fail(f"test database is at {current}; migration {revision} has not been applied")
