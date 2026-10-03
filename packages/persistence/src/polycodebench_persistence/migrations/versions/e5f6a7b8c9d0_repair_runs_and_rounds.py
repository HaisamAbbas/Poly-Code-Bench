"""Self-repair runs: ordered immutable rounds, deliveries and the round-boundary checkpoint.

Self-repair is an explicitly versioned candidate protocol (Technical Spec 4.3, 17.2). One
``repair_run`` row carries the run's frontier, accumulated spend and selection state - that row
update committed together with a round insert IS the round-boundary checkpoint. Each ``repair_round``
is append-only and immutable like ``candidate``: rounds are evidence, not state. Deliveries are
separate immutable rows so an infrastructure redelivery adds a delivery and its cost without ever
editing a round, granting another repair round or erasing spent budget (Technical Spec 7.4/7.5).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "b9e04c7a1f38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "repair_run",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("attempt_id", sa.Uuid(), sa.ForeignKey("attempt.id"), nullable=False),
        sa.Column("repair_run_id", sa.String(128), nullable=False),
        sa.Column("protocol_digest", sa.String(71), nullable=False),
        sa.Column("protocol", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("public_case_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rounds_committed", sa.Integer(), nullable=False),
        sa.Column("spend", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("selected_round_index", sa.Integer(), nullable=True),
        sa.Column("run_digest", sa.String(71), nullable=False),
        sa.Column(
            "row_version", sa.BigInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("repair_run_id"),
        sa.CheckConstraint("rounds_committed >= 0", name="repair_rounds_committed_nonnegative"),
        sa.CheckConstraint("state IN ('open','complete')", name="repair_run_state"),
        sa.CheckConstraint(
            "state <> 'complete' OR selected_round_index IS NOT NULL",
            name="complete_run_selects_a_round",
        ),
        sa.CheckConstraint(
            "run_digest ~ '^sha256:[0-9a-f]{64}$'", name="repair_run_digest_format"
        ),
        sa.CheckConstraint("row_version >= 0", name="row_version_nonnegative"),
    )
    op.create_table(
        "repair_round",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("repair_run_id", sa.Uuid(), sa.ForeignKey("repair_run.id"), nullable=False),
        sa.Column("round_index", sa.Integer(), nullable=False),
        sa.Column("candidate_revision", sa.Integer(), nullable=False),
        sa.Column("candidate_digest", sa.String(71), nullable=True),
        sa.Column("prompt_digest", sa.String(71), nullable=False),
        sa.Column("request_digest", sa.String(71), nullable=False),
        sa.Column("feedback_digest", sa.String(71), nullable=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("public_results", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_micros", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("repair_run_id", "round_index"),
        sa.CheckConstraint("round_index >= 0", name="repair_round_index_nonnegative"),
        sa.CheckConstraint("candidate_revision = round_index + 1", name="repair_round_revision"),
        sa.CheckConstraint("state IN ('frozen','model_failure')", name="repair_round_state"),
        sa.CheckConstraint(
            "state <> 'frozen' OR candidate_digest IS NOT NULL", name="frozen_round_has_candidate"
        ),
        sa.CheckConstraint(
            "state <> 'model_failure' OR candidate_digest IS NULL",
            name="failed_round_has_no_candidate",
        ),
        sa.CheckConstraint(
            "round_index = 0 OR feedback_digest IS NOT NULL", name="repair_round_has_feedback"
        ),
    )
    op.create_table(
        "repair_delivery",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("repair_round_id", sa.Uuid(), sa.ForeignKey("repair_round.id"), nullable=False),
        sa.Column("delivery_index", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_micros", sa.BigInteger(), nullable=False),
        sa.Column("active_ms", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("repair_round_id", "delivery_index"),
        sa.CheckConstraint("delivery_index >= 1", name="repair_delivery_index_positive"),
        sa.CheckConstraint("input_tokens >= 0", name="repair_delivery_input_nonnegative"),
        sa.CheckConstraint("output_tokens >= 0", name="repair_delivery_output_nonnegative"),
        sa.CheckConstraint("cost_micros >= 0", name="repair_delivery_cost_nonnegative"),
        sa.CheckConstraint("active_ms >= 0", name="repair_delivery_active_nonnegative"),
    )
    op.create_index("ix_repair_round_run", "repair_round", ["repair_run_id"])
    op.create_index("ix_repair_delivery_round", "repair_delivery", ["repair_round_id"])
    op.execute(
        "CREATE TRIGGER version_guard_repair_run BEFORE UPDATE ON repair_run "
        "FOR EACH ROW EXECUTE FUNCTION pcb_require_row_version_step()"
    )
    op.execute(
        "CREATE TRIGGER immutable_repair_round BEFORE UPDATE OR DELETE ON repair_round "
        "FOR EACH ROW EXECUTE FUNCTION pcb_reject_immutable_change()"
    )
    op.execute(
        "CREATE TRIGGER immutable_repair_delivery BEFORE UPDATE OR DELETE ON repair_delivery "
        "FOR EACH ROW EXECUTE FUNCTION pcb_reject_immutable_change()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS immutable_repair_delivery ON repair_delivery")
    op.execute("DROP TRIGGER IF EXISTS immutable_repair_round ON repair_round")
    op.execute("DROP TRIGGER IF EXISTS version_guard_repair_run ON repair_run")
    op.drop_index("ix_repair_delivery_round", table_name="repair_delivery")
    op.drop_index("ix_repair_round_run", table_name="repair_round")
    op.drop_table("repair_delivery")
    op.drop_table("repair_round")
    op.drop_table("repair_run")
