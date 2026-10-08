"""Persist finite-scope firewall decisions and bounded replacement evidence."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "d4f7b2a196c3"
down_revision: str | None = "a194d6c3e781"
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
    "'derived_benchmark_manifest','monitor_policy','benchmark_health','audit_attestation'"
)


def upgrade() -> None:
    op.drop_constraint(op.f("ck_audit_document_kind"), "audit_document", type_="check")
    op.create_check_constraint(
        op.f("ck_audit_document_kind"),
        "audit_document",
        f"kind IN ({_AUDIT_DOCUMENT_KINDS})",
    )
    op.create_check_constraint(
        op.f("ck_audit_document_p95_successors"),
        "audit_document",
        "kind NOT IN ('firewall_policy','firewall_scope','replacement_source_metadata',"
        "'replacement_plan','replacement_validation','derived_benchmark_manifest',"
        "'firewall_decision') "
        "OR (kind IN ('firewall_decision','replacement_plan') AND schema_version = 1) "
        "OR (kind = 'firewall_decision' AND schema_version = 2) OR supersedes_id IS NULL",
    )
    op.create_index(
        "uq_firewall_policy_benchmark_version",
        "audit_document",
        [
            text("(payload->'benchmark_ref'->>'document_id')"),
            text("(payload->>'policy_version')"),
        ],
        unique=True,
        postgresql_where=text("kind = 'firewall_policy' AND schema_version = 2"),
    )
    op.create_index(
        "uq_firewall_scope_policy_task",
        "audit_document",
        [
            text("(payload->'policy_ref'->>'document_id')"),
            text("(payload->'task_ref'->>'entity_id')"),
        ],
        unique=True,
        postgresql_where=text("kind = 'firewall_scope' AND supersedes_id IS NULL"),
    )
    op.create_index(
        "uq_firewall_decision_policy_task_head",
        "audit_document",
        [
            text("(payload->'policy_ref'->>'document_id')"),
            text("(payload->'task_ref'->>'entity_id')"),
        ],
        unique=True,
        postgresql_where=text(
            "kind = 'firewall_decision' AND schema_version = 2 AND supersedes_id IS NULL"
        ),
    )
    op.create_index(
        "uq_firewall_decision_single_successor",
        "audit_document",
        ["supersedes_id"],
        unique=True,
        postgresql_where=text(
            "kind = 'firewall_decision' AND schema_version = 2 AND supersedes_id IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_replacement_validation_plan_task_draft",
        "audit_document",
        [
            text("(payload->'plan_ref'->>'document_id')"),
            text("(payload->'task_ref'->>'entity_id')"),
            text("(payload->>'draft_index')"),
        ],
        unique=True,
        postgresql_where=text("kind = 'replacement_validation' AND supersedes_id IS NULL"),
    )
    op.create_index(
        "uq_derived_benchmark_version",
        "audit_document",
        [
            text("(payload->'official_snapshot_ref'->>'document_id')"),
            text("(payload->>'derived_version')"),
        ],
        unique=True,
        postgresql_where=text("kind = 'derived_benchmark_manifest' AND supersedes_id IS NULL"),
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM audit_document
                WHERE kind IN (
                    'firewall_policy','firewall_scope','replacement_source_metadata',
                    'replacement_validation','derived_benchmark_manifest'
                ) OR (kind IN ('firewall_decision','replacement_plan') AND schema_version = 2)
            ) THEN
                RAISE EXCEPTION
                    'cannot remove firewall/replacement contracts while evidence exists';
            END IF;
        END;
        $$
        """
    )
    for index_name in (
        "uq_derived_benchmark_version",
        "uq_replacement_validation_plan_task_draft",
        "uq_firewall_decision_single_successor",
        "uq_firewall_decision_policy_task_head",
        "uq_firewall_scope_policy_task",
        "uq_firewall_policy_benchmark_version",
    ):
        op.drop_index(index_name, table_name="audit_document")
    op.drop_constraint(op.f("ck_audit_document_p95_successors"), "audit_document", type_="check")
    op.drop_constraint(op.f("ck_audit_document_kind"), "audit_document", type_="check")
    op.create_check_constraint(
        op.f("ck_audit_document_kind"),
        "audit_document",
        "kind IN ('benchmark_snapshot','task_fingerprint','corpus_snapshot','audit_plan',"
        "'query_manifest','coverage_manifest','match_evidence','model_context','risk_policy',"
        "'risk_assessment','temporal_assessment','sealed_manifest','canary_policy',"
        "'seal_access_event','canary_observation','behavioral_audit_plan',"
        "'behavioral_method_registry','behavioral_task_validity','behavioral_observation',"
        "'behavioral_assessment','firewall_decision','replacement_plan','monitor_policy',"
        "'benchmark_health','audit_attestation')",
    )
