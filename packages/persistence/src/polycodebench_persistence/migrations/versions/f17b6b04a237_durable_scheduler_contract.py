"""Add scheduler resource classes, progress events and one-time execution finalization."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f17b6b04a237"
down_revision: str | None = "e8c51d90ab73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("stage_job", sa.Column("input_artifact_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_stage_job_input_artifact_id_artifact",
        "stage_job",
        "artifact",
        ["input_artifact_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "stage_job",
        sa.Column(
            "resource_class", sa.String(64), server_default=sa.text("'default'"), nullable=False
        ),
    )
    op.add_column("stage_job", sa.Column("fairness_campaign_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_stage_job_fairness_campaign_id_campaign",
        "stage_job",
        "campaign",
        ["fairness_campaign_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "stage_job",
        sa.Column(
            "provider_key", sa.String(128), server_default=sa.text("'system'"), nullable=False
        ),
    )
    op.add_column("stage_job", sa.Column("quality_gate", sa.String(24), nullable=True))
    op.add_column("stage_job", sa.Column("skip_reason", sa.String(64), nullable=True))
    # Preserve old skip records without treating them as an accepted branch skip.
    op.execute("UPDATE stage_job SET skip_reason='legacy_unspecified' WHERE state='skipped'")
    op.create_check_constraint(
        "quality_gate",
        "stage_job",
        "quality_gate IS NULL OR quality_gate IN ('pass','fail','unknown','not_applicable')",
    )
    op.create_check_constraint(
        "skip_reason_shape",
        "stage_job",
        "(state = 'skipped' AND skip_reason IS NOT NULL) OR "
        "(state <> 'skipped' AND skip_reason IS NULL)",
    )
    op.create_index(
        "ix_stage_job_fairness",
        "stage_job",
        ["fairness_campaign_id", "provider_key", "state"],
    )

    op.add_column(
        "stage_dependency",
        sa.Column("condition", sa.String(24), server_default=sa.text("'success'"), nullable=False),
    )
    op.add_column(
        "stage_dependency",
        sa.Column(
            "accepted_skip_reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "condition",
        "stage_dependency",
        "condition IN ('success','gate_pass','gate_fail','terminal')",
    )

    op.add_column(
        "worker_registration",
        sa.Column(
            "allowed_resource_classes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "capacity_slot",
        sa.Column(
            "resource_class", sa.String(64), server_default=sa.text("'default'"), nullable=False
        ),
    )
    op.drop_constraint(op.f("ck_capacity_slot_state"), "capacity_slot", type_="check")
    op.drop_constraint(op.f("ck_capacity_slot_busy_has_job"), "capacity_slot", type_="check")
    op.create_check_constraint(
        "state",
        "capacity_slot",
        "state IN ('available','reserved','busy','cleanup','draining','disabled')",
    )
    op.create_check_constraint(
        "assignment_shape",
        "capacity_slot",
        "(state IN ('reserved','busy','cleanup') AND job_id IS NOT NULL AND fence IS NOT NULL) OR "
        "(state IN ('available','draining','disabled') AND job_id IS NULL AND fence IS NULL "
        "AND guest_id IS NULL)",
    )
    op.create_index(
        "uq_capacity_slot_active_job",
        "capacity_slot",
        ["job_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('reserved','busy','cleanup')"),
    )

    op.create_table(
        "stage_job_event",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("event_seq", sa.BigInteger(), nullable=False),
        sa.Column("event_kind", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("fence", sa.BigInteger(), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("event_seq >= 1", name="event_seq_positive"),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["stage_job.id"],
            name="fk_stage_job_event_job_id_stage_job",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_stage_job_event"),
        sa.UniqueConstraint("job_id", "event_seq", name="uq_stage_job_event_job_id"),
    )
    op.create_index("ix_stage_job_event_job", "stage_job_event", ["job_id", "event_seq"])

    # Stage executions are immutable once finalized. The initial persistence migration used a
    # fully immutable trigger, which made the spec's single finalization transaction impossible.
    op.execute("DROP TRIGGER immutable_stage_execution ON stage_execution")
    op.execute("DROP FUNCTION IF EXISTS pcb_guard_stage_execution_finalize()")
    op.execute(
        """
        CREATE FUNCTION pcb_guard_stage_execution_finalize() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'stage execution records cannot be deleted' USING ERRCODE = '23514';
          END IF;
          IF OLD.finished_at IS NOT NULL OR NEW.finished_at IS NULL OR NEW.result IS NULL THEN
            RAISE EXCEPTION 'stage execution can only be finalized once' USING ERRCODE = '23514';
          END IF;
          IF NEW.id IS DISTINCT FROM OLD.id
             OR NEW.job_id IS DISTINCT FROM OLD.job_id
             OR NEW.fence IS DISTINCT FROM OLD.fence
             OR NEW.worker_id IS DISTINCT FROM OLD.worker_id
             OR NEW.started_at IS DISTINCT FROM OLD.started_at
             OR NEW.environment_artifact_id IS DISTINCT FROM OLD.environment_artifact_id
             OR (NEW.finished_at < OLD.started_at)
             OR (NEW.result = 'succeeded' AND NEW.failure_class IS NOT NULL)
             OR (NEW.result <> 'succeeded' AND NEW.failure_class IS NULL) THEN
            RAISE EXCEPTION 'stage execution finalization changes immutable identity or is invalid'
              USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER stage_execution_finalize_guard
          BEFORE UPDATE OR DELETE ON stage_execution
          FOR EACH ROW EXECUTE FUNCTION pcb_guard_stage_execution_finalize();
        """
    )
    op.execute(
        "CREATE TRIGGER immutable_stage_job_event BEFORE UPDATE OR DELETE ON stage_job_event "
        "FOR EACH ROW EXECUTE FUNCTION pcb_reject_immutable_change()"
    )

    op.execute("GRANT SELECT, INSERT ON stage_dependency, stage_job_event TO pcb_scheduler")
    op.execute(
        "GRANT UPDATE (finished_at, result, failure_class, output_manifest_id) "
        "ON stage_execution TO pcb_scheduler"
    )
    op.execute(
        "GRANT SELECT ON attempt, run, campaign, evaluation, release, config_document, artifact "
        "TO pcb_scheduler"
    )
    op.execute("GRANT UPDATE (state, failure_class, row_version) ON attempt TO pcb_scheduler")
    op.execute("GRANT UPDATE (state, row_version) ON evaluation TO pcb_scheduler")


def downgrade() -> None:
    raise NotImplementedError(
        "Scheduler deliveries and progress events are durable execution evidence."
    )
