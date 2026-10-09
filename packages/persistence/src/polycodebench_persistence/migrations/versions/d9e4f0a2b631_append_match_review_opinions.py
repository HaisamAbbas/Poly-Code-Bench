"""Persist authenticated match-review opinions in the append-only review ledger."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d9e4f0a2b631"
down_revision = "c81a4d2e7f30"
branch_labels = None
depends_on = None


_OPINION_SHAPE = (
    "(opinion IS NULL AND idempotency_key IS NULL AND request_digest IS NULL) OR "
    "COALESCE((jsonb_typeof(opinion) = 'object' AND "
    "idempotency_key ~ '^[A-Za-z0-9._~-]{1,255}$' AND "
    "request_digest ~ '^sha256:[0-9a-f]{64}$' AND "
    "opinion->>'opinion_id' = id::text AND "
    "opinion->>'reviewer_subject' = reviewer_subject AND "
    "opinion->>'decision' = decision AND "
    "opinion->>'review_seq' = review_seq::text AND "
    "opinion->'candidate_ref'->>'document_id' = review_document_id::text), false)"
)


def upgrade() -> None:
    op.add_column("match_review", sa.Column("opinion", postgresql.JSONB(), nullable=True))
    op.add_column("match_review", sa.Column("idempotency_key", sa.String(255), nullable=True))
    op.add_column("match_review", sa.Column("request_digest", sa.String(71), nullable=True))
    op.create_check_constraint(
        op.f("ck_match_review_opinion_shape"), "match_review", _OPINION_SHAPE
    )
    op.create_index(
        "uq_match_review_candidate_reviewer",
        "match_review",
        ["candidate_id", "reviewer_subject"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_match_review_candidate_reviewer_key", table_name="match_review")
    op.drop_constraint(op.f("ck_match_review_opinion_shape"), "match_review", type_="check")
    op.drop_column("match_review", "request_digest")
    op.drop_column("match_review", "idempotency_key")
    op.drop_column("match_review", "opinion")
