"""Persist exact reviewed projection approvals before public publication."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b5e17f2c4096"
down_revision: str | None = "a4f04c4f5a12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE artifact_projection_approval (
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        source_artifact_id uuid NOT NULL,
        projection_digest varchar(71) NOT NULL,
        approved_by varchar(255) NOT NULL,
        reason text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT pk_artifact_projection_approval PRIMARY KEY (id),
        CONSTRAINT fk_artifact_projection_approval_source_artifact_id_artifact
            FOREIGN KEY (source_artifact_id) REFERENCES artifact(id) ON DELETE RESTRICT,
        CONSTRAINT ck_artifact_projection_approval_digest_format
            CHECK (projection_digest ~ '^sha256:[0-9a-f]{64}$'),
        CONSTRAINT ck_artifact_projection_approval_reason_nonempty
            CHECK (length(trim(reason)) > 0)
    );
    CREATE FUNCTION pcb_guard_projection_approval() RETURNS trigger AS $$
    DECLARE source_visibility text; source_status text;
    BEGIN
      SELECT visibility, status INTO source_visibility, source_status
      FROM artifact WHERE id = NEW.source_artifact_id;
      IF source_status IS DISTINCT FROM 'verified' OR source_visibility = 'public' THEN
        RAISE EXCEPTION USING ERRCODE = '23514',
          MESSAGE = 'projection approval requires a verified non-public source';
      END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql;
    CREATE TRIGGER artifact_projection_approval_scope_guard
      BEFORE INSERT ON artifact_projection_approval
      FOR EACH ROW EXECUTE FUNCTION pcb_guard_projection_approval();
    CREATE TRIGGER artifact_projection_approval_immutable
      BEFORE UPDATE OR DELETE ON artifact_projection_approval
      FOR EACH ROW EXECUTE FUNCTION pcb_reject_immutable_change();
    """)


def downgrade() -> None:
    raise NotImplementedError("Projection approval evidence must be preserved.")
