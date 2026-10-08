"""Persist immutable sealed-access and canary-observation evidence."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "93b11c2d7e4f"
down_revision: str | None = "92a10b7c6d5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AUDIT_DOCUMENT_KINDS = (
    "'benchmark_snapshot','task_fingerprint','corpus_snapshot','audit_plan',"
    "'query_manifest','coverage_manifest','match_evidence','model_context','risk_policy',"
    "'risk_assessment','temporal_assessment','sealed_manifest','canary_policy',"
    "'seal_access_event','canary_observation','behavioral_audit_plan','firewall_decision',"
    "'replacement_plan','monitor_policy','benchmark_health','audit_attestation'"
)


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT supersedes_id FROM audit_document
                WHERE kind = 'sealed_manifest' AND supersedes_id IS NOT NULL
                GROUP BY supersedes_id HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'cannot enforce linear sealed-manifest history while successor branches exist';
            END IF;
        END;
        $$
        """
    )
    op.drop_constraint(op.f("ck_audit_document_kind"), "audit_document", type_="check")
    op.create_check_constraint(
        op.f("ck_audit_document_kind"),
        "audit_document",
        f"kind IN ({_AUDIT_DOCUMENT_KINDS})",
    )
    op.create_index(
        "uq_sealed_manifest_single_successor",
        "audit_document",
        ["supersedes_id"],
        unique=True,
        postgresql_where=text("kind = 'sealed_manifest' AND supersedes_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM audit_document
                WHERE kind IN ('seal_access_event','canary_observation')
            ) THEN
                RAISE EXCEPTION
                    'cannot remove sealed-access or canary-observation kinds while documents exist';
            END IF;
        END;
        $$
        """
    )
    op.drop_index("uq_sealed_manifest_single_successor", table_name="audit_document")
    op.drop_constraint(op.f("ck_audit_document_kind"), "audit_document", type_="check")
    op.create_check_constraint(
        op.f("ck_audit_document_kind"),
        "audit_document",
        "kind IN ('benchmark_snapshot','task_fingerprint','corpus_snapshot','audit_plan',"
        "'query_manifest','coverage_manifest','match_evidence','model_context','risk_policy',"
        "'risk_assessment','temporal_assessment','sealed_manifest','canary_policy',"
        "'behavioral_audit_plan','firewall_decision','replacement_plan','monitor_policy',"
        "'benchmark_health','audit_attestation')",
    )
