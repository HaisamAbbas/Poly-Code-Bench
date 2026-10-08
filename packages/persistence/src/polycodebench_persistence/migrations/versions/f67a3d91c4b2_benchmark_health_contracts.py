"""Keep benchmark-health reports as immutable, versioned cohort snapshots."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f67a3d91c4b2"
down_revision: str | None = "e5c7b2a94d10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        op.f("ck_audit_document_benchmark_health_append_only"),
        "audit_document",
        "kind <> 'benchmark_health' OR supersedes_id IS NULL",
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM audit_document
                WHERE kind = 'benchmark_health' AND schema_version = 2
            ) THEN
                RAISE EXCEPTION
                    'cannot remove versioned benchmark-health contracts while reports exist';
            END IF;
        END;
        $$
        """
    )
    op.drop_constraint(
        op.f("ck_audit_document_benchmark_health_append_only"),
        "audit_document",
        type_="check",
    )
