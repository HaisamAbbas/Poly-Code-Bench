"""Judge execution records: cohorts, anonymized packets, every delivery, results, calibration labels.

The three-vote policy requires that no delivery disappears. This revision therefore adds one row
per dispatched judge delivery (``judge_delivery``) alongside the accepted vote, plus the
per-packet result that the scorer reads. Every new table is append-only and immutable: an
adjudication adds a row that supersedes an earlier one rather than editing it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b9e04c7a1f38"
down_revision: str | None = "b2f6d4a91c73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES = "('ready','needs_review','infra_blocked')"


def upgrade() -> None:
    bind = op.get_bind()
    existing = bind.execute(sa.text("SELECT count(*) FROM judge_packet")).scalar_one()
    if existing:
        # The judge workflow does not exist before this revision, so the table is empty. Adding
        # NOT NULL columns to rows written by an unknown writer would invent evidence.
        raise RuntimeError("judge_packet must be empty before judge workflow columns are added")

    op.create_table(
        "judge_cohort",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("cohort_id", sa.String(length=160), nullable=False),
        sa.Column("evaluation_version", sa.Integer(), nullable=False),
        sa.Column("panel_id", sa.String(length=160), nullable=False),
        sa.Column("panel_digest", sa.String(length=71), nullable=False),
        sa.Column("rubric_digest", sa.String(length=71), nullable=False),
        sa.Column("candidate_model_config_ids", postgresql.JSONB(), nullable=False),
        sa.Column("cohort_digest", sa.String(length=71), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "evaluation_version > 0", name=op.f("ck_judge_cohort_evaluation_version_positive")
        ),
        sa.CheckConstraint(
            "panel_digest ~ '^sha256:[0-9a-f]{64}$'",
            name=op.f("ck_judge_cohort_panel_digest_format"),
        ),
        sa.CheckConstraint(
            "cohort_digest ~ '^sha256:[0-9a-f]{64}$'",
            name=op.f("ck_judge_cohort_cohort_digest_format"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_judge_cohort")),
        sa.UniqueConstraint(
            "cohort_id", "evaluation_version", name=op.f("uq_judge_cohort_cohort_id")
        ),
    )

    op.add_column(
        "judge_packet",
        # The canonical packet bytes are the evidence a reviewer re-reads, so they are required.
        sa.Column("packet_artifact_id", sa.Uuid(), nullable=False),
    )
    # A cohort may be registered later; a packet run without one is still valid evidence.
    op.add_column("judge_packet", sa.Column("cohort_id", sa.Uuid(), nullable=True))
    op.add_column("judge_packet", sa.Column("language", sa.String(length=16), nullable=False))
    op.add_column(
        "judge_packet",
        sa.Column(
            "packet_role", sa.String(length=16), server_default=sa.text("'scored'"), nullable=False
        ),
    )
    op.create_check_constraint(
        op.f("ck_judge_packet_language"), "judge_packet", "language IN ('python','rust')"
    )
    op.create_check_constraint(
        op.f("ck_judge_packet_packet_role"),
        "judge_packet",
        "packet_role IN ('scored','calibration')",
    )
    op.create_check_constraint(
        op.f("ck_judge_packet_packet_digest_format"),
        "judge_packet",
        "packet_digest ~ '^sha256:[0-9a-f]{64}$'",
    )
    op.create_foreign_key(
        op.f("fk_judge_packet_packet_artifact_id_artifact"),
        "judge_packet",
        "artifact",
        ["packet_artifact_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_judge_packet_cohort_id_judge_cohort"),
        "judge_packet",
        "judge_cohort",
        ["cohort_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "judge_delivery",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("packet_id", sa.Uuid(), nullable=False),
        sa.Column("vote_index", sa.Integer(), nullable=False),
        sa.Column("delivery_index", sa.Integer(), nullable=False),
        sa.Column("call_delivery_id", sa.Uuid(), nullable=True),
        sa.Column("call_intent_id", sa.Uuid(), nullable=True),
        sa.Column("raw_artifact_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("invalid_reason", sa.String(length=48), nullable=True),
        sa.Column("detail", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("judge_revision", sa.String(length=128), nullable=False),
        sa.Column("seed", sa.BigInteger(), nullable=True),
        sa.Column("seed_supported", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("repair_instruction_id", sa.String(length=64), nullable=True),
        sa.Column("raw_response_digest", sa.String(length=71), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "vote_index >= 0", name=op.f("ck_judge_delivery_vote_index_nonnegative")
        ),
        sa.CheckConstraint(
            "delivery_index >= 0 AND delivery_index <= 2",
            name=op.f("ck_judge_delivery_delivery_index_bound"),
        ),
        sa.CheckConstraint(
            "status IN ('valid','invalid','transport_failure')",
            name=op.f("ck_judge_delivery_status"),
        ),
        sa.CheckConstraint(
            "(status = 'invalid') = (invalid_reason IS NOT NULL)",
            name=op.f("ck_judge_delivery_invalid_reason_matches_status"),
        ),
        sa.CheckConstraint(
            "seed IS NULL OR seed >= 0", name=op.f("ck_judge_delivery_seed_nonnegative")
        ),
        sa.ForeignKeyConstraint(
            ["call_delivery_id"],
            ["call_delivery.id"],
            name=op.f("fk_judge_delivery_call_delivery_id_call_delivery"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["call_intent_id"],
            ["call_intent.id"],
            name=op.f("fk_judge_delivery_call_intent_id_call_intent"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["packet_id"],
            ["judge_packet.id"],
            name=op.f("fk_judge_delivery_packet_id_judge_packet"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["raw_artifact_id"],
            ["artifact.id"],
            name=op.f("fk_judge_delivery_raw_artifact_id_artifact"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_judge_delivery")),
        sa.UniqueConstraint(
            "packet_id", "vote_index", "delivery_index", name=op.f("uq_judge_delivery_packet_id")
        ),
    )
    op.create_index(
        op.f("ix_judge_delivery_packet_status"),
        "judge_delivery",
        ["packet_id", "status"],
        unique=False,
    )

    op.create_check_constraint(
        op.f("ck_judge_vote_status_is_valid"), "judge_vote", "status = 'valid'"
    )

    op.create_table(
        "judge_result",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("packet_id", sa.Uuid(), nullable=False),
        sa.Column("evaluation_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("report_artifact_id", sa.Uuid(), nullable=False),
        sa.Column("report_digest", sa.String(length=71), nullable=False),
        sa.Column("result_index", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(f"status IN {_STATUSES}", name=op.f("ck_judge_result_status")),
        sa.CheckConstraint(
            "result_index >= 0", name=op.f("ck_judge_result_result_index_nonnegative")
        ),
        sa.CheckConstraint(
            "report_digest ~ '^sha256:[0-9a-f]{64}$'",
            name=op.f("ck_judge_result_report_digest_format"),
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_id"],
            ["evaluation.id"],
            name=op.f("fk_judge_result_evaluation_id_evaluation"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["packet_id"],
            ["judge_packet.id"],
            name=op.f("fk_judge_result_packet_id_judge_packet"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["report_artifact_id"],
            ["artifact.id"],
            name=op.f("fk_judge_result_report_artifact_id_artifact"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_judge_result")),
        sa.UniqueConstraint("packet_id", "result_index", name=op.f("uq_judge_result_packet_id")),
    )

    op.create_table(
        "judge_item_result",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("result_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.String(length=64), nullable=False),
        sa.Column("dimension", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("mean_score", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("vote_count", sa.Integer(), nullable=False),
        sa.Column("required_votes", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("adjudication_id", sa.Uuid(), nullable=True),
        sa.Column("triggers", postgresql.JSONB(), nullable=False),
        sa.Column("vote_scores", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(f"status IN {_STATUSES}", name=op.f("ck_judge_item_result_status")),
        sa.CheckConstraint(
            "source IN ('judge_votes','adjudication')", name=op.f("ck_judge_item_result_source")
        ),
        sa.CheckConstraint(
            "mean_score IS NULL OR (mean_score >= 0 AND mean_score <= 1)",
            name=op.f("ck_judge_item_result_mean_range"),
        ),
        sa.CheckConstraint(
            "vote_count >= 0 AND vote_count <= required_votes",
            name=op.f("ck_judge_item_result_vote_count_range"),
        ),
        sa.CheckConstraint(
            "(source = 'adjudication') = (adjudication_id IS NOT NULL)",
            name=op.f("ck_judge_item_result_adjudication_matches_source"),
        ),
        sa.ForeignKeyConstraint(
            ["adjudication_id"],
            ["adjudication.id"],
            name=op.f("fk_judge_item_result_adjudication_id_adjudication"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["result_id"],
            ["judge_result.id"],
            name=op.f("fk_judge_item_result_result_id_judge_result"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_judge_item_result")),
        sa.UniqueConstraint("result_id", "item_id", name=op.f("uq_judge_item_result_result_id")),
    )

    op.create_table(
        "calibration_label",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("cohort_id", sa.Uuid(), nullable=False),
        sa.Column("packet_id", sa.Uuid(), nullable=False),
        sa.Column("packet_digest", sa.String(length=71), nullable=False),
        sa.Column("item_id", sa.String(length=64), nullable=False),
        sa.Column("score", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("labeler_subject", sa.String(length=255), nullable=False),
        sa.Column("qualification", sa.String(length=64), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("cited_anchor_ids", postgresql.JSONB(), nullable=False),
        sa.Column("labeled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "score >= 0 AND score <= 1", name=op.f("ck_calibration_label_score_range")
        ),
        sa.CheckConstraint(
            "packet_digest ~ '^sha256:[0-9a-f]{64}$'",
            name=op.f("ck_calibration_label_packet_digest_format"),
        ),
        sa.ForeignKeyConstraint(
            ["cohort_id"],
            ["judge_cohort.id"],
            name=op.f("fk_calibration_label_cohort_id_judge_cohort"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["packet_id"],
            ["judge_packet.id"],
            name=op.f("fk_calibration_label_packet_id_judge_packet"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calibration_label")),
        sa.UniqueConstraint(
            "packet_digest",
            "item_id",
            "labeler_subject",
            name=op.f("uq_calibration_label_packet_digest"),
        ),
    )

    for table in (
        "judge_cohort",
        "judge_delivery",
        "judge_result",
        "judge_item_result",
        "calibration_label",
    ):
        op.execute(
            sa.text(
                f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION pcb_reject_immutable_change()"
            )
        )


def downgrade() -> None:
    # Judge evidence decides published scores. Dropping deliveries, votes or adjudications would
    # destroy the record of how a score was produced, so this revision does not reverse.
    raise NotImplementedError("Judge execution evidence must be preserved.")
