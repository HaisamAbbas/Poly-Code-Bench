"""Allow the scheduler to enqueue an explicitly selected evaluation."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "2a62b6001aa1"
down_revision: str | None = "f601d3c28a47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("GRANT INSERT ON evaluation TO pcb_scheduler")


def downgrade() -> None:
    op.execute("REVOKE INSERT ON evaluation FROM pcb_scheduler")
