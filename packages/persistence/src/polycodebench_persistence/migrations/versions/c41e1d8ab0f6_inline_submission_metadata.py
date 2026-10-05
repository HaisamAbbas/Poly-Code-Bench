"""Allow validated inline metadata for public submission requests."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c41e1d8ab0f6"
down_revision: str | None = "a20c4e619d32"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "model_submission",
        "metadata_artifact_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )
    op.create_check_constraint(
        "submission_content_present",
        "model_submission",
        "metadata_artifact_id IS NOT NULL OR request_document IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_model_submission_submission_content_present", "model_submission", type_="check"
    )
    op.alter_column(
        "model_submission",
        "metadata_artifact_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
