"""Persist independent match-review adjudications as immutable ledger events."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e1f2a3b4c5d6"
down_revision = "d9e4f0a2b631"
branch_labels = None
depends_on = None


_ADJUDICATION_SHAPE = (
    "COALESCE((jsonb_typeof(adjudication) = 'object' AND "
    "idempotency_key ~ '^[A-Za-z0-9._~-]{1,255}$' AND "
    "request_digest ~ '^sha256:[0-9a-f]{64}$' AND "
    "adjudication->>'adjudication_id' = id::text AND "
    "adjudication->>'adjudicator_subject' = adjudicator_subject AND "
    "adjudication->>'decision' = decision AND "
    "adjudication->>'relation' = relation), false)"
)


def upgrade() -> None:
    op.create_table(
        "match_adjudication",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("adjudicator_subject", sa.String(255), nullable=False),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("relation", sa.String(32), nullable=False),
        sa.Column("adjudication", postgresql.JSONB(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_digest", sa.String(71), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("decision IN ('accepted','rejected')", name="decision"),
        sa.CheckConstraint(
            "relation IN ('exact_component','near_exact_component','semantic_duplicate',"
            "'shared_family','shared_concept','no_substantive_match','unresolved')",
            name="relation",
        ),
        sa.CheckConstraint(_ADJUDICATION_SHAPE, name="adjudication_shape"),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["match_candidate.id"],
            name="fk_match_adjudication_candidate_id_match_candidate",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_match_adjudication"),
        sa.UniqueConstraint("candidate_id", name="uq_match_adjudication_candidate"),
    )
    op.execute(
        "CREATE TRIGGER immutable_match_adjudication BEFORE UPDATE OR DELETE "
        "ON match_adjudication FOR EACH ROW EXECUTE FUNCTION pcb_reject_immutable_change()"
    )
    op.execute("GRANT SELECT, INSERT ON match_adjudication TO pcb_operator, pcb_administrator")


def downgrade() -> None:
    connection = op.get_bind()
    has_rows = connection.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM match_adjudication)")
    ).scalar_one()
    if has_rows:
        raise RuntimeError("match adjudications are immutable evidence; export before downgrade")
    op.execute("DROP TRIGGER IF EXISTS immutable_match_adjudication ON match_adjudication")
    op.drop_table("match_adjudication")
