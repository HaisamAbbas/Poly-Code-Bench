"""Align repair-table foreign key delete rules with the schema model."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d8f971ea2b34"
down_revision: str | None = "c41e1d8ab0f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Bound lock waits and validation scans; timeout rolls back the atomic FK replacement.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    op.drop_constraint(
        "fk_repair_delivery_repair_round_id_repair_round",
        "repair_delivery",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_repair_delivery_repair_round_id_repair_round",
        "repair_delivery",
        "repair_round",
        ["repair_round_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "fk_repair_round_repair_run_id_repair_run", "repair_round", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_repair_round_repair_run_id_repair_run",
        "repair_round",
        "repair_run",
        ["repair_run_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint("fk_repair_run_attempt_id_attempt", "repair_run", type_="foreignkey")
    op.create_foreign_key(
        "fk_repair_run_attempt_id_attempt",
        "repair_run",
        "attempt",
        ["attempt_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_repair_delivery_repair_round_id_repair_round",
        "repair_delivery",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_repair_delivery_repair_round_id_repair_round",
        "repair_delivery",
        "repair_round",
        ["repair_round_id"],
        ["id"],
    )
    op.drop_constraint(
        "fk_repair_round_repair_run_id_repair_run", "repair_round", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_repair_round_repair_run_id_repair_run",
        "repair_round",
        "repair_run",
        ["repair_run_id"],
        ["id"],
    )
    op.drop_constraint("fk_repair_run_attempt_id_attempt", "repair_run", type_="foreignkey")
    op.create_foreign_key(
        "fk_repair_run_attempt_id_attempt",
        "repair_run",
        "attempt",
        ["attempt_id"],
        ["id"],
    )
