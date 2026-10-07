"""Expose only integrity-bound, approved public artifacts to the public reader role."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d4f082b91c33"
down_revision: str | None = "c02ea53a4d17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE VIEW public_artifact_catalog WITH (security_barrier=true) AS
        SELECT artifact.id, artifact.content_digest, artifact.size_bytes,
               artifact.media_type, artifact.storage_key
        FROM artifact
        WHERE artifact.visibility = 'public'
          AND artifact.status = 'verified'
          AND EXISTS (
              SELECT 1
              FROM artifact_declassification AS published
              JOIN artifact_projection_approval AS approval
                ON approval.source_artifact_id = published.source_artifact_id
              WHERE published.public_artifact_id = artifact.id
                AND approval.projection_digest = artifact.content_digest
                AND approval.projection_digest = published.review_digest
                AND approval.approved_by = published.approved_by
                AND approval.reason = published.reason
                AND approval.created_at <= published.created_at
          )
        """
    )
    op.execute("GRANT SELECT ON public_artifact_catalog TO pcb_public_reader")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON public_artifact_catalog FROM pcb_public_reader")
    op.execute("DROP VIEW public_artifact_catalog")
