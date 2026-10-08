"""Persist bounded monitor schedules, reservations, and redacted in-app alerts."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "e5c7b2a94d10"
down_revision: str | None = "d4f7b2a196c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AUDIT_DOCUMENT_KINDS = (
    "'benchmark_snapshot','task_fingerprint','corpus_snapshot','audit_plan',"
    "'query_manifest','coverage_manifest','match_evidence','model_context','risk_policy',"
    "'risk_assessment','temporal_assessment','sealed_manifest','canary_policy',"
    "'seal_access_event','canary_observation','behavioral_audit_plan',"
    "'behavioral_method_registry','behavioral_task_validity','behavioral_observation',"
    "'behavioral_assessment','firewall_policy','firewall_scope','firewall_decision',"
    "'replacement_source_metadata','replacement_plan','replacement_validation',"
    "'derived_benchmark_manifest','monitor_policy','monitor_alert','benchmark_health',"
    "'audit_attestation'"
)


def upgrade() -> None:
    op.drop_constraint(op.f("ck_audit_document_kind"), "audit_document", type_="check")
    op.create_check_constraint(
        op.f("ck_audit_document_kind"),
        "audit_document",
        f"kind IN ({_AUDIT_DOCUMENT_KINDS})",
    )
    op.create_check_constraint(
        op.f("ck_audit_document_monitor_policy_successors"),
        "audit_document",
        "kind <> 'monitor_policy' OR schema_version = 2 OR supersedes_id IS NULL",
    )
    op.create_index(
        "uq_monitor_policy_benchmark_version",
        "audit_document",
        [
            text("(payload->'benchmark_ref'->>'document_id')"),
            text("(payload->>'policy_version')"),
        ],
        unique=True,
        postgresql_where=text("kind = 'monitor_policy' AND schema_version = 2"),
    )
    op.create_index(
        "uq_monitor_policy_single_successor",
        "audit_document",
        ["supersedes_id"],
        unique=True,
        postgresql_where=text("kind = 'monitor_policy' AND supersedes_id IS NOT NULL"),
    )
    op.create_index(
        "uq_monitor_alert_policy_dedupe",
        "audit_document",
        [
            text("(payload->'policy_ref'->>'document_id')"),
            text("(payload->>'dedupe_key')"),
        ],
        unique=True,
        postgresql_where=text("kind = 'monitor_alert'"),
    )

    op.create_table(
        "monitor_slot",
        Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
        ),
        Column(
            "policy_document_id",
            UUID(as_uuid=True),
            ForeignKey("audit_document.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        Column(
            "audit_run_id",
            UUID(as_uuid=True),
            ForeignKey("audit_run.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        Column("slot_key", String(32), nullable=False),
        Column("scheduled_at", DateTime(timezone=True), nullable=False),
        Column("local_day", Date, nullable=False),
        Column("refresh_kind", String(16), nullable=False),
        Column("source_document_ids", JSONB, nullable=False),
        Column("state", String(24), nullable=False),
        Column("dispatch_authorized", Boolean, nullable=False, server_default=text("false")),
        Column("query_units", BigInteger, nullable=False, server_default=text("0")),
        Column("retry_reserve_units", BigInteger, nullable=False, server_default=text("0")),
        Column("reserved_storage_bytes", BigInteger, nullable=False, server_default=text("0")),
        Column("reserved_cost_micro_usd", BigInteger, nullable=False, server_default=text("0")),
        Column("missed_slots_before", Integer, nullable=False, server_default=text("0")),
        Column("attempts", Integer, nullable=False, server_default=text("0")),
        Column("max_attempts", Integer, nullable=False),
        Column("next_retry_at", DateTime(timezone=True), nullable=True),
        Column("last_error_code", String(40), nullable=True),
        Column("completed_at", DateTime(timezone=True), nullable=True),
        Column("row_version", BigInteger, nullable=False, server_default=text("0")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
        UniqueConstraint(
            "policy_document_id", "slot_key", name=op.f("uq_monitor_slot_policy_slot")
        ),
        CheckConstraint("length(slot_key) BETWEEN 1 AND 32", name=op.f("ck_monitor_slot_slot_key")),
        CheckConstraint(
            "refresh_kind IN ('incremental','full')",
            name=op.f("ck_monitor_slot_refresh_kind"),
        ),
        CheckConstraint(
            "state IN ('missed','planned','retry_wait','queued','running','complete','partial',"
            "'failed','blocked','cancelled')",
            name=op.f("ck_monitor_slot_state"),
        ),
        CheckConstraint(
            "state NOT IN ('queued','running','complete') OR "
            "(dispatch_authorized AND audit_run_id IS NOT NULL)",
            name=op.f("ck_monitor_slot_authorized_dispatch"),
        ),
        CheckConstraint(
            "jsonb_typeof(source_document_ids) = 'array'",
            name=op.f("ck_monitor_slot_sources_array"),
        ),
        CheckConstraint(
            "query_units >= 0 AND retry_reserve_units >= query_units "
            "AND reserved_storage_bytes >= 0 AND reserved_cost_micro_usd >= 0 "
            "AND missed_slots_before >= 0",
            name=op.f("ck_monitor_slot_reservations"),
        ),
        CheckConstraint(
            "attempts >= 0 AND attempts <= max_attempts",
            name=op.f("ck_monitor_slot_attempts"),
        ),
        CheckConstraint("max_attempts BETWEEN 1 AND 11", name=op.f("ck_monitor_slot_max_attempts")),
        CheckConstraint(
            "(state = 'retry_wait') = (next_retry_at IS NOT NULL)",
            name=op.f("ck_monitor_slot_retry_schedule"),
        ),
        CheckConstraint(
            "last_error_code IS NULL OR last_error_code IN "
            "('source_unavailable','rate_limited','connector_error','coverage_incomplete',"
            "'budget_exhausted','authorization_required')",
            name=op.f("ck_monitor_slot_error_code"),
        ),
        CheckConstraint("row_version >= 0", name=op.f("ck_monitor_slot_row_version_nonnegative")),
    )
    op.create_index(
        "ix_monitor_slot_due", "monitor_slot", ["state", "next_retry_at", "scheduled_at"]
    )
    op.create_index(
        "ix_monitor_slot_policy_day", "monitor_slot", ["policy_document_id", "local_day"]
    )
    op.create_table(
        "monitor_slot_source",
        Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
        ),
        Column(
            "slot_id",
            UUID(as_uuid=True),
            ForeignKey("monitor_slot.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        Column(
            "policy_document_id",
            UUID(as_uuid=True),
            ForeignKey("audit_document.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        Column(
            "source_document_id",
            UUID(as_uuid=True),
            ForeignKey("audit_document.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        Column("local_day", Date, nullable=False),
        Column("reserved_query_units", BigInteger, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
        UniqueConstraint("slot_id", "source_document_id", name=op.f("uq_monitor_slot_source")),
        CheckConstraint(
            "reserved_query_units >= 1",
            name=op.f("ck_monitor_slot_source_reserved_units_positive"),
        ),
    )
    op.create_index(
        "ix_monitor_source_daily_reservations",
        "monitor_slot_source",
        ["policy_document_id", "source_document_id", "local_day"],
    )
    op.create_table(
        "monitor_alert_inbox",
        Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
        ),
        Column(
            "alert_document_id",
            UUID(as_uuid=True),
            ForeignKey("audit_document.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        Column("recipient_subject", String(512), nullable=False),
        Column("read_at", DateTime(timezone=True), nullable=True),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
        UniqueConstraint(
            "alert_document_id",
            "recipient_subject",
            name=op.f("uq_monitor_alert_inbox_recipient"),
        ),
        CheckConstraint(
            "length(recipient_subject) BETWEEN 1 AND 512",
            name=op.f("ck_monitor_alert_inbox_recipient_subject"),
        ),
    )
    op.create_index(
        "ix_monitor_alert_inbox_recipient",
        "monitor_alert_inbox",
        ["recipient_subject", "created_at"],
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM monitor_slot)
                OR EXISTS (SELECT 1 FROM monitor_slot_source)
                OR EXISTS (SELECT 1 FROM monitor_alert_inbox)
                OR EXISTS (
                    SELECT 1 FROM audit_document
                    WHERE kind = 'monitor_alert'
                       OR (kind = 'monitor_policy' AND schema_version = 2)
                ) THEN
                RAISE EXCEPTION
                    'cannot remove continuous monitoring contracts while evidence exists';
            END IF;
        END;
        $$
        """
    )
    op.drop_index("ix_monitor_alert_inbox_recipient", table_name="monitor_alert_inbox")
    op.drop_table("monitor_alert_inbox")
    op.drop_index("ix_monitor_source_daily_reservations", table_name="monitor_slot_source")
    op.drop_table("monitor_slot_source")
    op.drop_index("ix_monitor_slot_policy_day", table_name="monitor_slot")
    op.drop_index("ix_monitor_slot_due", table_name="monitor_slot")
    op.drop_table("monitor_slot")
    for name in (
        "uq_monitor_alert_policy_dedupe",
        "uq_monitor_policy_single_successor",
        "uq_monitor_policy_benchmark_version",
    ):
        op.drop_index(name, table_name="audit_document")
    op.drop_constraint(
        op.f("ck_audit_document_monitor_policy_successors"), "audit_document", type_="check"
    )
    op.drop_constraint(op.f("ck_audit_document_kind"), "audit_document", type_="check")
    op.create_check_constraint(
        op.f("ck_audit_document_kind"),
        "audit_document",
        "kind IN ('benchmark_snapshot','task_fingerprint','corpus_snapshot','audit_plan',"
        "'query_manifest','coverage_manifest','match_evidence','model_context','risk_policy',"
        "'risk_assessment','temporal_assessment','sealed_manifest','canary_policy',"
        "'seal_access_event','canary_observation','behavioral_audit_plan',"
        "'behavioral_method_registry','behavioral_task_validity','behavioral_observation',"
        "'behavioral_assessment','firewall_policy','firewall_scope','firewall_decision',"
        "'replacement_source_metadata','replacement_plan','replacement_validation',"
        "'derived_benchmark_manifest','monitor_policy','benchmark_health','audit_attestation')",
    )
