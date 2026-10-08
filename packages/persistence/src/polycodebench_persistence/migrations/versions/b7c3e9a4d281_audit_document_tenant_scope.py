"""Bind private benchmark-audit documents to the authenticated tenant."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7c3e9a4d281"
down_revision: str | None = "f67a3d91c4b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "audit_document",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_audit_document_tenant_owner_kind_created",
        "audit_document",
        ["tenant_id", "created_by", "kind", "created_at", "id"],
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM audit_document WHERE tenant_id IS NOT NULL) THEN
                RAISE EXCEPTION
                    'cannot remove benchmark-audit tenant scope while tenant-bound documents exist';
            END IF;
        END;
        $$
        """
    )
    op.drop_index("ix_audit_document_tenant_owner_kind_created", table_name="audit_document")
    op.drop_column("audit_document", "tenant_id")
