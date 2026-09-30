"""Persist complete scheduler results and evaluation infrastructure failures."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "8ac42e1d09bf"
down_revision: str | None = "7b8cc92d13ea"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("stage_job", sa.Column("result_document", postgresql.JSONB(), nullable=True))
    op.add_column("evaluation", sa.Column("failure_class", sa.String(64), nullable=True))
    op.execute("GRANT UPDATE (failure_class) ON evaluation TO pcb_scheduler")


def downgrade() -> None:
    raise NotImplementedError("Committed result identity and failure evidence must be preserved.")
