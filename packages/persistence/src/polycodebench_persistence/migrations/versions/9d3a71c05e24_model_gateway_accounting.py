"""Model gateway: endpoint approval evidence, per-delivery reservations and bucketed ledger.

The technical specification lists UQ(account_id, call_intent_id) for reservations. A retry
after an ambiguous delivery must reserve additional exposure while the earlier exposure is
retained, so the uniqueness is widened to include the delivery index (recorded as ADR-0008).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "9d3a71c05e24"
down_revision: str | None = "8ac42e1d09bf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Endpoint registration carries the resolved policy, declared capabilities and approval.
    op.add_column(
        "endpoint_registration",
        sa.Column("network_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    )
    op.add_column(
        "endpoint_registration",
        sa.Column("declared_capabilities", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    )
    op.add_column(
        "endpoint_registration",
        sa.Column("conformance_report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "endpoint_registration", sa.Column("approved_by", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "endpoint_registration",
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("endpoint_registration", sa.Column("decision_reason", sa.Text(), nullable=True))
    op.create_check_constraint(
        "approved_has_approver",
        "endpoint_registration",
        "approval_status <> 'approved' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)",
    )
    op.execute("""
    CREATE FUNCTION pcb_guard_endpoint_registration() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.provider_kind <> OLD.provider_kind
           OR NEW.base_url_ref <> OLD.base_url_ref
           OR NEW.secret_ref <> OLD.secret_ref
           OR NEW.network_policy_id <> OLD.network_policy_id
           OR NEW.network_policy <> OLD.network_policy
           OR NEW.declared_capabilities <> OLD.declared_capabilities
           OR NEW.capabilities_digest <> OLD.capabilities_digest
           OR NEW.registered_by <> OLD.registered_by THEN
            RAISE EXCEPTION 'endpoint registration identity is immutable; register a new endpoint'
                USING ERRCODE = '55000';
        END IF;
        IF OLD.approval_status IN ('rejected', 'revoked') AND NEW.approval_status <> OLD.approval_status THEN
            RAISE EXCEPTION 'a rejected or revoked endpoint cannot be reopened'
                USING ERRCODE = '55000';
        END IF;
        RETURN NEW;
    END;
    $$;
    CREATE TRIGGER endpoint_registration_guard BEFORE UPDATE ON endpoint_registration
        FOR EACH ROW EXECUTE FUNCTION pcb_guard_endpoint_registration();
    """)

    # Hierarchical accounts and non-monetary resource limits.
    op.add_column(
        "budget_account", sa.Column("parent_account_id", sa.Uuid(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        op.f("fk_budget_account_parent_account_id_budget_account"),
        "budget_account",
        "budget_account",
        ["parent_account_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "scope_kind",
        "budget_account",
        "scope_kind IN ('campaign','run','attempt','evaluation')",
    )
    op.create_table(
        "budget_resource",
        sa.Column(
            "id", sa.Uuid(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("account_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("resource", sa.String(length=24), nullable=False),
        sa.Column("hard_limit", sa.BigInteger(), nullable=False),
        sa.Column("spent_confirmed", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("reserved_open", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "uncertain_committed", sa.BigInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("row_version", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint(
            "resource IN ('turns','input_tokens','output_tokens')",
            name=op.f("ck_budget_resource_resource"),
        ),
        sa.CheckConstraint(
            "hard_limit >= 0", name=op.f("ck_budget_resource_hard_limit_nonnegative")
        ),
        sa.CheckConstraint(
            "spent_confirmed >= 0 AND reserved_open >= 0 AND uncertain_committed >= 0",
            name=op.f("ck_budget_resource_balance_nonnegative"),
        ),
        sa.CheckConstraint(
            "row_version >= 0", name=op.f("ck_budget_resource_row_version_nonnegative")
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["budget_account.id"],
            name=op.f("fk_budget_resource_account_id_budget_account"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_budget_resource")),
        sa.UniqueConstraint("account_id", "resource", name=op.f("uq_budget_resource_account_id")),
    )
    op.execute(
        "CREATE TRIGGER version_guard_budget_resource BEFORE UPDATE ON budget_resource "
        "FOR EACH ROW EXECUTE FUNCTION pcb_require_row_version_step();"
    )

    # Reservations are per delivery; retries reserve additional exposure.
    op.add_column(
        "budget_reservation",
        sa.Column("delivery_index", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.drop_constraint(
        op.f("uq_budget_reservation_account_id"), "budget_reservation", type_="unique"
    )
    op.create_unique_constraint(
        op.f("uq_budget_reservation_account_id"),
        "budget_reservation",
        ["account_id", "call_intent_id", "delivery_index"],
    )
    op.create_check_constraint(
        "delivery_index_nonnegative", "budget_reservation", "delivery_index >= 0"
    )

    # Ledger entries move amounts between named buckets; balances are derivable from them.
    op.add_column(
        "accounting_entry",
        sa.Column(
            "resource", sa.String(length=24), server_default=sa.text("'money'"), nullable=False
        ),
    )
    op.add_column(
        "accounting_entry",
        sa.Column(
            "from_bucket", sa.String(length=24), server_default=sa.text("'none'"), nullable=False
        ),
    )
    op.add_column(
        "accounting_entry",
        sa.Column(
            "to_bucket", sa.String(length=24), server_default=sa.text("'none'"), nullable=False
        ),
    )
    op.drop_constraint(op.f("ck_accounting_entry_entry_kind"), "accounting_entry", type_="check")
    op.create_check_constraint(
        "entry_kind",
        "accounting_entry",
        "entry_kind IN ('reservation','charge','release','adjustment','retain')",
    )
    op.create_check_constraint(
        "resource",
        "accounting_entry",
        "resource IN ('money','turns','input_tokens','output_tokens')",
    )
    op.create_check_constraint(
        "buckets",
        "accounting_entry",
        "from_bucket IN ('none','reserved_open','uncertain_committed','spent_confirmed') AND "
        "to_bucket IN ('none','reserved_open','uncertain_committed','spent_confirmed') AND "
        "from_bucket <> to_bucket",
    )
    op.create_check_constraint("amount_positive", "accounting_entry", "amount_micro_usd > 0")

    op.add_column(
        "call_delivery",
        sa.Column("normalized_response_artifact_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_call_delivery_normalized_response_artifact_id_artifact"),
        "call_delivery",
        "artifact",
        ["normalized_response_artifact_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    # A delivery is evidence, but its outcome is learned after dispatch. The blanket
    # immutability trigger is replaced by a transition guard: identity is fixed, each outcome
    # field is write-once, and terminal states never change.
    op.execute("""
    DROP TRIGGER immutable_call_delivery ON call_delivery;
    CREATE FUNCTION pcb_guard_call_delivery() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'call deliveries are permanent evidence' USING ERRCODE = '55000';
        END IF;
        IF NEW.id <> OLD.id OR NEW.intent_id <> OLD.intent_id
           OR NEW.delivery_index <> OLD.delivery_index OR NEW.dispatched_at <> OLD.dispatched_at THEN
            RAISE EXCEPTION 'call delivery identity is immutable' USING ERRCODE = '55000';
        END IF;
        IF (OLD.provider_request_id IS NOT NULL AND NEW.provider_request_id IS DISTINCT FROM OLD.provider_request_id)
           OR (OLD.raw_response_artifact_id IS NOT NULL AND NEW.raw_response_artifact_id IS DISTINCT FROM OLD.raw_response_artifact_id)
           OR (OLD.normalized_response_artifact_id IS NOT NULL AND NEW.normalized_response_artifact_id IS DISTINCT FROM OLD.normalized_response_artifact_id)
           OR (OLD.responded_at IS NOT NULL AND NEW.responded_at IS DISTINCT FROM OLD.responded_at) THEN
            RAISE EXCEPTION 'call delivery outcome fields are write-once' USING ERRCODE = '55000';
        END IF;
        IF OLD.status IN ('responded', 'failed') AND NEW.status <> OLD.status THEN
            RAISE EXCEPTION 'terminal call delivery status cannot change' USING ERRCODE = '55000';
        END IF;
        IF OLD.status = 'ambiguous' AND NEW.status NOT IN ('ambiguous', 'responded') THEN
            RAISE EXCEPTION 'an ambiguous delivery can only be resolved by a first-arriving response' USING ERRCODE = '55000';
        END IF;
        IF OLD.failure_code IS NOT NULL AND NEW.failure_code IS DISTINCT FROM OLD.failure_code
           AND NEW.failure_code <> 'late_response_not_consumed' THEN
            RAISE EXCEPTION 'call delivery failure code is write-once' USING ERRCODE = '55000';
        END IF;
        RETURN NEW;
    END;
    $$;
    CREATE TRIGGER call_delivery_guard BEFORE UPDATE OR DELETE ON call_delivery
        FOR EACH ROW EXECUTE FUNCTION pcb_guard_call_delivery();
    """)
    op.create_index(
        "uq_call_delivery_one_response",
        "call_delivery",
        ["intent_id"],
        unique=True,
        postgresql_where=sa.text("status = 'responded'"),
    )
    op.create_index(
        "uq_call_delivery_one_in_flight",
        "call_delivery",
        ["intent_id"],
        unique=True,
        postgresql_where=sa.text("status = 'dispatching'"),
    )
    op.create_check_constraint(
        "source",
        "usage_record",
        "source IN ('provider_reported','unavailable','reconciliation')",
    )


def downgrade() -> None:
    raise NotImplementedError("Model call accounting is durable financial evidence.")
