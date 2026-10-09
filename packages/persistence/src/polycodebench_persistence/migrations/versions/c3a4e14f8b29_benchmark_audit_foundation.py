"""Add immutable audit documents, audit scope tables and capability-gated queue scope."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3a4e14f8b29"
down_revision: str | None = "2a62b6001aa1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id() -> sa.Column[UUID]:
    return sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False)


def _created_at() -> sa.Column[datetime]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def _create_audit_tables() -> None:
    op.create_table(
        "audit_document",
        _id(),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("semantic_digest", sa.String(71), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("document_created_at", sa.String(35), nullable=False),
        sa.Column("timestamp_precision", sa.String(16), nullable=False),
        sa.Column("trace_id", sa.Uuid(), nullable=True),
        sa.Column("document_row_version", sa.BigInteger(), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "kind IN ('benchmark_snapshot','task_fingerprint','corpus_snapshot','audit_plan',"
            "'query_manifest','coverage_manifest','match_evidence','risk_policy','risk_assessment',"
            "'temporal_assessment','sealed_manifest','canary_policy','behavioral_audit_plan',"
            "'firewall_decision','replacement_plan','monitor_policy','benchmark_health',"
            "'audit_attestation')",
            name="kind",
        ),
        sa.CheckConstraint("schema_version = 1", name="schema_version_v1"),
        sa.CheckConstraint("semantic_digest ~ '^sha256:[0-9a-f]{64}$'", name="digest_format"),
        sa.CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_object"),
        sa.CheckConstraint(
            "timestamp_precision IN ('second','millisecond','microsecond','nanosecond')",
            name="timestamp_precision",
        ),
        sa.CheckConstraint("document_row_version >= 0", name="document_row_version_nonnegative"),
        sa.CheckConstraint("supersedes_id IS NULL OR supersedes_id <> id", name="not_self_successor"),
        sa.ForeignKeyConstraint(
            ["supersedes_id", "kind"],
            ["audit_document.id", "audit_document.kind"],
            name="fk_audit_document_successor_same_kind",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_document"),
        sa.UniqueConstraint("kind", "semantic_digest", name="uq_audit_document_kind"),
        sa.UniqueConstraint("id", "kind", name="uq_audit_document_id"),
    )
    op.create_index("ix_audit_document_kind_created", "audit_document", ["kind", "created_at"])
    op.create_index("ix_audit_document_supersedes", "audit_document", ["supersedes_id"])

    op.create_table(
        "benchmark_registry",
        _id(),
        sa.Column("slug", sa.String(96), nullable=False),
        sa.Column("original_evaluation_owner", sa.String(255), nullable=False),
        sa.Column("metadata_document_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "status IN ('catalogued','metadata_only','importable','audit_conformant','blocked','retired')",
            name="status",
        ),
        sa.ForeignKeyConstraint(
            ["metadata_document_id"], ["audit_document.id"], name="fk_benchmark_registry_metadata"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_benchmark_registry"),
        sa.UniqueConstraint("slug", name="uq_benchmark_registry_slug"),
    )
    op.create_table(
        "benchmark_snapshot",
        _id(),
        sa.Column("registry_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.String(255), nullable=False),
        sa.Column("split", sa.String(128), nullable=False),
        sa.Column("membership_digest", sa.String(71), nullable=False),
        sa.Column("rights_state", sa.String(32), nullable=False),
        _created_at(),
        sa.CheckConstraint("membership_digest ~ '^sha256:[0-9a-f]{64}$'", name="membership_digest"),
        sa.CheckConstraint(
            "rights_state IN ('unreviewed','needs_item_review','approved','blocked','gated')",
            name="rights_state",
        ),
        sa.ForeignKeyConstraint(["registry_id"], ["benchmark_registry.id"], name="fk_snapshot_registry"),
        sa.ForeignKeyConstraint(["document_id"], ["audit_document.id"], name="fk_snapshot_document"),
        sa.PrimaryKeyConstraint("id", name="pk_benchmark_snapshot"),
        sa.UniqueConstraint("registry_id", "version", "split", name="uq_snapshot_registry_version"),
        sa.UniqueConstraint("id", "registry_id", name="uq_snapshot_id_registry"),
    )
    op.create_index("ix_benchmark_snapshot_version_split", "benchmark_snapshot", ["version", "split"])

    op.create_table(
        "benchmark_item",
        _id(),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("task_version_id", sa.Uuid(), nullable=False),
        sa.Column("item_key", sa.String(255), nullable=False),
        sa.Column("source_digest", sa.String(71), nullable=False),
        sa.Column("original_artifact_id", sa.Uuid(), nullable=True),
        sa.Column("membership_index", sa.Integer(), nullable=False),
        _created_at(),
        sa.CheckConstraint("source_digest ~ '^sha256:[0-9a-f]{64}$'", name="source_digest"),
        sa.CheckConstraint("membership_index >= 0", name="membership_index"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["benchmark_snapshot.id"], name="fk_item_snapshot"),
        sa.ForeignKeyConstraint(["task_version_id"], ["task_version.id"], name="fk_item_task_version"),
        sa.ForeignKeyConstraint(["original_artifact_id"], ["artifact.id"], name="fk_item_artifact"),
        sa.PrimaryKeyConstraint("id", name="pk_benchmark_item"),
        sa.UniqueConstraint("snapshot_id", "item_key", name="uq_item_snapshot_key"),
        sa.UniqueConstraint("snapshot_id", "task_version_id", name="uq_item_snapshot_task"),
    )
    op.create_index("ix_benchmark_item_task", "benchmark_item", ["task_version_id"])

    op.create_table(
        "audit_component",
        _id(),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("component_key", sa.String(128), nullable=False),
        sa.Column("component_digest", sa.String(71), nullable=False),
        sa.Column("content_artifact_id", sa.Uuid(), nullable=True),
        sa.Column("visibility", sa.String(16), nullable=False),
        _created_at(),
        sa.CheckConstraint("component_digest ~ '^sha256:[0-9a-f]{64}$'", name="digest_format"),
        sa.CheckConstraint("visibility IN ('private','restricted','public')", name="visibility"),
        sa.ForeignKeyConstraint(["item_id"], ["benchmark_item.id"], name="fk_component_item"),
        sa.ForeignKeyConstraint(["content_artifact_id"], ["artifact.id"], name="fk_component_artifact"),
        sa.PrimaryKeyConstraint("id", name="pk_audit_component"),
        sa.UniqueConstraint("item_id", "component_key", name="uq_component_item_key"),
    )
    op.create_index("ix_audit_component_digest", "audit_component", ["component_digest"])

    op.create_table(
        "fingerprint",
        _id(),
        sa.Column("component_id", sa.Uuid(), nullable=False),
        sa.Column("feature_kind", sa.String(48), nullable=False),
        sa.Column("method_version", sa.String(255), nullable=False),
        sa.Column("feature_digest", sa.String(71), nullable=False),
        sa.Column("private_artifact_id", sa.Uuid(), nullable=True),
        _created_at(),
        sa.CheckConstraint("feature_digest ~ '^sha256:[0-9a-f]{64}$'", name="digest_format"),
        sa.ForeignKeyConstraint(["component_id"], ["audit_component.id"], name="fk_fingerprint_component"),
        sa.ForeignKeyConstraint(["private_artifact_id"], ["artifact.id"], name="fk_fingerprint_artifact"),
        sa.PrimaryKeyConstraint("id", name="pk_fingerprint"),
        sa.UniqueConstraint("component_id", "feature_kind", "method_version", name="uq_fingerprint_method"),
    )
    op.create_index("ix_fingerprint_method_digest", "fingerprint", ["method_version", "feature_digest"])

    op.create_table(
        "corpus_source",
        _id(),
        sa.Column("slug", sa.String(96), nullable=False),
        sa.Column("official_url", sa.Text(), nullable=False),
        sa.Column("policy_document_id", sa.Uuid(), nullable=False),
        sa.Column("rights_state", sa.String(32), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "rights_state IN ('unreviewed','needs_item_review','approved','blocked','gated')",
            name="rights_state",
        ),
        sa.ForeignKeyConstraint(["policy_document_id"], ["audit_document.id"], name="fk_corpus_policy"),
        sa.PrimaryKeyConstraint("id", name="pk_corpus_source"),
        sa.UniqueConstraint("slug", name="uq_corpus_source_slug"),
    )
    op.create_index("ix_corpus_source_rights", "corpus_source", ["rights_state"])

    op.create_table(
        "corpus_snapshot",
        _id(),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.String(255), nullable=False),
        sa.Column("content_root", sa.String(71), nullable=False),
        sa.Column("index_digest", sa.String(71), nullable=False),
        sa.Column("coverage", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rights_document_id", sa.Uuid(), nullable=False),
        _created_at(),
        sa.CheckConstraint("content_root ~ '^sha256:[0-9a-f]{64}$'", name="content_root"),
        sa.CheckConstraint("index_digest ~ '^sha256:[0-9a-f]{64}$'", name="index_digest"),
        sa.CheckConstraint("jsonb_typeof(coverage) = 'object'", name="coverage_object"),
        sa.ForeignKeyConstraint(["source_id"], ["corpus_source.id"], name="fk_corpus_snapshot_source"),
        sa.ForeignKeyConstraint(["document_id"], ["audit_document.id"], name="fk_corpus_snapshot_document"),
        sa.ForeignKeyConstraint(["rights_document_id"], ["audit_document.id"], name="fk_corpus_snapshot_rights"),
        sa.PrimaryKeyConstraint("id", name="pk_corpus_snapshot"),
        sa.UniqueConstraint("source_id", "version", name="uq_corpus_snapshot_source_version"),
    )
    op.create_index("ix_corpus_snapshot_source_version", "corpus_snapshot", ["source_id", "version"])

    op.create_table(
        "corpus_document",
        _id(),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("source_identity_digest", sa.String(71), nullable=False),
        sa.Column("content_digest", sa.String(71), nullable=False),
        sa.Column("content_artifact_id", sa.Uuid(), nullable=True),
        sa.Column("source_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("date_precision", sa.String(16), nullable=False),
        sa.Column("lineage", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        _created_at(),
        sa.CheckConstraint("source_identity_digest ~ '^sha256:[0-9a-f]{64}$'", name="identity_digest"),
        sa.CheckConstraint("content_digest ~ '^sha256:[0-9a-f]{64}$'", name="content_digest"),
        sa.CheckConstraint(
            "date_precision IN ('unknown','day','second','millisecond','microsecond')",
            name="date_precision",
        ),
        sa.CheckConstraint("jsonb_typeof(lineage) = 'array'", name="lineage_array"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["corpus_snapshot.id"], name="fk_corpus_document_snapshot"),
        sa.ForeignKeyConstraint(["content_artifact_id"], ["artifact.id"], name="fk_corpus_document_artifact"),
        sa.PrimaryKeyConstraint("id", name="pk_corpus_document"),
        sa.UniqueConstraint("snapshot_id", "source_identity_digest", name="uq_corpus_document_identity"),
    )
    op.create_index("ix_corpus_document_content", "corpus_document", ["content_digest"])

    for table_name in (
        "audit_document",
        "benchmark_snapshot",
        "benchmark_item",
        "audit_component",
        "fingerprint",
        "corpus_snapshot",
        "corpus_document",
    ):
        op.execute(
            f"CREATE TRIGGER immutable_{table_name} BEFORE UPDATE OR DELETE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION pcb_reject_immutable_change()"
        )


def _create_scope_and_assessment_tables() -> None:
    # The checkout lacked the two prior curation scopes named by §8. These minimal parent rows
    # make the six-column FK invariant real; their workflows are owned by later prompts.
    for table_name in ("curation_round", "discovery_search"):
        op.create_table(
            table_name,
            _id(),
            sa.Column("state", sa.String(24), nullable=False),
            sa.Column("row_version", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
            _created_at(),
            sa.CheckConstraint(
                "state IN ('planned','queued','running','complete','blocked','cancelled')",
                name="state",
            ),
            sa.CheckConstraint("row_version >= 0", name="row_version_nonnegative"),
            sa.PrimaryKeyConstraint("id", name=f"pk_{table_name}"),
        )

    op.create_table(
        "audit_run",
        _id(),
        sa.Column("plan_document_id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("current_stage", sa.String(32), nullable=True),
        sa.Column("dispatch_authorized", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("reserved_query_units", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("reserved_storage_bytes", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("row_version", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "state IN ('draft','planned','queued','scanning','verifying','assessing',"
            "'review_required','complete','partial','blocked','cancelled')",
            name="state",
        ),
        sa.CheckConstraint("length(idempotency_key) BETWEEN 1 AND 255", name="idempotency_key"),
        sa.CheckConstraint(
            "reserved_query_units >= 0 AND reserved_storage_bytes >= 0", name="reservations"
        ),
        sa.CheckConstraint("row_version >= 0", name="row_version_nonnegative"),
        sa.ForeignKeyConstraint(["plan_document_id"], ["audit_document.id"], name="fk_audit_run_plan"),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaign.id"], name="fk_audit_run_campaign"),
        sa.PrimaryKeyConstraint("id", name="pk_audit_run"),
        sa.UniqueConstraint("plan_document_id", "idempotency_key", name="uq_audit_run_plan_idempotency"),
    )
    op.create_index("ix_audit_run_state_created", "audit_run", ["state", "created_at"])

    op.create_table(
        "audit_query",
        _id(),
        sa.Column("audit_run_id", sa.Uuid(), nullable=False),
        sa.Column("query_document_id", sa.Uuid(), nullable=False),
        sa.Column("query_index", sa.Integer(), nullable=False),
        sa.Column("logical_call_key", sa.String(255), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("reserved_units", sa.Integer(), nullable=False),
        sa.Column("row_version", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        _created_at(),
        sa.CheckConstraint("query_index >= 0", name="query_index_nonnegative"),
        sa.CheckConstraint("reserved_units >= 1", name="reserved_units_positive"),
        sa.CheckConstraint(
            "state IN ('planned','queued','running','complete','truncated','failed','blocked','cancelled')",
            name="state",
        ),
        sa.CheckConstraint("row_version >= 0", name="row_version_nonnegative"),
        sa.ForeignKeyConstraint(["audit_run_id"], ["audit_run.id"], name="fk_audit_query_run"),
        sa.ForeignKeyConstraint(["query_document_id"], ["audit_document.id"], name="fk_audit_query_document"),
        sa.PrimaryKeyConstraint("id", name="pk_audit_query"),
        sa.UniqueConstraint("audit_run_id", "query_index", name="uq_audit_query_run_index"),
        sa.UniqueConstraint("audit_run_id", "logical_call_key", name="uq_audit_query_logical_call"),
    )
    op.create_index("ix_audit_query_run_state", "audit_query", ["audit_run_id", "state"])

    op.create_table(
        "audit_checkpoint",
        _id(),
        sa.Column("audit_run_id", sa.Uuid(), nullable=False),
        sa.Column("checkpoint_seq", sa.BigInteger(), nullable=False),
        sa.Column("fence", sa.BigInteger(), nullable=False),
        sa.Column("scope_digest", sa.String(71), nullable=False),
        sa.Column("checkpoint", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        _created_at(),
        sa.CheckConstraint("checkpoint_seq >= 1 AND fence >= 1", name="sequence_fence_positive"),
        sa.CheckConstraint("scope_digest ~ '^sha256:[0-9a-f]{64}$'", name="scope_digest_format"),
        sa.CheckConstraint("jsonb_typeof(checkpoint) = 'object'", name="checkpoint_object"),
        sa.ForeignKeyConstraint(["audit_run_id"], ["audit_run.id"], name="fk_audit_checkpoint_run"),
        sa.PrimaryKeyConstraint("id", name="pk_audit_checkpoint"),
        sa.UniqueConstraint("audit_run_id", "checkpoint_seq", name="uq_audit_checkpoint_run_seq"),
    )
    op.create_index("ix_audit_checkpoint_run", "audit_checkpoint", ["audit_run_id", "checkpoint_seq"])

    op.create_table(
        "match_candidate",
        _id(),
        sa.Column("audit_run_id", sa.Uuid(), nullable=False),
        sa.Column("task_version_id", sa.Uuid(), nullable=False),
        sa.Column("source_document_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_document_id", sa.Uuid(), nullable=False),
        sa.Column("relation", sa.String(32), nullable=False),
        sa.Column("confidence", sa.String(16), nullable=True),
        sa.Column("confidence_null_reason", sa.String(32), nullable=True),
        sa.Column("state", sa.String(24), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "relation IN ('exact_component','near_exact_component','semantic_duplicate',"
            "'shared_family','shared_concept','no_substantive_match','unresolved')",
            name="relation",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR confidence ~ '^(0|[1-9][0-9]?|100)\\.[0-9]{6}$'",
            name="confidence_decimal",
        ),
        sa.CheckConstraint(
            "(confidence IS NULL AND confidence_null_reason IN "
            "('not_run','unavailable','not_applicable','insufficient_coverage','withheld')) OR "
            "(confidence IS NOT NULL AND confidence_null_reason IS NULL)",
            name="confidence_null_reason",
        ),
        sa.CheckConstraint(
            "state IN ('proposed','verified','review_required','accepted','rejected','disputed','superseded')",
            name="state",
        ),
        sa.ForeignKeyConstraint(["audit_run_id"], ["audit_run.id"], name="fk_match_candidate_run"),
        sa.ForeignKeyConstraint(["task_version_id"], ["task_version.id"], name="fk_match_candidate_task"),
        sa.ForeignKeyConstraint(["source_document_id"], ["audit_document.id"], name="fk_match_candidate_source"),
        sa.ForeignKeyConstraint(["evidence_document_id"], ["audit_document.id"], name="fk_match_candidate_evidence"),
        sa.PrimaryKeyConstraint("id", name="pk_match_candidate"),
        sa.UniqueConstraint(
            "audit_run_id", "task_version_id", "source_document_id", "evidence_document_id",
            name="uq_match_candidate_evidence",
        ),
    )
    op.create_index("ix_match_candidate_task_state", "match_candidate", ["task_version_id", "state"])

    op.create_table(
        "match_review",
        _id(),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("review_seq", sa.Integer(), nullable=False),
        sa.Column("reviewer_subject", sa.String(255), nullable=False),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("review_document_id", sa.Uuid(), nullable=False),
        _created_at(),
        sa.CheckConstraint("review_seq >= 1", name="review_seq_positive"),
        sa.CheckConstraint("decision IN ('accepted','rejected','disputed','superseded')", name="decision"),
        sa.ForeignKeyConstraint(["candidate_id"], ["match_candidate.id"], name="fk_match_review_candidate"),
        sa.ForeignKeyConstraint(["review_document_id"], ["audit_document.id"], name="fk_match_review_document"),
        sa.PrimaryKeyConstraint("id", name="pk_match_review"),
        sa.UniqueConstraint("candidate_id", "review_seq", name="uq_match_review_candidate_seq"),
    )
    op.create_index("ix_match_review_candidate", "match_review", ["candidate_id", "review_seq"])

    op.create_table(
        "risk_assessment",
        _id(),
        sa.Column("task_version_id", sa.Uuid(), nullable=False),
        sa.Column("plan_document_id", sa.Uuid(), nullable=False),
        sa.Column("policy_document_id", sa.Uuid(), nullable=False),
        sa.Column("context_document_id", sa.Uuid(), nullable=True),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("observed_index", sa.Numeric(9, 6), nullable=True),
        sa.Column("lower_bound", sa.Numeric(9, 6), nullable=False),
        sa.Column("upper_bound", sa.Numeric(9, 6), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        _created_at(),
        sa.CheckConstraint("observed_index IS NULL OR observed_index BETWEEN 0 AND 100", name="observed_range"),
        sa.CheckConstraint(
            "lower_bound >= 0 AND lower_bound <= upper_bound AND upper_bound <= 100", name="bounds"
        ),
        sa.CheckConstraint(
            "state IN ('low_observed','medium_observed','high_observed','insufficient_evidence','not_applicable')",
            name="state",
        ),
        sa.CheckConstraint("supersedes_id IS NULL OR supersedes_id <> id", name="not_self_successor"),
        sa.ForeignKeyConstraint(["task_version_id"], ["task_version.id"], name="fk_risk_assessment_task"),
        sa.ForeignKeyConstraint(["plan_document_id"], ["audit_document.id"], name="fk_risk_assessment_plan"),
        sa.ForeignKeyConstraint(["policy_document_id"], ["audit_document.id"], name="fk_risk_assessment_policy"),
        sa.ForeignKeyConstraint(["context_document_id"], ["audit_document.id"], name="fk_risk_assessment_context"),
        sa.ForeignKeyConstraint(["document_id"], ["audit_document.id"], name="fk_risk_assessment_document"),
        sa.ForeignKeyConstraint(["supersedes_id"], ["risk_assessment.id"], name="fk_risk_assessment_successor"),
        sa.PrimaryKeyConstraint("id", name="pk_risk_assessment"),
        sa.UniqueConstraint("task_version_id", "policy_document_id", "document_id", name="uq_risk_assessment_input"),
    )
    op.create_index("ix_risk_assessment_task_policy", "risk_assessment", ["task_version_id", "policy_document_id"])

    op.create_table(
        "temporal_assessment",
        _id(),
        sa.Column("task_version_id", sa.Uuid(), nullable=False),
        sa.Column("context_document_id", sa.Uuid(), nullable=True),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("state", sa.String(40), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "state IN ('post_declared_cutoff','pre_cutoff_exposure_detected','interval_overlap',"
            "'unknown_cutoff','unknown_source_time','mutable_model_context')",
            name="state",
        ),
        sa.CheckConstraint("supersedes_id IS NULL OR supersedes_id <> id", name="not_self_successor"),
        sa.ForeignKeyConstraint(["task_version_id"], ["task_version.id"], name="fk_temporal_assessment_task"),
        sa.ForeignKeyConstraint(["context_document_id"], ["audit_document.id"], name="fk_temporal_assessment_context"),
        sa.ForeignKeyConstraint(["document_id"], ["audit_document.id"], name="fk_temporal_assessment_document"),
        sa.ForeignKeyConstraint(["supersedes_id"], ["temporal_assessment.id"], name="fk_temporal_assessment_successor"),
        sa.PrimaryKeyConstraint("id", name="pk_temporal_assessment"),
        sa.UniqueConstraint("task_version_id", "document_id", name="uq_temporal_assessment_document"),
    )
    op.create_index("ix_temporal_assessment_task", "temporal_assessment", ["task_version_id"])

    for table_name in ("audit_checkpoint", "match_review", "risk_assessment", "temporal_assessment"):
        op.execute(
            f"CREATE TRIGGER immutable_{table_name} BEFORE UPDATE OR DELETE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION pcb_reject_immutable_change()"
        )


def upgrade() -> None:
    _create_audit_tables()
    _create_scope_and_assessment_tables()

    # Historical run purpose is unknown and remains NULL. New run creation writes a purpose.
    op.add_column("run", sa.Column("audit_run_id", sa.Uuid(), nullable=True))
    op.add_column("run", sa.Column("purpose", sa.String(32), nullable=True))
    op.create_foreign_key("fk_run_audit_run_id_audit_run", "run", "audit_run", ["audit_run_id"], ["id"])
    op.create_check_constraint(
        "purpose_audit_scope",
        "run",
        "(purpose IS NULL AND audit_run_id IS NULL) OR "
        "(purpose = 'audit_diagnostic' AND audit_run_id IS NOT NULL) OR "
        "(purpose IN ('representative','challenge') AND audit_run_id IS NULL)",
    )

    op.add_column("stage_job", sa.Column("curation_round_id", sa.Uuid(), nullable=True))
    op.add_column("stage_job", sa.Column("discovery_search_id", sa.Uuid(), nullable=True))
    op.add_column("stage_job", sa.Column("audit_run_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_stage_job_curation_round_id_curation_round", "stage_job", "curation_round", ["curation_round_id"], ["id"])
    op.create_foreign_key("fk_stage_job_discovery_search_id_discovery_search", "stage_job", "discovery_search", ["discovery_search_id"], ["id"])
    op.create_foreign_key("fk_stage_job_audit_run_id_audit_run", "stage_job", "audit_run", ["audit_run_id"], ["id"])
    op.drop_constraint(op.f("ck_stage_job_one_scope"), "stage_job", type_="check")
    op.create_check_constraint(
        "one_scope",
        "stage_job",
        "num_nonnulls(attempt_id,evaluation_id,release_id,curation_round_id,"
        "discovery_search_id,audit_run_id) = 1",
    )
    op.create_index("ix_stage_job_audit_run", "stage_job", ["audit_run_id"])

    op.add_column(
        "worker_registration",
        sa.Column("audit_capable", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )

    op.drop_constraint(op.f("ck_budget_account_scope_kind"), "budget_account", type_="check")
    op.create_check_constraint(
        "scope_kind",
        "budget_account",
        "scope_kind IN ('campaign','run','attempt','evaluation','audit_run')",
    )
    op.add_column("call_intent", sa.Column("audit_run_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_call_intent_audit_run_id_audit_run", "call_intent", "audit_run", ["audit_run_id"], ["id"])
    op.add_column("call_intent", sa.Column("diagnostic_audit_run_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_call_intent_diagnostic_audit_run_id_audit_run",
        "call_intent",
        "audit_run",
        ["diagnostic_audit_run_id"],
        ["id"],
    )
    op.drop_constraint(op.f("ck_call_intent_one_scope"), "call_intent", type_="check")
    op.create_check_constraint(
        "one_scope", "call_intent", "num_nonnulls(attempt_id,evaluation_id,audit_run_id) = 1"
    )
    op.create_check_constraint(
        "diagnostic_audit_context_scope",
        "call_intent",
        "diagnostic_audit_run_id IS NULL OR "
        "(attempt_id IS NOT NULL AND audit_run_id IS NULL)",
    )
    op.create_index(
        "uq_call_intent_audit_run_key",
        "call_intent",
        ["audit_run_id", "logical_call_key"],
        unique=True,
        postgresql_where=sa.text("audit_run_id IS NOT NULL"),
    )
    op.create_index(
        "ix_call_intent_diagnostic_audit_run",
        "call_intent",
        ["diagnostic_audit_run_id"],
        postgresql_where=sa.text("diagnostic_audit_run_id IS NOT NULL"),
    )

    op.execute(
        "GRANT SELECT, INSERT ON audit_document, benchmark_registry, benchmark_snapshot, "
        "benchmark_item, audit_component, fingerprint, corpus_source, corpus_snapshot, "
        "corpus_document, audit_run, audit_query, audit_checkpoint, match_candidate, "
        "match_review, risk_assessment, temporal_assessment TO pcb_operator, pcb_administrator"
    )
    op.execute("GRANT UPDATE (state, row_version) ON audit_run TO pcb_operator")
    op.execute(
        "GRANT SELECT ON audit_run, curation_round, discovery_search TO pcb_scheduler"
    )
    op.execute(
        "GRANT UPDATE (state, current_stage, row_version) ON audit_run TO pcb_scheduler"
    )
    op.execute(
        "GRANT UPDATE (state, row_version) ON curation_round, discovery_search TO pcb_scheduler"
    )
    op.execute(
        "GRANT SELECT ON audit_run TO pcb_model_gateway"
    )
    op.execute(
        "GRANT SELECT ON audit_run, budget_account TO pcb_operator"
    )
    op.execute("GRANT SELECT ON artifact TO pcb_operator")


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        raise RuntimeError("audit downgrade requires an online PostgreSQL safety check")
    has_rows = connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM audit_document) OR "
            "EXISTS (SELECT 1 FROM curation_round) OR "
            "EXISTS (SELECT 1 FROM discovery_search) OR "
            "EXISTS (SELECT 1 FROM stage_job WHERE curation_round_id IS NOT NULL "
            "OR discovery_search_id IS NOT NULL OR audit_run_id IS NOT NULL) OR "
            "EXISTS (SELECT 1 FROM run WHERE purpose IS NOT NULL) OR "
            "EXISTS (SELECT 1 FROM worker_registration WHERE audit_capable)"
        )
    ).scalar_one()
    if has_rows:
        raise RuntimeError(
            "audit records or new queue scopes exist; export and quiesce them before downgrade"
        )

    op.drop_index("ix_call_intent_diagnostic_audit_run", table_name="call_intent")
    op.drop_constraint(
        op.f("ck_call_intent_diagnostic_audit_context_scope"), "call_intent", type_="check"
    )
    op.drop_constraint(
        "fk_call_intent_diagnostic_audit_run_id_audit_run", "call_intent", type_="foreignkey"
    )
    op.drop_column("call_intent", "diagnostic_audit_run_id")
    op.drop_index("uq_call_intent_audit_run_key", table_name="call_intent")
    op.drop_constraint(op.f("ck_call_intent_one_scope"), "call_intent", type_="check")
    op.create_check_constraint(
        "one_scope", "call_intent", "num_nonnulls(attempt_id,evaluation_id) = 1"
    )
    op.drop_constraint("fk_call_intent_audit_run_id_audit_run", "call_intent", type_="foreignkey")
    op.drop_column("call_intent", "audit_run_id")
    op.drop_constraint(op.f("ck_budget_account_scope_kind"), "budget_account", type_="check")
    op.create_check_constraint(
        "scope_kind", "budget_account", "scope_kind IN ('campaign','run','attempt','evaluation')"
    )
    op.drop_column("worker_registration", "audit_capable")

    op.drop_index("ix_stage_job_audit_run", table_name="stage_job")
    op.drop_constraint(op.f("ck_stage_job_one_scope"), "stage_job", type_="check")
    op.create_check_constraint(
        "one_scope", "stage_job", "num_nonnulls(attempt_id,evaluation_id,release_id) = 1"
    )
    for column, constraint in (
        ("audit_run_id", "fk_stage_job_audit_run_id_audit_run"),
        ("discovery_search_id", "fk_stage_job_discovery_search_id_discovery_search"),
        ("curation_round_id", "fk_stage_job_curation_round_id_curation_round"),
    ):
        op.drop_constraint(constraint, "stage_job", type_="foreignkey")
        op.drop_column("stage_job", column)

    op.drop_constraint(op.f("ck_run_purpose_audit_scope"), "run", type_="check")
    op.drop_constraint("fk_run_audit_run_id_audit_run", "run", type_="foreignkey")
    op.drop_column("run", "purpose")
    op.drop_column("run", "audit_run_id")

    for table_name in (
        "temporal_assessment",
        "risk_assessment",
        "match_review",
        "match_candidate",
        "audit_checkpoint",
        "audit_query",
        "audit_run",
        "discovery_search",
        "curation_round",
        "corpus_document",
        "corpus_snapshot",
        "corpus_source",
        "fingerprint",
        "audit_component",
        "benchmark_item",
        "benchmark_snapshot",
        "benchmark_registry",
        "audit_document",
    ):
        op.drop_table(table_name)
