"""Permit the versioned audit evidence documents introduced by Prompt90."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "1a2b3c4d5e6f"
down_revision: str | None = "e9b30a7c1f42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("schema_version_v1", "audit_document", type_="check")
    op.create_check_constraint(
        "schema_version_supported",
        "audit_document",
        "schema_version IN (1,2)",
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM audit_document WHERE schema_version <> 1
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade audit_document while version 2 documents exist';
            END IF;
        END;
        $$
        """
    )
    op.drop_constraint("schema_version_supported", "audit_document", type_="check")
    op.create_check_constraint("schema_version_v1", "audit_document", "schema_version = 1")
