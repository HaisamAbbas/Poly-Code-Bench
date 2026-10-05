"""Constrain the public contents of withdrawal notices."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b390a26f17cd"
down_revision: str | None = "a971d65c204e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        op.f("ck_public_release_document_document_withdrawal_allowlist"),
        "public_release_document",
        "(state = 'published' AND NOT (document ? 'withdrawal')) OR "
        "(state = 'withdrawn' AND jsonb_typeof(document->'withdrawal') = 'object' AND "
        "(document->'withdrawal') ? 'reason' AND "
        "((document->'withdrawal') - ARRAY['reason','replacement_id']) = '{}'::jsonb AND "
        "COALESCE(NULLIF(btrim((document->'withdrawal')->>'reason'), ''), '') <> '')",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_public_release_document_document_withdrawal_allowlist"),
        "public_release_document",
        type_="check",
    )
