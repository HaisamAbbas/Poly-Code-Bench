"""Allow the scheduler to advance the parent run lifecycle."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c02ea53a4d17"
down_revision: str | None = "b390a26f17cd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("GRANT UPDATE (status, row_version) ON run TO pcb_scheduler")


def downgrade() -> None:
    op.execute("REVOKE UPDATE (status, row_version) ON run FROM pcb_scheduler")
