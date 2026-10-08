"""Require private verified artifacts for append-only fingerprint features."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e9b30a7c1f42"
down_revision: str | None = "d52a7e11b30f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM fingerprint AS fp
                LEFT JOIN artifact AS a ON a.id = fp.private_artifact_id
                WHERE fp.private_artifact_id IS NULL
                   OR a.id IS NULL
                   OR a.status <> 'verified'
                   OR a.visibility NOT IN ('hidden', 'internal')
            ) THEN
                RAISE EXCEPTION 'existing fingerprint rows are not bound to verified private artifacts';
            END IF;
        END;
        $$;
        """
    )
    op.alter_column("fingerprint", "private_artifact_id", existing_type=sa.Uuid(), nullable=False)
    op.execute(
        """
        CREATE FUNCTION pcb_require_private_fingerprint_artifact() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            artifact_visibility text;
            artifact_status text;
        BEGIN
            SELECT visibility, status
            INTO artifact_visibility, artifact_status
            FROM artifact
            WHERE id = NEW.private_artifact_id
            FOR KEY SHARE;
            IF NOT FOUND
               OR artifact_status <> 'verified'
               OR artifact_visibility NOT IN ('hidden', 'internal') THEN
                RAISE EXCEPTION 'fingerprint features require verified private artifacts';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER require_private_fingerprint_artifact
        BEFORE INSERT ON fingerprint
        FOR EACH ROW EXECUTE FUNCTION pcb_require_private_fingerprint_artifact();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM fingerprint) THEN
                RAISE EXCEPTION 'cannot remove private fingerprint guarantees while feature rows exist';
            END IF;
        END;
        $$;
        """
    )
    op.execute("DROP TRIGGER require_private_fingerprint_artifact ON fingerprint")
    op.execute("DROP FUNCTION pcb_require_private_fingerprint_artifact()")
    op.alter_column("fingerprint", "private_artifact_id", existing_type=sa.Uuid(), nullable=True)
