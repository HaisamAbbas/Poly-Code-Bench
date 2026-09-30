"""Allow durable cancellation of unfinished evaluation scopes."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "7b8cc92d13ea"
down_revision: str | None = "f17b6b04a237"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_evaluation_state"), "evaluation", type_="check")
    op.create_check_constraint(
        "state",
        "evaluation",
        "state IN ('queued','running','ready','failed','superseded','cancelled')",
    )


def downgrade() -> None:
    raise NotImplementedError("Evaluation cancellation is durable execution evidence.")
