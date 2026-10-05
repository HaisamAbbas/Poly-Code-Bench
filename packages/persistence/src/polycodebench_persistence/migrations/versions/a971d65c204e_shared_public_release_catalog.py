"""Add an immutable, sanitized PostgreSQL catalog for published API releases."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a971d65c204e"
down_revision: str | None = "d8f971ea2b34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "public_release_document",
        sa.Column("release_id", sa.String(120), nullable=False),
        sa.Column("document", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_digest", sa.String(71), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("release_id", name="pk_public_release_document"),
        sa.CheckConstraint(
            "state IN ('published','withdrawn')", name=op.f("ck_public_release_document_state")
        ),
        sa.CheckConstraint(
            "content_digest ~ '^sha256:[0-9a-f]{64}$'",
            name=op.f("ck_public_release_document_content_digest_format"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(document) = 'object'",
            name=op.f("ck_public_release_document_document_object"),
        ),
        sa.CheckConstraint(
            "document ?& ARRAY['id','version','state','content','projection','content_digest','manifest']",
            name=op.f("ck_public_release_document_document_required_public_fields"),
        ),
        sa.CheckConstraint(
            "(document - ARRAY['id','slug','version','state','content','projection',"
            "'content_digest','manifest','withdrawal']) = '{}'::jsonb",
            name=op.f("ck_public_release_document_document_public_allowlist"),
        ),
        sa.CheckConstraint(
            "NOT (document ?| ARRAY['validation','review','approval'])",
            name=op.f("ck_public_release_document_document_no_private_workflow_fields"),
        ),
        sa.CheckConstraint(
            "document->>'id' = release_id",
            name=op.f("ck_public_release_document_document_identity"),
        ),
        sa.CheckConstraint(
            "document->>'state' = state",
            name=op.f("ck_public_release_document_document_state"),
        ),
        sa.CheckConstraint(
            "document->>'content_digest' = content_digest",
            name=op.f("ck_public_release_document_document_digest"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(document->'version') = 'number' AND (document->>'version')::integer > 0",
            name=op.f("ck_public_release_document_document_version_positive"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(document->'content') = 'object' AND "
            "jsonb_typeof(document->'projection') = 'object' AND "
            "jsonb_typeof(document->'manifest') = 'object'",
            name=op.f("ck_public_release_document_document_public_payload_objects"),
        ),
    )
    op.create_table(
        "public_release_pointer",
        sa.Column("target", sa.String(160), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("release_id", sa.String(120), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("target", name="pk_public_release_pointer"),
        sa.ForeignKeyConstraint(
            ["release_id"],
            ["public_release_document.release_id"],
            name="fk_public_release_pointer_release_id_public_release_document",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "generation >= 1", name=op.f("ck_public_release_pointer_generation_positive")
        ),
    )
    op.execute(
        """
        CREATE FUNCTION guard_public_release_document_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.release_id = NEW.release_id
               AND OLD.document = NEW.document
               AND OLD.content_digest = NEW.content_digest
               AND OLD.state = NEW.state
               AND OLD.published_at = NEW.published_at THEN
                RETURN NEW;
            END IF;
            IF OLD.state <> 'published' OR NEW.state <> 'withdrawn'
               OR OLD.release_id <> NEW.release_id
               OR OLD.content_digest <> NEW.content_digest
               OR OLD.published_at <> NEW.published_at
               OR (NEW.document - ARRAY['state','version','withdrawal'])
                  IS DISTINCT FROM (OLD.document - ARRAY['state','version','withdrawal'])
               OR (NEW.document->>'version')::integer <> (OLD.document->>'version')::integer + 1
               OR jsonb_typeof(NEW.document->'withdrawal') <> 'object'
               OR COALESCE(NULLIF(btrim(NEW.document->'withdrawal'->>'reason'), ''), '') = '' THEN
                RAISE EXCEPTION 'public release snapshots are immutable except for a withdrawal notice';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER public_release_document_immutable
        BEFORE UPDATE ON public_release_document
        FOR EACH ROW EXECUTE FUNCTION guard_public_release_document_update()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS public_release_document_immutable ON public_release_document"
    )
    op.execute("DROP FUNCTION IF EXISTS guard_public_release_document_update()")
    op.drop_table("public_release_pointer")
    op.drop_table("public_release_document")
