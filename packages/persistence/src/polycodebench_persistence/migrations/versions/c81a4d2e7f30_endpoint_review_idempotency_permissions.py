"""Allow endpoint administrators to persist idempotent review decisions."""

from __future__ import annotations

from alembic import op

revision = "c81a4d2e7f30"
down_revision = "b7c3e9a4d281"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON idempotency_record TO pcb_endpoint_administrator"
    )


def downgrade() -> None:
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE, DELETE "
        "ON idempotency_record FROM pcb_endpoint_administrator"
    )
