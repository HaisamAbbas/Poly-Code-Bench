"""Bind each solve checkpoint to its protocol, workspace and transcript by digest.

A checkpoint is the atomic pair (workspace revision, conversation state). These columns let a
restorer prove the pair was committed together under one protocol instead of trusting two
independent artifact references.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2f6d4a91c73"
down_revision: str | None = "9d3a71c05e24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ZERO = "sha256:" + "0" * 64


def upgrade() -> None:
    for column in ("protocol_digest", "workspace_digest", "transcript_digest", "binding_digest"):
        op.add_column(
            "attempt_checkpoint",
            sa.Column(column, sa.String(length=71), nullable=False, server_default=_ZERO),
        )
        op.alter_column("attempt_checkpoint", column, server_default=None)
    op.create_check_constraint(
        "digest_format",
        "attempt_checkpoint",
        "protocol_digest ~ '^sha256:[0-9a-f]{64}$' AND workspace_digest ~ '^sha256:[0-9a-f]{64}$' "
        "AND transcript_digest ~ '^sha256:[0-9a-f]{64}$' "
        "AND binding_digest ~ '^sha256:[0-9a-f]{64}$'",
    )


def downgrade() -> None:
    raise NotImplementedError("Solve checkpoints are durable execution evidence.")
