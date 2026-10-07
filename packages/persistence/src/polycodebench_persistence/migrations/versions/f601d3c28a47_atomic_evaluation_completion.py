"""Commit evaluator evidence and its gate with the leased stage result."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f601d3c28a47"
down_revision: str | None = "d4f082b91c33"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("GRANT UPDATE (gate, evidence_manifest_id) ON evaluation TO pcb_scheduler")


def downgrade() -> None:
    op.execute("REVOKE UPDATE (gate, evidence_manifest_id) ON evaluation FROM pcb_scheduler")
