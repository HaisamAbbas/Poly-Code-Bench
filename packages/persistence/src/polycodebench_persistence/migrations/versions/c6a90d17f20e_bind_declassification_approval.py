"""Require a stored exact-content approval for public declassification."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c6a90d17f20e"
down_revision: str | None = "b5e17f2c4096"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
    CREATE OR REPLACE FUNCTION pcb_guard_artifact_declassification() RETURNS trigger AS $$
    DECLARE src artifact%ROWTYPE; dst artifact%ROWTYPE; approved boolean;
    BEGIN
      SELECT * INTO src FROM artifact WHERE id = NEW.source_artifact_id;
      SELECT * INTO dst FROM artifact WHERE id = NEW.public_artifact_id;
      IF src.status IS DISTINCT FROM 'verified' OR src.visibility = 'public'
         OR dst.status IS DISTINCT FROM 'verified' OR dst.visibility IS DISTINCT FROM 'public' THEN
        RAISE EXCEPTION USING ERRCODE = '23514',
          MESSAGE = 'declassification requires verified non-public source and verified public projection';
      END IF;
      SELECT EXISTS (
        SELECT 1 FROM artifact_projection_approval
        WHERE source_artifact_id = NEW.source_artifact_id
          AND projection_digest = dst.content_digest
          AND projection_digest = NEW.review_digest
          AND approved_by = NEW.approved_by
          AND reason = NEW.reason
      ) INTO approved;
      IF NOT approved THEN
        RAISE EXCEPTION USING ERRCODE = '23514',
          MESSAGE = 'declassification requires a matching stored projection approval';
      END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    raise NotImplementedError("Publication approval guards must be preserved.")
