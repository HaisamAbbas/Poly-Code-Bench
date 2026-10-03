"""Store reviewed public model-submission metadata and approval plans."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a20c4e619d32"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "model_submission",
        sa.Column("request_document", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "model_submission",
        sa.Column("approval_document", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "model_submission", sa.Column("approval_digest", sa.String(length=71), nullable=True)
    )
    op.create_check_constraint(
        "approval_digest_format",
        "model_submission",
        "approval_digest IS NULL OR approval_digest ~ '^sha256:[0-9a-f]{64}$'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_model_submission_approval_digest_format", "model_submission", type_="check"
    )
    op.drop_column("model_submission", "approval_digest")
    op.drop_column("model_submission", "approval_document")
    op.drop_column("model_submission", "request_document")
