"""Store preregistered behavioral diagnostic evidence and unique outcomes."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "a194d6c3e781"
down_revision: str | None = "93b11c2d7e4f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AUDIT_DOCUMENT_KINDS = (
    "'benchmark_snapshot','task_fingerprint','corpus_snapshot','audit_plan',"
    "'query_manifest','coverage_manifest','match_evidence','model_context','risk_policy',"
    "'risk_assessment','temporal_assessment','sealed_manifest','canary_policy',"
    "'seal_access_event','canary_observation','behavioral_audit_plan',"
    "'behavioral_method_registry','behavioral_task_validity','behavioral_observation',"
    "'behavioral_assessment','firewall_decision','replacement_plan','monitor_policy',"
    "'benchmark_health','audit_attestation'"
)


def upgrade() -> None:
    op.drop_constraint(op.f("ck_audit_document_kind"), "audit_document", type_="check")
    op.create_check_constraint(
        op.f("ck_audit_document_kind"),
        "audit_document",
        f"kind IN ({_AUDIT_DOCUMENT_KINDS})",
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_behavioral_observation_plan_unit
        ON audit_document (
            (payload->'plan_ref'->>'document_id'),
            (payload->>'pair_id'),
            (payload->'task_ref'->>'entity_id'),
            (payload->>'sample_role'),
            (payload->'model_context_ref'->>'document_id')
        )
        WHERE kind = 'behavioral_observation' AND supersedes_id IS NULL
        """
    )
    op.create_index(
        "uq_behavioral_observation_single_successor",
        "audit_document",
        ["supersedes_id"],
        unique=True,
        postgresql_where=text("kind = 'behavioral_observation' AND supersedes_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM audit_document
                WHERE kind IN (
                    'behavioral_method_registry','behavioral_task_validity',
                    'behavioral_observation','behavioral_assessment'
                ) OR (kind = 'behavioral_audit_plan' AND schema_version = 2)
            ) THEN
                RAISE EXCEPTION
                    'cannot remove behavioral diagnostic contracts while v2 evidence exists';
            END IF;
        END;
        $$
        """
    )
    op.drop_index("uq_behavioral_observation_single_successor", table_name="audit_document")
    op.drop_index("uq_behavioral_observation_plan_unit", table_name="audit_document")
    op.drop_constraint(op.f("ck_audit_document_kind"), "audit_document", type_="check")
    op.create_check_constraint(
        op.f("ck_audit_document_kind"),
        "audit_document",
        "kind IN ('benchmark_snapshot','task_fingerprint','corpus_snapshot','audit_plan',"
        "'query_manifest','coverage_manifest','match_evidence','model_context','risk_policy',"
        "'risk_assessment','temporal_assessment','sealed_manifest','canary_policy',"
        "'seal_access_event','canary_observation','behavioral_audit_plan','firewall_decision',"
        "'replacement_plan','monitor_policy','benchmark_health','audit_attestation')",
    )
