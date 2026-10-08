"""Allow immutable model-context audit documents."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "92a10b7c6d5e"
down_revision: str | None = "1a2b3c4d5e6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AUDIT_DOCUMENT_KINDS = (
    "'benchmark_snapshot','task_fingerprint','corpus_snapshot','audit_plan',"
    "'query_manifest','coverage_manifest','match_evidence','model_context','risk_policy',"
    "'risk_assessment','temporal_assessment','sealed_manifest','canary_policy',"
    "'behavioral_audit_plan','firewall_decision','replacement_plan','monitor_policy',"
    "'benchmark_health','audit_attestation'"
)


def upgrade() -> None:
    op.drop_constraint(op.f("ck_audit_document_kind"), "audit_document", type_="check")
    op.create_check_constraint(
        op.f("ck_audit_document_kind"),
        "audit_document",
        f"kind IN ({_AUDIT_DOCUMENT_KINDS})",
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM audit_document WHERE kind = 'model_context'
            ) THEN
                RAISE EXCEPTION
                    'cannot remove model_context kind while model-context documents exist';
            END IF;
        END;
        $$
        """
    )
    op.drop_constraint(op.f("ck_audit_document_kind"), "audit_document", type_="check")
    op.create_check_constraint(
        op.f("ck_audit_document_kind"),
        "audit_document",
        "kind IN ('benchmark_snapshot','task_fingerprint','corpus_snapshot','audit_plan',"
        "'query_manifest','coverage_manifest','match_evidence','risk_policy','risk_assessment',"
        "'temporal_assessment','sealed_manifest','canary_policy','behavioral_audit_plan',"
        "'firewall_decision','replacement_plan','monitor_policy','benchmark_health',"
        "'audit_attestation')",
    )
