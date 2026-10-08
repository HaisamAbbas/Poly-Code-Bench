"""PostgreSQL relational schema for durable benchmark records.

The metadata is the authoritative source for migrations and repository SQL.
JSONB fields hold already validated extension documents; identities and
relationships remain relational constraints.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
metadata = MetaData(naming_convention=NAMING_CONVENTION)


def pk() -> Column[UUID]:
    return Column(
        "id",
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )


def created_at(name: str = "created_at") -> Column[datetime]:
    return Column(name, DateTime(timezone=True), nullable=False, server_default=func.now())


def fk(
    name: str,
    target: str,
    *,
    nullable: bool = False,
    use_alter: bool = False,
) -> Column[UUID]:
    return Column(
        name,
        Uuid(as_uuid=True),
        ForeignKey(target, ondelete="RESTRICT", use_alter=use_alter),
        nullable=nullable,
    )


audit_document = Table(
    "audit_document",
    metadata,
    pk(),
    Column("kind", String(48), nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("semantic_digest", String(71), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("supersedes_id", Uuid(as_uuid=True), nullable=True),
    Column("created_by", String(255), nullable=False),
    Column("document_created_at", String(35), nullable=False),
    Column("timestamp_precision", String(16), nullable=False),
    Column("trace_id", Uuid(as_uuid=True), nullable=True),
    Column("document_row_version", BigInteger, nullable=False),
    created_at(),
    UniqueConstraint("kind", "semantic_digest"),
    UniqueConstraint("id", "kind"),
    ForeignKeyConstraint(
        ["supersedes_id", "kind"],
        ["audit_document.id", "audit_document.kind"],
        name="fk_audit_document_successor_same_kind",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "kind IN ('benchmark_snapshot','task_fingerprint','corpus_snapshot','audit_plan',"
        "'query_manifest','coverage_manifest','match_evidence','model_context','risk_policy','risk_assessment',"
        "'temporal_assessment','sealed_manifest','canary_policy','seal_access_event',"
        "'canary_observation','behavioral_audit_plan','behavioral_method_registry',"
        "'behavioral_task_validity','behavioral_observation','behavioral_assessment',"
        "'firewall_policy','firewall_scope','firewall_decision','replacement_source_metadata',"
        "'replacement_plan','replacement_validation','derived_benchmark_manifest',"
        "'monitor_policy','monitor_alert','benchmark_health',"
        "'audit_attestation')",
        name="kind",
    ),
    CheckConstraint("schema_version IN (1,2)", name="schema_version_supported"),
    CheckConstraint("semantic_digest ~ '^sha256:[0-9a-f]{64}$'", name="digest_format"),
    CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_object"),
    CheckConstraint(
        "timestamp_precision IN ('second','millisecond','microsecond','nanosecond')",
        name="timestamp_precision",
    ),
    CheckConstraint("document_row_version >= 0", name="document_row_version_nonnegative"),
    CheckConstraint("supersedes_id IS NULL OR supersedes_id <> id", name="not_self_successor"),
    CheckConstraint(
        "kind NOT IN ('firewall_policy','firewall_scope','replacement_source_metadata',"
        "'replacement_plan','replacement_validation','derived_benchmark_manifest',"
        "'firewall_decision') "
        "OR (kind IN ('firewall_decision','replacement_plan') AND schema_version = 1) "
        "OR (kind = 'firewall_decision' AND schema_version = 2) OR supersedes_id IS NULL",
        name="p95_successors",
    ),
    CheckConstraint(
        "kind <> 'monitor_policy' OR schema_version = 2 OR supersedes_id IS NULL",
        name="monitor_policy_successors",
    ),
    Index("ix_audit_document_kind_created", "kind", "created_at"),
    Index("ix_audit_document_supersedes", "supersedes_id"),
    Index(
        "uq_behavioral_observation_plan_unit",
        text("(payload->'plan_ref'->>'document_id')"),
        text("(payload->>'pair_id')"),
        text("(payload->'task_ref'->>'entity_id')"),
        text("(payload->>'sample_role')"),
        text("(payload->'model_context_ref'->>'document_id')"),
        unique=True,
        postgresql_where=text("kind = 'behavioral_observation' AND supersedes_id IS NULL"),
    ),
    Index(
        "uq_behavioral_observation_single_successor",
        "supersedes_id",
        unique=True,
        postgresql_where=text("kind = 'behavioral_observation' AND supersedes_id IS NOT NULL"),
    ),
    Index(
        "uq_sealed_manifest_single_successor",
        "supersedes_id",
        unique=True,
        postgresql_where=text("kind = 'sealed_manifest' AND supersedes_id IS NOT NULL"),
    ),
    Index(
        "uq_firewall_policy_benchmark_version",
        text("(payload->'benchmark_ref'->>'document_id')"),
        text("(payload->>'policy_version')"),
        unique=True,
        postgresql_where=text("kind = 'firewall_policy' AND schema_version = 2"),
    ),
    Index(
        "uq_firewall_scope_policy_task",
        text("(payload->'policy_ref'->>'document_id')"),
        text("(payload->'task_ref'->>'entity_id')"),
        unique=True,
        postgresql_where=text("kind = 'firewall_scope' AND supersedes_id IS NULL"),
    ),
    Index(
        "uq_firewall_decision_policy_task_head",
        text("(payload->'policy_ref'->>'document_id')"),
        text("(payload->'task_ref'->>'entity_id')"),
        unique=True,
        postgresql_where=text(
            "kind = 'firewall_decision' AND schema_version = 2 AND supersedes_id IS NULL"
        ),
    ),
    Index(
        "uq_firewall_decision_single_successor",
        "supersedes_id",
        unique=True,
        postgresql_where=text(
            "kind = 'firewall_decision' AND schema_version = 2 AND supersedes_id IS NOT NULL"
        ),
    ),
    Index(
        "uq_replacement_validation_plan_task_draft",
        text("(payload->'plan_ref'->>'document_id')"),
        text("(payload->'task_ref'->>'entity_id')"),
        text("(payload->>'draft_index')"),
        unique=True,
        postgresql_where=text("kind = 'replacement_validation' AND supersedes_id IS NULL"),
    ),
    Index(
        "uq_derived_benchmark_version",
        text("(payload->'official_snapshot_ref'->>'document_id')"),
        text("(payload->>'derived_version')"),
        unique=True,
        postgresql_where=text("kind = 'derived_benchmark_manifest' AND supersedes_id IS NULL"),
    ),
    Index(
        "uq_monitor_policy_benchmark_version",
        text("(payload->'benchmark_ref'->>'document_id')"),
        text("(payload->>'policy_version')"),
        unique=True,
        postgresql_where=text("kind = 'monitor_policy' AND schema_version = 2"),
    ),
    Index(
        "uq_monitor_policy_single_successor",
        "supersedes_id",
        unique=True,
        postgresql_where=text("kind = 'monitor_policy' AND supersedes_id IS NOT NULL"),
    ),
    Index(
        "uq_monitor_alert_policy_dedupe",
        text("(payload->'policy_ref'->>'document_id')"),
        text("(payload->>'dedupe_key')"),
        unique=True,
        postgresql_where=text("kind = 'monitor_alert'"),
    ),
)


artifact = Table(
    "artifact",
    metadata,
    pk(),
    Column("visibility", String(16), nullable=False),
    Column("content_digest", String(71), nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    Column("media_type", String(255), nullable=False),
    Column("storage_key", Text, nullable=False),
    Column("encryption_domain", String(128), nullable=False),
    Column("status", String(24), nullable=False),
    Column("producer_execution_id", Uuid(as_uuid=True), nullable=True),
    created_at(),
    UniqueConstraint("visibility", "encryption_domain", "content_digest"),
    CheckConstraint("visibility IN ('public','internal','hidden')", name="visibility"),
    CheckConstraint("status IN ('provisional','verified','quarantined','deleted')", name="status"),
    CheckConstraint("size_bytes >= 0", name="size_bytes_nonnegative"),
    CheckConstraint("content_digest ~ '^sha256:[0-9a-f]{64}$'", name="digest_format"),
)

artifact_quota = Table(
    "artifact_quota",
    metadata,
    Column("visibility", String(16), primary_key=True),
    Column("encryption_domain", String(128), primary_key=True),
    Column("max_bytes", BigInteger, nullable=False),
    Column("used_bytes", BigInteger, nullable=False, server_default=text("0")),
    Column("reserved_bytes", BigInteger, nullable=False, server_default=text("0")),
    Column("row_version", BigInteger, nullable=False, server_default=text("0")),
    created_at(),
    CheckConstraint("visibility IN ('public','internal','hidden')", name="visibility"),
    CheckConstraint("max_bytes >= 0", name="max_bytes_nonnegative"),
    CheckConstraint("used_bytes >= 0 AND reserved_bytes >= 0", name="usage_nonnegative"),
    CheckConstraint("used_bytes + reserved_bytes <= max_bytes", name="within_quota"),
    CheckConstraint("row_version >= 0", name="row_version_nonnegative"),
)

artifact_upload = Table(
    "artifact_upload",
    metadata,
    pk(),
    Column("owner_subject", String(255), nullable=False),
    Column("visibility", String(16), nullable=False),
    Column("encryption_domain", String(128), nullable=False),
    Column("expected_digest", String(71), nullable=False),
    Column("expected_size_bytes", BigInteger, nullable=False),
    Column("media_type", String(255), nullable=False),
    Column("provisional_key", Text, nullable=False),
    Column("state", String(24), nullable=False),
    Column("failure_code", String(64), nullable=True),
    fk("artifact_id", "artifact.id", nullable=True),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("garbage_collect_after", DateTime(timezone=True), nullable=False),
    created_at(),
    UniqueConstraint("provisional_key"),
    CheckConstraint("visibility IN ('public','internal','hidden')", name="visibility"),
    CheckConstraint("expected_digest ~ '^sha256:[0-9a-f]{64}$'", name="digest_format"),
    CheckConstraint("expected_size_bytes >= 0", name="size_nonnegative"),
    CheckConstraint(
        "state IN ('reserved','uploaded','finalizing','verified','rejected','expired')",
        name="state",
    ),
    CheckConstraint(
        "(state = 'verified' AND artifact_id IS NOT NULL) OR "
        "(state <> 'verified' AND artifact_id IS NULL)",
        name="artifact_link_state",
    ),
    CheckConstraint("garbage_collect_after >= expires_at", name="gc_after_expiry"),
    Index("ix_artifact_upload_expiry", "state", "expires_at"),
    Index("ix_artifact_upload_gc", "state", "garbage_collect_after"),
)

artifact_retention_hold = Table(
    "artifact_retention_hold",
    metadata,
    pk(),
    fk("artifact_id", "artifact.id"),
    Column("reason", Text, nullable=False),
    Column("held_by", String(255), nullable=False),
    created_at("held_at"),
    Column("released_by", String(255), nullable=True),
    Column("released_at", DateTime(timezone=True), nullable=True),
    CheckConstraint("length(trim(reason)) > 0", name="reason_nonempty"),
    CheckConstraint(
        "(released_by IS NULL AND released_at IS NULL) OR "
        "(released_by IS NOT NULL AND released_at IS NOT NULL)",
        name="release_shape",
    ),
    Index("ix_artifact_hold_active", "artifact_id", postgresql_where=text("released_at IS NULL")),
)

artifact_declassification = Table(
    "artifact_declassification",
    metadata,
    Column(
        "source_artifact_id",
        Uuid(as_uuid=True),
        ForeignKey("artifact.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column(
        "public_artifact_id",
        Uuid(as_uuid=True),
        ForeignKey("artifact.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("review_digest", String(71), nullable=False),
    Column("approved_by", String(255), nullable=False),
    Column("reason", Text, nullable=False),
    created_at(),
    CheckConstraint("source_artifact_id <> public_artifact_id", name="distinct_artifacts"),
    CheckConstraint("review_digest ~ '^sha256:[0-9a-f]{64}$'", name="review_digest_format"),
    CheckConstraint("length(trim(reason)) > 0", name="reason_nonempty"),
)

artifact_projection_approval = Table(
    "artifact_projection_approval",
    metadata,
    pk(),
    fk("source_artifact_id", "artifact.id"),
    Column("projection_digest", String(71), nullable=False),
    Column("approved_by", String(255), nullable=False),
    Column("reason", Text, nullable=False),
    created_at(),
    CheckConstraint("projection_digest ~ '^sha256:[0-9a-f]{64}$'", name="digest_format"),
    CheckConstraint("length(trim(reason)) > 0", name="reason_nonempty"),
)

task = Table(
    "task",
    metadata,
    pk(),
    Column("slug", String(160), nullable=False),
    Column("family", String(64), nullable=False),
    Column("source_identity", String(512), nullable=False),
    Column("primary_language", String(32), nullable=False),
    Column("row_version", BigInteger, nullable=False, server_default=text("0")),
    created_at(),
    UniqueConstraint("slug"),
    CheckConstraint("row_version >= 0", name="row_version_nonnegative"),
)

task_version = Table(
    "task_version",
    metadata,
    pk(),
    fk("task_id", "task.id"),
    Column("version", Integer, nullable=False),
    Column("digest", String(71), nullable=False),
    fk("manifest_artifact_id", "artifact.id"),
    fk("visible_artifact_id", "artifact.id"),
    fk("hidden_artifact_id", "artifact.id"),
    Column("language", String(32), nullable=False),
    Column("family", String(64), nullable=False),
    Column("cluster_id", String(160), nullable=False),
    Column("stratum_id", String(160), nullable=False),
    Column("first_public_at", DateTime(timezone=True), nullable=True),
    Column("frozen_at", DateTime(timezone=True), nullable=True),
    Column("schema_version", Integer, nullable=False),
    Column("document", JSONB, nullable=False),
    Column("admission_evidence", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    created_at(),
    UniqueConstraint("task_id", "version"),
    UniqueConstraint("digest"),
    CheckConstraint("version > 0", name="version_positive"),
    CheckConstraint("schema_version > 0", name="schema_version_positive"),
    CheckConstraint("digest ~ '^sha256:[0-9a-f]{64}$'", name="digest_format"),
    Index("ix_task_version_language", "language"),
    Index("ix_task_version_family", "family"),
)

benchmark_registry = Table(
    "benchmark_registry",
    metadata,
    pk(),
    Column("slug", String(96), nullable=False),
    Column("original_evaluation_owner", String(255), nullable=False),
    fk("metadata_document_id", "audit_document.id", nullable=True),
    Column("status", String(24), nullable=False),
    created_at(),
    UniqueConstraint("slug"),
    CheckConstraint(
        "status IN ('catalogued','metadata_only','importable','audit_conformant',"
        "'blocked','retired')",
        name="status",
    ),
)

benchmark_snapshot = Table(
    "benchmark_snapshot",
    metadata,
    pk(),
    fk("registry_id", "benchmark_registry.id"),
    fk("document_id", "audit_document.id"),
    Column("version", String(255), nullable=False),
    Column("split", String(128), nullable=False),
    Column("membership_digest", String(71), nullable=False),
    Column("rights_state", String(32), nullable=False),
    created_at(),
    UniqueConstraint("registry_id", "version", "split"),
    UniqueConstraint("id", "registry_id"),
    UniqueConstraint("id", "membership_digest", name="id_membership_digest"),
    CheckConstraint("membership_digest ~ '^sha256:[0-9a-f]{64}$'", name="membership_digest"),
    CheckConstraint(
        "rights_state IN ('unreviewed','needs_item_review','approved','blocked','gated')",
        name="rights_state",
    ),
    Index("ix_benchmark_snapshot_version_split", "version", "split"),
)

benchmark_item = Table(
    "benchmark_item",
    metadata,
    pk(),
    fk("snapshot_id", "benchmark_snapshot.id"),
    fk("task_version_id", "task_version.id", nullable=True),
    Column("item_key", String(255), nullable=False),
    Column("source_digest", String(71), nullable=True),
    fk("original_artifact_id", "artifact.id", nullable=True),
    Column("membership_index", Integer, nullable=False),
    Column("import_state", String(24), nullable=False, server_default=text("'legacy_unverified'")),
    Column("error_codes", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("source_date_evidence", JSONB, nullable=True),
    Column("source_urls", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("self_source_exposure", Boolean, nullable=True),
    Column("source_public_exposure", Boolean, nullable=True),
    Column("independent_duplicate_eligible", Boolean, nullable=True),
    created_at(),
    UniqueConstraint("snapshot_id", "item_key"),
    UniqueConstraint("snapshot_id", "task_version_id"),
    CheckConstraint(
        "source_digest IS NULL OR source_digest ~ '^sha256:[0-9a-f]{64}$'", name="source_digest"
    ),
    CheckConstraint("membership_index >= 0", name="membership_index"),
    CheckConstraint(
        "import_state IN ('legacy_unverified','imported','incomplete','missing','blocked')",
        name="import_state",
    ),
    CheckConstraint("jsonb_typeof(error_codes) = 'array'", name="error_codes_array"),
    CheckConstraint("jsonb_typeof(source_urls) = 'array'", name="source_urls_array"),
    CheckConstraint(
        "import_state = 'legacy_unverified' OR "
        "(import_state = 'imported' AND source_digest IS NOT NULL "
        "AND task_version_id IS NULL AND original_artifact_id IS NOT NULL "
        "AND jsonb_array_length(error_codes) = 0) OR "
        "(import_state = 'incomplete' AND source_digest IS NOT NULL "
        "AND task_version_id IS NULL AND original_artifact_id IS NOT NULL "
        "AND jsonb_array_length(error_codes) > 0) OR "
        "(import_state = 'missing' AND source_digest IS NULL "
        "AND task_version_id IS NULL AND original_artifact_id IS NULL "
        "AND jsonb_array_length(error_codes) > 0) OR "
        "(import_state = 'blocked' AND task_version_id IS NULL "
        "AND jsonb_array_length(error_codes) > 0)",
        name="import_state_shape",
    ),
    CheckConstraint(
        "(self_source_exposure IS NULL AND source_public_exposure IS NULL "
        "AND independent_duplicate_eligible IS NULL) OR "
        "(self_source_exposure IS NOT NULL AND source_public_exposure IS NOT NULL "
        "AND independent_duplicate_eligible IS NOT NULL)",
        name="exposure_fields_together",
    ),
    Index("ix_benchmark_item_task", "task_version_id"),
    Index(
        "ix_benchmark_item_import_membership_index",
        "snapshot_id",
        "membership_index",
        unique=True,
        postgresql_where=text("import_state <> 'legacy_unverified'"),
    ),
)

benchmark_import_manifest = Table(
    "benchmark_import_manifest",
    metadata,
    pk(),
    Column("snapshot_id", Uuid(as_uuid=True), nullable=False),
    Column("source_uri", Text, nullable=False),
    Column("source_member", String(1024), nullable=False),
    Column("revision", String(64), nullable=False),
    Column("variant", String(24), nullable=False),
    Column("importer_version", String(64), nullable=False),
    Column("parser_config", JSONB, nullable=False),
    Column("parser_config_digest", String(71), nullable=False),
    Column("sample_seed", String(20), nullable=False),
    Column("selected_membership", JSONB, nullable=False),
    Column("membership_digest", String(71), nullable=False),
    Column("source_digest", String(71), nullable=False),
    fk("source_artifact_id", "artifact.id"),
    Column("source_visibility", String(16), nullable=False),
    Column("storage_visibility", String(16), nullable=False),
    Column("rights_state", String(24), nullable=False),
    Column("rights_evidence_digest", String(71), nullable=False),
    Column("rights_evidence_artifact_id", Uuid(as_uuid=True), nullable=False),
    Column("result_state", String(24), nullable=False),
    Column("source_error_codes", JSONB, nullable=False),
    created_at(),
    UniqueConstraint("snapshot_id"),
    ForeignKeyConstraint(
        ["snapshot_id", "membership_digest"],
        ["benchmark_snapshot.id", "benchmark_snapshot.membership_digest"],
        name="fk_benchmark_import_manifest_snapshot_membership",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["rights_evidence_artifact_id"],
        ["artifact.id"],
        name="fk_benchmark_import_rights_artifact",
        ondelete="RESTRICT",
    ),
    CheckConstraint("revision ~ '^[0-9a-f]{40}([0-9a-f]{24})?$'", name="revision_format"),
    CheckConstraint("parser_config_digest ~ '^sha256:[0-9a-f]{64}$'", name="parser_config_digest"),
    CheckConstraint("membership_digest ~ '^sha256:[0-9a-f]{64}$'", name="membership_digest"),
    CheckConstraint("source_digest ~ '^sha256:[0-9a-f]{64}$'", name="source_digest"),
    CheckConstraint(
        "rights_evidence_digest ~ '^sha256:[0-9a-f]{64}$'", name="rights_evidence_digest"
    ),
    CheckConstraint("variant IN ('official','original','sanitized','verified')", name="variant"),
    CheckConstraint("importer_version <> ''", name="importer_version_nonempty"),
    CheckConstraint(
        "source_visibility IN ('public','restricted','private')", name="source_visibility"
    ),
    CheckConstraint("storage_visibility IN ('private','restricted')", name="storage_visibility"),
    CheckConstraint("rights_state = 'approved'", name="approved_rights_only"),
    CheckConstraint("result_state IN ('complete','partial','blocked')", name="result_state"),
    CheckConstraint("jsonb_typeof(parser_config) = 'object'", name="parser_config_object"),
    CheckConstraint(
        "jsonb_typeof(selected_membership) = 'array' AND "
        "jsonb_array_length(selected_membership) = 100",
        name="selected_membership_100",
    ),
    CheckConstraint("jsonb_typeof(source_error_codes) = 'array'", name="source_errors_array"),
    CheckConstraint("sample_seed ~ '^(0|[1-9][0-9]{0,19})$'", name="sample_seed_format"),
)

benchmark_item_lineage = Table(
    "benchmark_item_lineage",
    metadata,
    pk(),
    fk("item_id", "benchmark_item.id"),
    Column("parent_benchmark_slug", String(96), nullable=False),
    Column("parent_item_key", String(255), nullable=False),
    Column("relation", String(24), nullable=False),
    Column("evidence_digest", String(71), nullable=False),
    fk("evidence_artifact_id", "artifact.id"),
    created_at(),
    UniqueConstraint(
        "item_id", "parent_benchmark_slug", "parent_item_key", "relation", name="parent"
    ),
    CheckConstraint("relation IN ('variant_of','derived_from','translated_from')", name="relation"),
    CheckConstraint("evidence_digest ~ '^sha256:[0-9a-f]{64}$'", name="evidence_digest"),
    Index("ix_benchmark_item_lineage_parent", "parent_benchmark_slug", "parent_item_key"),
)

audit_component = Table(
    "audit_component",
    metadata,
    pk(),
    fk("item_id", "benchmark_item.id"),
    Column("component_key", String(128), nullable=False),
    Column("component_digest", String(71), nullable=False),
    fk("content_artifact_id", "artifact.id", nullable=True),
    Column("visibility", String(16), nullable=False),
    created_at(),
    UniqueConstraint("item_id", "component_key"),
    CheckConstraint("component_digest ~ '^sha256:[0-9a-f]{64}$'", name="digest_format"),
    CheckConstraint("visibility IN ('private','restricted','public')", name="visibility"),
    Index("ix_audit_component_digest", "component_digest"),
)

fingerprint = Table(
    "fingerprint",
    metadata,
    pk(),
    fk("component_id", "audit_component.id"),
    Column("feature_kind", String(48), nullable=False),
    Column("method_version", String(255), nullable=False),
    Column("feature_digest", String(71), nullable=False),
    fk("private_artifact_id", "artifact.id"),
    created_at(),
    UniqueConstraint("component_id", "feature_kind", "method_version"),
    CheckConstraint("feature_digest ~ '^sha256:[0-9a-f]{64}$'", name="digest_format"),
    Index("ix_fingerprint_method_digest", "method_version", "feature_digest"),
)

corpus_source = Table(
    "corpus_source",
    metadata,
    pk(),
    Column("slug", String(96), nullable=False),
    Column("official_url", Text, nullable=False),
    fk("policy_document_id", "audit_document.id"),
    Column("rights_state", String(32), nullable=False),
    created_at(),
    UniqueConstraint("slug"),
    CheckConstraint(
        "rights_state IN ('unreviewed','needs_item_review','approved','blocked','gated')",
        name="rights_state",
    ),
    Index("ix_corpus_source_rights", "rights_state"),
)

corpus_snapshot = Table(
    "corpus_snapshot",
    metadata,
    pk(),
    fk("source_id", "corpus_source.id"),
    fk("document_id", "audit_document.id"),
    Column("version", String(255), nullable=False),
    Column("content_root", String(71), nullable=False),
    Column("index_digest", String(71), nullable=False),
    Column("coverage", JSONB, nullable=False),
    fk("rights_document_id", "audit_document.id"),
    created_at(),
    UniqueConstraint("source_id", "version"),
    CheckConstraint("content_root ~ '^sha256:[0-9a-f]{64}$'", name="content_root"),
    CheckConstraint("index_digest ~ '^sha256:[0-9a-f]{64}$'", name="index_digest"),
    CheckConstraint("jsonb_typeof(coverage) = 'object'", name="coverage_object"),
    Index("ix_corpus_snapshot_source_version", "source_id", "version"),
)

corpus_document = Table(
    "corpus_document",
    metadata,
    pk(),
    fk("snapshot_id", "corpus_snapshot.id"),
    Column("source_identity_digest", String(71), nullable=False),
    Column("content_digest", String(71), nullable=False),
    fk("content_artifact_id", "artifact.id", nullable=True),
    Column("source_date", DateTime(timezone=True), nullable=True),
    Column("date_precision", String(16), nullable=False),
    Column("lineage", JSONB, nullable=False),
    created_at(),
    UniqueConstraint("snapshot_id", "source_identity_digest"),
    CheckConstraint("source_identity_digest ~ '^sha256:[0-9a-f]{64}$'", name="identity_digest"),
    CheckConstraint("content_digest ~ '^sha256:[0-9a-f]{64}$'", name="content_digest"),
    CheckConstraint(
        "date_precision IN ('unknown','day','second','millisecond','microsecond')",
        name="date_precision",
    ),
    CheckConstraint("jsonb_typeof(lineage) = 'array'", name="lineage_array"),
    Index("ix_corpus_document_content", "content_digest"),
)

task_set = Table(
    "task_set",
    metadata,
    pk(),
    Column("name", String(160), nullable=False),
    Column("version", Integer, nullable=False),
    Column("digest", String(71), nullable=False),
    Column("split", String(32), nullable=False),
    Column("status", String(24), nullable=False),
    fk("manifest_artifact_id", "artifact.id"),
    Column("document", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("frozen_at", DateTime(timezone=True), nullable=True),
    Column("row_version", BigInteger, nullable=False, server_default=text("0")),
    created_at(),
    UniqueConstraint("name", "version"),
    UniqueConstraint("digest"),
    CheckConstraint("version > 0", name="version_positive"),
    CheckConstraint("status IN ('draft','validating','frozen','withdrawn')", name="status"),
    CheckConstraint("row_version >= 0", name="row_version_nonnegative"),
)

task_set_member = Table(
    "task_set_member",
    metadata,
    Column(
        "task_set_id",
        Uuid(as_uuid=True),
        ForeignKey("task_set.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "task_version_id",
        Uuid(as_uuid=True),
        ForeignKey("task_version.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("stratum_id", String(160), nullable=False),
    Column("sampling_weight_bp", Integer, nullable=False),
    PrimaryKeyConstraint("task_set_id", "task_version_id"),
    CheckConstraint("sampling_weight_bp > 0", name="sampling_weight_positive"),
)

config_document = Table(
    "config_document",
    metadata,
    pk(),
    Column("kind", String(64), nullable=False),
    Column("version_label", String(128), nullable=False),
    Column("digest", String(71), nullable=False),
    fk("canonical_artifact_id", "artifact.id"),
    Column("schema_version", Integer, nullable=False),
    Column("document", JSONB, nullable=False),
    created_at(),
    UniqueConstraint("kind", "digest"),
    CheckConstraint("schema_version > 0", name="schema_version_positive"),
    CheckConstraint("digest ~ '^sha256:[0-9a-f]{64}$'", name="digest_format"),
)

endpoint_registration = Table(
    "endpoint_registration",
    metadata,
    pk(),
    Column("provider_kind", String(48), nullable=False),
    Column("base_url_ref", Text, nullable=False),
    Column("secret_ref", Text, nullable=False),
    Column("network_policy_id", String(160), nullable=False),
    Column("approval_status", String(24), nullable=False),
    Column("capabilities_digest", String(71), nullable=False),
    Column("registered_by", String(255), nullable=False),
    Column("network_policy", JSONB, nullable=False),
    Column("declared_capabilities", JSONB, nullable=False),
    Column("conformance_report", JSONB, nullable=True),
    Column("approved_by", String(255), nullable=True),
    Column("approved_at", DateTime(timezone=True), nullable=True),
    Column("decision_reason", Text, nullable=True),
    Column("row_version", BigInteger, nullable=False, server_default=text("0")),
    created_at(),
    CheckConstraint(
        "approval_status IN ('pending','approved','rejected','revoked')", name="approval_status"
    ),
    CheckConstraint(
        "approval_status <> 'approved' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)",
        name="approved_has_approver",
    ),
    CheckConstraint(
        "capabilities_digest ~ '^sha256:[0-9a-f]{64}$'", name="capabilities_digest_format"
    ),
    CheckConstraint("row_version >= 0", name="row_version_nonnegative"),
)

model_revision = Table(
    "model_revision",
    metadata,
    pk(),
    Column("provider", String(64), nullable=False),
    Column("name", String(255), nullable=False),
    Column("immutable_revision", String(255), nullable=True),
    fk("endpoint_registration_id", "endpoint_registration.id", nullable=True),
    Column("cutoff_at", DateTime(timezone=True), nullable=True),
    Column("cutoff_source", Text, nullable=True),
    fk("capabilities_config_id", "config_document.id"),
    created_at(),
    UniqueConstraint("provider", "name", "immutable_revision"),
)

budget_account = Table(
    "budget_account",
    metadata,
    pk(),
    Column("scope_kind", String(32), nullable=False),
    Column("scope_id", String(255), nullable=False),
    fk("parent_account_id", "budget_account.id", nullable=True),
    Column("hard_limit_micro_usd", BigInteger, nullable=False),
    Column("spent_confirmed", BigInteger, nullable=False, server_default=text("0")),
    Column("reserved_open", BigInteger, nullable=False, server_default=text("0")),
    Column("uncertain_committed", BigInteger, nullable=False, server_default=text("0")),
    Column("row_version", BigInteger, nullable=False, server_default=text("0")),
    created_at(),
    UniqueConstraint("scope_kind", "scope_id"),
    CheckConstraint("hard_limit_micro_usd >= 0", name="hard_limit_nonnegative"),
    CheckConstraint(
        "spent_confirmed >= 0 AND reserved_open >= 0 AND uncertain_committed >= 0",
        name="balance_nonnegative",
    ),
    CheckConstraint("row_version >= 0", name="row_version_nonnegative"),
    CheckConstraint(
        "scope_kind IN ('campaign','run','attempt','evaluation','audit_run')", name="scope_kind"
    ),
)

budget_resource = Table(
    "budget_resource",
    metadata,
    pk(),
    fk("account_id", "budget_account.id"),
    Column("resource", String(24), nullable=False),
    Column("hard_limit", BigInteger, nullable=False),
    Column("spent_confirmed", BigInteger, nullable=False, server_default=text("0")),
    Column("reserved_open", BigInteger, nullable=False, server_default=text("0")),
    Column("uncertain_committed", BigInteger, nullable=False, server_default=text("0")),
    Column("row_version", BigInteger, nullable=False, server_default=text("0")),
    UniqueConstraint("account_id", "resource"),
    CheckConstraint("resource IN ('turns','input_tokens','output_tokens')", name="resource"),
    CheckConstraint("hard_limit >= 0", name="hard_limit_nonnegative"),
    CheckConstraint(
        "spent_confirmed >= 0 AND reserved_open >= 0 AND uncertain_committed >= 0",
        name="balance_nonnegative",
    ),
    CheckConstraint("row_version >= 0", name="row_version_nonnegative"),
)

campaign = Table(
    "campaign",
    metadata,
    pk(),
    Column("name", String(160), nullable=False),
    Column("status", String(24), nullable=False),
    Column("owner_subject", String(255), nullable=False),
    fk("budget_account_id", "budget_account.id", nullable=True),
    Column("row_version", BigInteger, nullable=False, server_default=text("0")),
    created_at(),
    CheckConstraint(
        "status IN ('draft','planned','running','cancelling','completed','failed','cancelled')",
        name="status",
    ),
    CheckConstraint("row_version >= 0", name="row_version_nonnegative"),
    Index("ix_campaign_status", "status"),
)

run = Table(
    "run",
    metadata,
    pk(),
    fk("campaign_id", "campaign.id"),
    fk("config_document_id", "config_document.id"),
    fk("task_set_id", "task_set.id"),
    fk("model_revision_id", "model_revision.id"),
    fk("audit_run_id", "audit_run.id", nullable=True),
    Column("purpose", String(32), nullable=True),
    Column("status", String(24), nullable=False),
    Column("created_by", String(255), nullable=False),
    Column("row_version", BigInteger, nullable=False, server_default=text("0")),
    created_at(),
    UniqueConstraint("campaign_id", "config_document_id"),
    CheckConstraint(
        "status IN ('planned','queued','running','cancelling','completed','failed','cancelled')",
        name="status",
    ),
    CheckConstraint(
        "(purpose IS NULL AND audit_run_id IS NULL) OR "
        "(purpose = 'audit_diagnostic' AND audit_run_id IS NOT NULL) OR "
        "(purpose IN ('representative','challenge') AND audit_run_id IS NULL)",
        name="purpose_audit_scope",
    ),
    CheckConstraint("row_version >= 0", name="row_version_nonnegative"),
    Index("ix_run_task_set", "task_set_id"),
)

attempt = Table(
    "attempt",
    metadata,
    pk(),
    fk("run_id", "run.id"),
    fk("task_version_id", "task_version.id"),
    Column("sample_index", Integer, nullable=False),
    Column("seed", Numeric(20, 0), nullable=False),
    Column("state", String(24), nullable=False),
    Column("failure_class", String(64), nullable=True),
    fk("checkpoint_artifact_id", "artifact.id", nullable=True),
    fk("candidate_artifact_id", "artifact.id", nullable=True),
    Column("row_version", BigInteger, nullable=False, server_default=text("0")),
    created_at(),
    UniqueConstraint("run_id", "task_version_id", "sample_index"),
    CheckConstraint("sample_index >= 0", name="sample_index_nonnegative"),
    CheckConstraint("seed >= 0 AND seed <= 18446744073709551615", name="seed_unsigned_64"),
    CheckConstraint(
        "state IN ('queued','running','completed','failed','cancelled','skipped')", name="state"
    ),
    CheckConstraint("row_version >= 0", name="row_version_nonnegative"),
    Index("ix_attempt_run_state", "run_id", "state"),
)

candidate = Table(
    "candidate",
    metadata,
    pk(),
    fk("attempt_id", "attempt.id"),
    Column("revision", Integer, nullable=False),
    Column("payload_digest", String(71), nullable=False),
    Column("submission_kind", String(32), nullable=False),
    Column("payload", JSONB, nullable=False),
    fk("canonical_artifact_id", "artifact.id"),
    Column("frozen_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    created_at(),
    UniqueConstraint("attempt_id", "revision"),
    CheckConstraint("revision > 0", name="revision_positive"),
    CheckConstraint("payload_digest ~ '^sha256:[0-9a-f]{64}$'", name="payload_digest_format"),
)

repair_run = Table(
    "repair_run",
    metadata,
    pk(),
    fk("attempt_id", "attempt.id"),
    Column("repair_run_id", String(128), nullable=False),
    Column("protocol_digest", String(71), nullable=False),
    Column("protocol", JSONB, nullable=False),
    Column("public_case_ids", JSONB, nullable=False),
    Column("rounds_committed", Integer, nullable=False),
    Column("spend", JSONB, nullable=False),
    Column("state", String(16), nullable=False),
    Column("selected_round_index", Integer, nullable=True),
    Column("run_digest", String(71), nullable=False),
    Column("row_version", BigInteger, nullable=False, server_default=text("0")),
    created_at(),
    UniqueConstraint("repair_run_id"),
    CheckConstraint("rounds_committed >= 0", name="repair_rounds_committed_nonnegative"),
    CheckConstraint("state IN ('open','complete')", name="repair_run_state"),
    CheckConstraint(
        "state <> 'complete' OR selected_round_index IS NOT NULL",
        name="complete_run_selects_a_round",
    ),
    CheckConstraint("run_digest ~ '^sha256:[0-9a-f]{64}$'", name="repair_run_digest_format"),
    CheckConstraint("row_version >= 0", name="row_version_nonnegative"),
)

repair_round = Table(
    "repair_round",
    metadata,
    pk(),
    fk("repair_run_id", "repair_run.id"),
    Column("round_index", Integer, nullable=False),
    Column("candidate_revision", Integer, nullable=False),
    Column("candidate_digest", String(71), nullable=True),
    Column("prompt_digest", String(71), nullable=False),
    Column("request_digest", String(71), nullable=False),
    Column("feedback_digest", String(71), nullable=True),
    Column("state", String(16), nullable=False),
    Column("public_results", JSONB, nullable=False),
    Column("input_tokens", Integer, nullable=False),
    Column("output_tokens", Integer, nullable=False),
    Column("cost_micros", BigInteger, nullable=False),
    created_at(),
    UniqueConstraint("repair_run_id", "round_index"),
    CheckConstraint("round_index >= 0", name="repair_round_index_nonnegative"),
    CheckConstraint("candidate_revision = round_index + 1", name="repair_round_revision"),
    CheckConstraint("state IN ('frozen','model_failure')", name="repair_round_state"),
    CheckConstraint(
        "state <> 'frozen' OR candidate_digest IS NOT NULL", name="frozen_round_has_candidate"
    ),
    CheckConstraint(
        "state <> 'model_failure' OR candidate_digest IS NULL", name="failed_round_has_no_candidate"
    ),
    CheckConstraint(
        "round_index = 0 OR feedback_digest IS NOT NULL", name="repair_round_has_feedback"
    ),
    Index("ix_repair_round_run", "repair_run_id"),
)

repair_delivery = Table(
    "repair_delivery",
    metadata,
    pk(),
    fk("repair_round_id", "repair_round.id"),
    Column("delivery_index", Integer, nullable=False),
    Column("input_tokens", Integer, nullable=False),
    Column("output_tokens", Integer, nullable=False),
    Column("cost_micros", BigInteger, nullable=False),
    Column("active_ms", BigInteger, nullable=False),
    created_at(),
    UniqueConstraint("repair_round_id", "delivery_index"),
    CheckConstraint("delivery_index >= 1", name="repair_delivery_index_positive"),
    CheckConstraint("input_tokens >= 0", name="repair_delivery_input_nonnegative"),
    CheckConstraint("output_tokens >= 0", name="repair_delivery_output_nonnegative"),
    CheckConstraint("cost_micros >= 0", name="repair_delivery_cost_nonnegative"),
    CheckConstraint("active_ms >= 0", name="repair_delivery_active_nonnegative"),
    Index("ix_repair_delivery_round", "repair_round_id"),
)

evaluation = Table(
    "evaluation",
    metadata,
    pk(),
    fk("attempt_id", "attempt.id"),
    fk("policy_config_id", "config_document.id"),
    Column("oracle_digest", String(71), nullable=False),
    Column("state", String(24), nullable=False),
    Column("gate", String(24), nullable=False),
    Column("failure_class", String(64), nullable=True),
    fk("evidence_manifest_id", "artifact.id", nullable=True),
    fk("supersedes_id", "evaluation.id", nullable=True),
    Column("row_version", BigInteger, nullable=False, server_default=text("0")),
    created_at(),
    UniqueConstraint("attempt_id", "policy_config_id", "oracle_digest"),
    CheckConstraint(
        "state IN ('queued','running','ready','failed','superseded','cancelled')", name="state"
    ),
    CheckConstraint("gate IN ('pass','fail','unknown','not_applicable')", name="gate"),
    CheckConstraint("oracle_digest ~ '^sha256:[0-9a-f]{64}$'", name="oracle_digest_format"),
    CheckConstraint(
        "state <> 'ready' OR (gate <> 'unknown' AND evidence_manifest_id IS NOT NULL)",
        name="ready_has_known_gate_and_evidence",
    ),
    CheckConstraint("row_version >= 0", name="row_version_nonnegative"),
    Index("ix_evaluation_attempt", "attempt_id"),
)

curation_round = Table(
    "curation_round",
    metadata,
    pk(),
    Column("state", String(24), nullable=False),
    Column("row_version", BigInteger, nullable=False, server_default=text("0")),
    created_at(),
    CheckConstraint(
        "state IN ('planned','queued','running','complete','blocked','cancelled')", name="state"
    ),
    CheckConstraint("row_version >= 0", name="row_version_nonnegative"),
)

discovery_search = Table(
    "discovery_search",
    metadata,
    pk(),
    Column("state", String(24), nullable=False),
    Column("row_version", BigInteger, nullable=False, server_default=text("0")),
    created_at(),
    CheckConstraint(
        "state IN ('planned','queued','running','complete','blocked','cancelled')", name="state"
    ),
    CheckConstraint("row_version >= 0", name="row_version_nonnegative"),
)

audit_run = Table(
    "audit_run",
    metadata,
    pk(),
    fk("plan_document_id", "audit_document.id"),
    fk("campaign_id", "campaign.id", nullable=True),
    Column("idempotency_key", String(255), nullable=False),
    Column("state", String(24), nullable=False),
    Column("current_stage", String(32), nullable=True),
    Column("dispatch_authorized", Boolean, nullable=False, server_default=text("false")),
    Column("reserved_query_units", Integer, nullable=False, server_default=text("0")),
    Column("reserved_storage_bytes", BigInteger, nullable=False, server_default=text("0")),
    Column("row_version", BigInteger, nullable=False, server_default=text("0")),
    created_at(),
    UniqueConstraint("plan_document_id", "idempotency_key"),
    CheckConstraint(
        "state IN ('draft','planned','queued','scanning','verifying','assessing',"
        "'review_required','complete','partial','blocked','cancelled')",
        name="state",
    ),
    CheckConstraint("length(idempotency_key) BETWEEN 1 AND 255", name="idempotency_key"),
    CheckConstraint(
        "reserved_query_units >= 0 AND reserved_storage_bytes >= 0", name="reservations"
    ),
    CheckConstraint("row_version >= 0", name="row_version_nonnegative"),
    Index("ix_audit_run_state_created", "state", "created_at"),
)

audit_query = Table(
    "audit_query",
    metadata,
    pk(),
    fk("audit_run_id", "audit_run.id"),
    fk("query_document_id", "audit_document.id"),
    Column("query_index", Integer, nullable=False),
    Column("logical_call_key", String(255), nullable=False),
    Column("state", String(24), nullable=False),
    Column("reserved_units", Integer, nullable=False),
    Column("row_version", BigInteger, nullable=False, server_default=text("0")),
    created_at(),
    UniqueConstraint("audit_run_id", "query_index"),
    UniqueConstraint("audit_run_id", "logical_call_key"),
    CheckConstraint("query_index >= 0", name="query_index_nonnegative"),
    CheckConstraint("reserved_units >= 1", name="reserved_units_positive"),
    CheckConstraint(
        "state IN ('planned','queued','running','complete','truncated','failed','blocked',"
        "'cancelled')",
        name="state",
    ),
    CheckConstraint("row_version >= 0", name="row_version_nonnegative"),
    Index("ix_audit_query_run_state", "audit_run_id", "state"),
)

monitor_slot = Table(
    "monitor_slot",
    metadata,
    pk(),
    fk("policy_document_id", "audit_document.id"),
    fk("audit_run_id", "audit_run.id", nullable=True),
    Column("slot_key", String(32), nullable=False),
    Column("scheduled_at", DateTime(timezone=True), nullable=False),
    Column("local_day", Date, nullable=False),
    Column("refresh_kind", String(16), nullable=False),
    Column("source_document_ids", JSONB, nullable=False),
    Column("state", String(24), nullable=False),
    Column("dispatch_authorized", Boolean, nullable=False, server_default=text("false")),
    Column("query_units", BigInteger, nullable=False, server_default=text("0")),
    Column("retry_reserve_units", BigInteger, nullable=False, server_default=text("0")),
    Column("reserved_storage_bytes", BigInteger, nullable=False, server_default=text("0")),
    Column("reserved_cost_micro_usd", BigInteger, nullable=False, server_default=text("0")),
    Column("missed_slots_before", Integer, nullable=False, server_default=text("0")),
    Column("attempts", Integer, nullable=False, server_default=text("0")),
    Column("max_attempts", Integer, nullable=False),
    Column("next_retry_at", DateTime(timezone=True), nullable=True),
    Column("last_error_code", String(40), nullable=True),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("row_version", BigInteger, nullable=False, server_default=text("0")),
    created_at(),
    UniqueConstraint("policy_document_id", "slot_key", name="uq_monitor_slot_policy_slot"),
    CheckConstraint("length(slot_key) BETWEEN 1 AND 32", name="slot_key"),
    CheckConstraint("refresh_kind IN ('incremental','full')", name="refresh_kind"),
    CheckConstraint(
        "state IN ('missed','planned','retry_wait','queued','running','complete','partial',"
        "'failed','blocked','cancelled')",
        name="state",
    ),
    CheckConstraint(
        "state NOT IN ('queued','running','complete') OR "
        "(dispatch_authorized AND audit_run_id IS NOT NULL)",
        name="authorized_dispatch",
    ),
    CheckConstraint("jsonb_typeof(source_document_ids) = 'array'", name="sources_array"),
    CheckConstraint(
        "query_units >= 0 AND retry_reserve_units >= query_units "
        "AND reserved_storage_bytes >= 0 AND reserved_cost_micro_usd >= 0 "
        "AND missed_slots_before >= 0",
        name="reservations",
    ),
    CheckConstraint("attempts >= 0 AND attempts <= max_attempts", name="attempts"),
    CheckConstraint("max_attempts BETWEEN 1 AND 11", name="max_attempts"),
    CheckConstraint("(state = 'retry_wait') = (next_retry_at IS NOT NULL)", name="retry_schedule"),
    CheckConstraint(
        "last_error_code IS NULL OR last_error_code IN "
        "('source_unavailable','rate_limited','connector_error','coverage_incomplete',"
        "'budget_exhausted','authorization_required')",
        name="error_code",
    ),
    CheckConstraint("row_version >= 0", name="row_version_nonnegative"),
    Index("ix_monitor_slot_due", "state", "next_retry_at", "scheduled_at"),
    Index("ix_monitor_slot_policy_day", "policy_document_id", "local_day"),
)

monitor_slot_source = Table(
    "monitor_slot_source",
    metadata,
    pk(),
    fk("slot_id", "monitor_slot.id"),
    fk("policy_document_id", "audit_document.id"),
    fk("source_document_id", "audit_document.id"),
    Column("local_day", Date, nullable=False),
    Column("reserved_query_units", BigInteger, nullable=False),
    created_at(),
    UniqueConstraint("slot_id", "source_document_id", name="uq_monitor_slot_source"),
    CheckConstraint("reserved_query_units >= 1", name="reserved_units_positive"),
    Index(
        "ix_monitor_source_daily_reservations",
        "policy_document_id",
        "source_document_id",
        "local_day",
    ),
)

monitor_alert_inbox = Table(
    "monitor_alert_inbox",
    metadata,
    pk(),
    fk("alert_document_id", "audit_document.id"),
    Column("recipient_subject", String(512), nullable=False),
    Column("read_at", DateTime(timezone=True), nullable=True),
    created_at(),
    UniqueConstraint(
        "alert_document_id", "recipient_subject", name="uq_monitor_alert_inbox_recipient"
    ),
    CheckConstraint("length(recipient_subject) BETWEEN 1 AND 512", name="recipient_subject"),
    Index("ix_monitor_alert_inbox_recipient", "recipient_subject", "created_at"),
)

audit_checkpoint = Table(
    "audit_checkpoint",
    metadata,
    pk(),
    fk("audit_run_id", "audit_run.id"),
    Column("checkpoint_seq", BigInteger, nullable=False),
    Column("fence", BigInteger, nullable=False),
    Column("scope_digest", String(71), nullable=False),
    Column("checkpoint", JSONB, nullable=False),
    created_at(),
    UniqueConstraint("audit_run_id", "checkpoint_seq"),
    CheckConstraint("checkpoint_seq >= 1 AND fence >= 1", name="sequence_fence_positive"),
    CheckConstraint("scope_digest ~ '^sha256:[0-9a-f]{64}$'", name="scope_digest_format"),
    CheckConstraint("jsonb_typeof(checkpoint) = 'object'", name="checkpoint_object"),
    Index("ix_audit_checkpoint_run", "audit_run_id", "checkpoint_seq"),
)

match_candidate = Table(
    "match_candidate",
    metadata,
    pk(),
    fk("audit_run_id", "audit_run.id"),
    fk("task_version_id", "task_version.id"),
    fk("source_document_id", "audit_document.id"),
    fk("evidence_document_id", "audit_document.id"),
    Column("relation", String(32), nullable=False),
    Column("confidence", String(16), nullable=True),
    Column("confidence_null_reason", String(32), nullable=True),
    Column("state", String(24), nullable=False),
    created_at(),
    UniqueConstraint(
        "audit_run_id", "task_version_id", "source_document_id", "evidence_document_id"
    ),
    CheckConstraint(
        "relation IN ('exact_component','near_exact_component','semantic_duplicate',"
        "'shared_family','shared_concept','no_substantive_match','unresolved')",
        name="relation",
    ),
    CheckConstraint(
        "confidence IS NULL OR confidence ~ '^(0|[1-9][0-9]?|100)\\.[0-9]{6}$'",
        name="confidence_decimal",
    ),
    CheckConstraint(
        "(confidence IS NULL AND confidence_null_reason IN "
        "('not_run','unavailable','not_applicable','insufficient_coverage','withheld')) OR "
        "(confidence IS NOT NULL AND confidence_null_reason IS NULL)",
        name="confidence_null_reason",
    ),
    CheckConstraint(
        "state IN ('proposed','verified','review_required','accepted','rejected','disputed',"
        "'superseded')",
        name="state",
    ),
    Index("ix_match_candidate_task_state", "task_version_id", "state"),
)

match_review = Table(
    "match_review",
    metadata,
    pk(),
    fk("candidate_id", "match_candidate.id"),
    Column("review_seq", Integer, nullable=False),
    Column("reviewer_subject", String(255), nullable=False),
    Column("decision", String(24), nullable=False),
    fk("review_document_id", "audit_document.id"),
    created_at(),
    UniqueConstraint("candidate_id", "review_seq"),
    CheckConstraint("review_seq >= 1", name="review_seq_positive"),
    CheckConstraint("decision IN ('accepted','rejected','disputed','superseded')", name="decision"),
    Index("ix_match_review_candidate", "candidate_id", "review_seq"),
)

risk_assessment = Table(
    "risk_assessment",
    metadata,
    pk(),
    fk("task_version_id", "task_version.id"),
    fk("plan_document_id", "audit_document.id"),
    fk("policy_document_id", "audit_document.id"),
    fk("context_document_id", "audit_document.id", nullable=True),
    fk("document_id", "audit_document.id"),
    fk("supersedes_id", "risk_assessment.id", nullable=True),
    Column("observed_index", Numeric(9, 6), nullable=True),
    Column("lower_bound", Numeric(9, 6), nullable=False),
    Column("upper_bound", Numeric(9, 6), nullable=False),
    Column("state", String(32), nullable=False),
    created_at(),
    UniqueConstraint("task_version_id", "policy_document_id", "document_id"),
    CheckConstraint(
        "observed_index IS NULL OR observed_index BETWEEN 0 AND 100", name="observed_range"
    ),
    CheckConstraint(
        "lower_bound >= 0 AND lower_bound <= upper_bound AND upper_bound <= 100", name="bounds"
    ),
    CheckConstraint(
        "state IN ('low_observed','medium_observed','high_observed','insufficient_evidence',"
        "'not_applicable')",
        name="state",
    ),
    CheckConstraint("supersedes_id IS NULL OR supersedes_id <> id", name="not_self_successor"),
    Index("ix_risk_assessment_task_policy", "task_version_id", "policy_document_id"),
)

temporal_assessment = Table(
    "temporal_assessment",
    metadata,
    pk(),
    fk("task_version_id", "task_version.id"),
    fk("context_document_id", "audit_document.id", nullable=True),
    fk("document_id", "audit_document.id"),
    fk("supersedes_id", "temporal_assessment.id", nullable=True),
    Column("state", String(40), nullable=False),
    created_at(),
    UniqueConstraint("task_version_id", "document_id"),
    CheckConstraint(
        "state IN ('post_declared_cutoff','pre_cutoff_exposure_detected','interval_overlap',"
        "'unknown_cutoff','unknown_source_time','mutable_model_context')",
        name="state",
    ),
    CheckConstraint("supersedes_id IS NULL OR supersedes_id <> id", name="not_self_successor"),
    Index("ix_temporal_assessment_task", "task_version_id"),
)

stage_job = Table(
    "stage_job",
    metadata,
    pk(),
    fk("attempt_id", "attempt.id", nullable=True),
    fk("evaluation_id", "evaluation.id", nullable=True),
    fk("release_id", "release.id", nullable=True),
    fk("curation_round_id", "curation_round.id", nullable=True),
    fk("discovery_search_id", "discovery_search.id", nullable=True),
    fk("audit_run_id", "audit_run.id", nullable=True),
    fk("input_artifact_id", "artifact.id", nullable=True),
    Column("stage", String(48), nullable=False),
    Column("shard_key", String(255), nullable=False, server_default=text("''")),
    Column("input_digest", String(71), nullable=False),
    Column("logical_key", String(71), nullable=False),
    Column("state", String(24), nullable=False),
    Column("required", Boolean, nullable=False, server_default=text("true")),
    Column("queue_class", String(64), nullable=False),
    Column("resource_class", String(64), nullable=False, server_default=text("'default'")),
    fk("fairness_campaign_id", "campaign.id", nullable=True),
    Column("provider_key", String(128), nullable=False, server_default=text("'system'")),
    Column("quality_gate", String(24), nullable=True),
    Column("result_document", JSONB, nullable=True),
    Column("skip_reason", String(64), nullable=True),
    Column("priority", Integer, nullable=False, server_default=text("0")),
    Column("available_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("lease_until", DateTime(timezone=True), nullable=True),
    Column("owner_id", String(255), nullable=True),
    Column("fence", BigInteger, nullable=False, server_default=text("0")),
    Column("deliveries", Integer, nullable=False, server_default=text("0")),
    Column("max_deliveries", Integer, nullable=False, server_default=text("3")),
    fk("output_artifact_id", "artifact.id", nullable=True),
    Column("error_code", String(64), nullable=True),
    Column("row_version", BigInteger, nullable=False, server_default=text("0")),
    created_at(),
    UniqueConstraint("logical_key"),
    CheckConstraint(
        "num_nonnulls(attempt_id,evaluation_id,release_id,curation_round_id,"
        "discovery_search_id,audit_run_id) = 1",
        name="one_scope",
    ),
    CheckConstraint(
        "state IN ('blocked','queued','leased','succeeded','retry_wait','dead','cancelled',"
        "'skipped')",
        name="state",
    ),
    CheckConstraint(
        "fence >= 0 AND deliveries >= 0 AND max_deliveries >= 1", name="delivery_bounds"
    ),
    CheckConstraint("row_version >= 0", name="row_version_nonnegative"),
    CheckConstraint(
        "quality_gate IS NULL OR quality_gate IN ('pass','fail','unknown','not_applicable')",
        name="quality_gate",
    ),
    CheckConstraint(
        "(state = 'skipped' AND skip_reason IS NOT NULL) OR "
        "(state <> 'skipped' AND skip_reason IS NULL)",
        name="skip_reason_shape",
    ),
    CheckConstraint(
        "(state = 'leased' AND lease_until IS NOT NULL AND owner_id IS NOT NULL) OR "
        "(state <> 'leased' AND lease_until IS NULL AND owner_id IS NULL)",
        name="lease_shape",
    ),
    Index(
        "ix_stage_job_ready",
        "queue_class",
        "priority",
        "available_at",
        postgresql_where=text("state IN ('queued','retry_wait')"),
    ),
    Index("ix_stage_job_expired", "lease_until", postgresql_where=text("state = 'leased'")),
    Index("ix_stage_job_fairness", "fairness_campaign_id", "provider_key", "state"),
)

stage_dependency = Table(
    "stage_dependency",
    metadata,
    Column(
        "job_id",
        Uuid(as_uuid=True),
        ForeignKey("stage_job.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "prerequisite_job_id",
        Uuid(as_uuid=True),
        ForeignKey("stage_job.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("condition", String(24), nullable=False, server_default=text("'success'")),
    Column("accepted_skip_reasons", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    PrimaryKeyConstraint("job_id", "prerequisite_job_id"),
    CheckConstraint("job_id <> prerequisite_job_id", name="not_self_dependency"),
    CheckConstraint(
        "condition IN ('success','gate_pass','gate_fail','terminal')", name="condition"
    ),
)

worker_registration = Table(
    "worker_registration",
    metadata,
    pk(),
    Column("workload_identity", String(255), nullable=False),
    Column("lane", String(32), nullable=False),
    Column("hardware_class", String(128), nullable=False),
    Column("allowed_queue_classes", JSONB, nullable=False),
    Column("allowed_resource_classes", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("status", String(24), nullable=False),
    Column("last_heartbeat_at", DateTime(timezone=True), nullable=True),
    Column("driver_identity", String(255), nullable=False),
    Column("audit_capable", Boolean, nullable=False, server_default=text("false")),
    Column("row_version", BigInteger, nullable=False, server_default=text("0")),
    created_at(),
    UniqueConstraint("workload_identity"),
    CheckConstraint("status IN ('active','draining','disabled','quarantined')", name="status"),
    CheckConstraint("row_version >= 0", name="row_version_nonnegative"),
)

capacity_slot = Table(
    "capacity_slot",
    metadata,
    pk(),
    fk("worker_id", "worker_registration.id"),
    fk("resource_spec_config_id", "config_document.id"),
    Column("slot_key", String(128), nullable=False),
    Column("resource_class", String(64), nullable=False, server_default=text("'default'")),
    Column("state", String(24), nullable=False),
    fk("job_id", "stage_job.id", nullable=True),
    Column("fence", BigInteger, nullable=True),
    Column("guest_id", String(255), nullable=True),
    Column("row_version", BigInteger, nullable=False, server_default=text("0")),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("worker_id", "slot_key"),
    CheckConstraint(
        "state IN ('available','reserved','busy','cleanup','draining','disabled')", name="state"
    ),
    CheckConstraint(
        "(state IN ('reserved','busy','cleanup') AND job_id IS NOT NULL AND fence IS NOT NULL) OR "
        "(state IN ('available','draining','disabled') AND job_id IS NULL AND fence IS NULL "
        "AND guest_id IS NULL)",
        name="assignment_shape",
    ),
    CheckConstraint("row_version >= 0", name="row_version_nonnegative"),
)

Index(
    "uq_capacity_slot_active_job",
    capacity_slot.c.job_id,
    unique=True,
    postgresql_where=capacity_slot.c.state.in_(["reserved", "busy", "cleanup"]),
)

stage_job_event = Table(
    "stage_job_event",
    metadata,
    pk(),
    fk("job_id", "stage_job.id"),
    Column("event_seq", BigInteger, nullable=False),
    Column("event_kind", String(64), nullable=False),
    Column("actor", String(255), nullable=False),
    Column("fence", BigInteger, nullable=True),
    Column("details", JSONB, nullable=False),
    created_at(),
    UniqueConstraint("job_id", "event_seq"),
    CheckConstraint("event_seq >= 1", name="event_seq_positive"),
    Index("ix_stage_job_event_job", "job_id", "event_seq"),
)

stage_execution = Table(
    "stage_execution",
    metadata,
    pk(),
    fk("job_id", "stage_job.id"),
    Column("fence", BigInteger, nullable=False),
    Column("worker_id", String(255), nullable=False),
    created_at("started_at"),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    Column("result", String(24), nullable=True),
    Column("failure_class", String(64), nullable=True),
    fk("environment_artifact_id", "artifact.id", nullable=True),
    fk("output_manifest_id", "artifact.id", nullable=True),
    UniqueConstraint("job_id", "fence"),
    CheckConstraint("fence >= 0", name="fence_nonnegative"),
    CheckConstraint(
        "result IS NULL OR result IN ('succeeded','failed','cancelled','lost')", name="result"
    ),
)

artifact_edge = Table(
    "artifact_edge",
    metadata,
    Column(
        "parent_artifact_id",
        Uuid(as_uuid=True),
        ForeignKey("artifact.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "child_artifact_id",
        Uuid(as_uuid=True),
        ForeignKey("artifact.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("relation", String(48), nullable=False),
    PrimaryKeyConstraint("parent_artifact_id", "child_artifact_id", "relation"),
    CheckConstraint("parent_artifact_id <> child_artifact_id", name="not_self_edge"),
)

observation = Table(
    "observation",
    metadata,
    pk(),
    fk("evaluation_id", "evaluation.id"),
    fk("stage_execution_id", "stage_execution.id"),
    fk("canonical_artifact_id", "artifact.id"),
    Column("check_id", String(160), nullable=False),
    Column("issue_key", String(255), nullable=True),
    Column("status", String(32), nullable=False),
    Column("primary_owner", String(64), nullable=True),
    Column("baseline_relation", String(32), nullable=True),
    Column("document", JSONB, nullable=False),
    created_at(),
    UniqueConstraint("evaluation_id", "check_id", "canonical_artifact_id"),
)

call_intent = Table(
    "call_intent",
    metadata,
    pk(),
    fk("attempt_id", "attempt.id", nullable=True),
    fk("evaluation_id", "evaluation.id", nullable=True),
    fk("audit_run_id", "audit_run.id", nullable=True),
    fk("diagnostic_audit_run_id", "audit_run.id", nullable=True),
    Column("logical_call_key", String(255), nullable=False),
    Column("request_digest", String(71), nullable=False),
    fk("model_config_id", "config_document.id"),
    Column("price_snapshot", JSONB, nullable=False),
    fk("request_artifact_id", "artifact.id", nullable=True),
    Column("state", String(24), nullable=False),
    created_at(),
    CheckConstraint("num_nonnulls(attempt_id,evaluation_id,audit_run_id) = 1", name="one_scope"),
    CheckConstraint(
        "diagnostic_audit_run_id IS NULL OR (attempt_id IS NOT NULL AND audit_run_id IS NULL)",
        name="diagnostic_audit_context_scope",
    ),
    CheckConstraint("request_digest ~ '^sha256:[0-9a-f]{64}$'", name="request_digest_format"),
    CheckConstraint(
        "state IN ('reserved','dispatching','settled','uncertain','failed')", name="state"
    ),
)
Index(
    "uq_call_intent_attempt_key",
    call_intent.c.attempt_id,
    call_intent.c.logical_call_key,
    unique=True,
    postgresql_where=call_intent.c.attempt_id.is_not(None),
)
Index(
    "uq_call_intent_evaluation_key",
    call_intent.c.evaluation_id,
    call_intent.c.logical_call_key,
    unique=True,
    postgresql_where=call_intent.c.evaluation_id.is_not(None),
)
Index(
    "uq_call_intent_audit_run_key",
    call_intent.c.audit_run_id,
    call_intent.c.logical_call_key,
    unique=True,
    postgresql_where=call_intent.c.audit_run_id.is_not(None),
)
Index(
    "ix_call_intent_diagnostic_audit_run",
    call_intent.c.diagnostic_audit_run_id,
    postgresql_where=call_intent.c.diagnostic_audit_run_id.is_not(None),
)

call_delivery = Table(
    "call_delivery",
    metadata,
    pk(),
    fk("intent_id", "call_intent.id"),
    Column("delivery_index", Integer, nullable=False),
    Column("dispatched_at", DateTime(timezone=True), nullable=False),
    Column("responded_at", DateTime(timezone=True), nullable=True),
    Column("provider_request_id", String(255), nullable=True),
    Column("status", String(24), nullable=False),
    Column("failure_code", String(64), nullable=True),
    fk("raw_response_artifact_id", "artifact.id", nullable=True),
    fk("normalized_response_artifact_id", "artifact.id", nullable=True),
    UniqueConstraint("intent_id", "delivery_index"),
    CheckConstraint("delivery_index >= 0", name="delivery_index_nonnegative"),
    CheckConstraint("status IN ('dispatching','responded','failed','ambiguous')", name="status"),
)

Index(
    "uq_call_delivery_one_response",
    call_delivery.c.intent_id,
    unique=True,
    postgresql_where=call_delivery.c.status == "responded",
)
Index(
    "uq_call_delivery_one_in_flight",
    call_delivery.c.intent_id,
    unique=True,
    postgresql_where=call_delivery.c.status == "dispatching",
)

usage_record = Table(
    "usage_record",
    metadata,
    pk(),
    fk("delivery_id", "call_delivery.id"),
    Column("input_tokens", BigInteger, nullable=True),
    Column("output_tokens", BigInteger, nullable=True),
    Column("reasoning_tokens", BigInteger, nullable=True),
    Column("usage_available", JSONB, nullable=False),
    Column("source", String(32), nullable=False),
    Column("actual_cost_micro_usd", BigInteger, nullable=True),
    Column("estimated_cost_micro_usd", BigInteger, nullable=True),
    Column("settlement_revision", Integer, nullable=False),
    created_at(),
    UniqueConstraint("delivery_id", "settlement_revision"),
    CheckConstraint("settlement_revision > 0", name="settlement_revision_positive"),
    CheckConstraint(
        "source IN ('provider_reported','unavailable','reconciliation')", name="source"
    ),
    CheckConstraint(
        "COALESCE(input_tokens,0) >= 0 AND COALESCE(output_tokens,0) >= 0 AND "
        "COALESCE(reasoning_tokens,0) >= 0",
        name="tokens_nonnegative",
    ),
)

budget_reservation = Table(
    "budget_reservation",
    metadata,
    pk(),
    fk("account_id", "budget_account.id"),
    fk("call_intent_id", "call_intent.id"),
    Column("delivery_index", Integer, nullable=False, server_default=text("0")),
    Column("amount_micro_usd", BigInteger, nullable=False),
    Column("state", String(24), nullable=False),
    created_at(),
    UniqueConstraint("account_id", "call_intent_id", "delivery_index"),
    CheckConstraint("amount_micro_usd >= 0", name="amount_nonnegative"),
    CheckConstraint("delivery_index >= 0", name="delivery_index_nonnegative"),
    CheckConstraint("state IN ('open','settled','released','uncertain')", name="state"),
)

accounting_entry = Table(
    "accounting_entry",
    metadata,
    pk(),
    fk("account_id", "budget_account.id"),
    fk("call_intent_id", "call_intent.id", nullable=True),
    fk("delivery_id", "call_delivery.id", nullable=True),
    Column("entry_kind", String(24), nullable=False),
    Column("resource", String(24), nullable=False, server_default=text("'money'")),
    Column("from_bucket", String(24), nullable=False, server_default=text("'none'")),
    Column("to_bucket", String(24), nullable=False, server_default=text("'none'")),
    Column("amount_micro_usd", BigInteger, nullable=False),
    Column("reason", Text, nullable=False),
    created_at(),
    CheckConstraint(
        "entry_kind IN ('reservation','charge','release','adjustment','retain')",
        name="entry_kind",
    ),
    CheckConstraint(
        "resource IN ('money','turns','input_tokens','output_tokens')", name="resource"
    ),
    CheckConstraint(
        "from_bucket IN ('none','reserved_open','uncertain_committed','spent_confirmed') AND "
        "to_bucket IN ('none','reserved_open','uncertain_committed','spent_confirmed') AND "
        "from_bucket <> to_bucket",
        name="buckets",
    ),
    CheckConstraint("amount_micro_usd > 0", name="amount_positive"),
    CheckConstraint("num_nonnulls(call_intent_id,delivery_id) >= 1", name="linked_call"),
)

judge_cohort = Table(
    "judge_cohort",
    metadata,
    pk(),
    Column("cohort_id", String(160), nullable=False),
    Column("evaluation_version", Integer, nullable=False),
    Column("panel_id", String(160), nullable=False),
    Column("panel_digest", String(71), nullable=False),
    Column("rubric_digest", String(71), nullable=False),
    Column("candidate_model_config_ids", JSONB, nullable=False),
    Column("cohort_digest", String(71), nullable=False),
    created_at(),
    UniqueConstraint("cohort_id", "evaluation_version"),
    CheckConstraint("evaluation_version > 0", name="evaluation_version_positive"),
    CheckConstraint("panel_digest ~ '^sha256:[0-9a-f]{64}$'", name="panel_digest_format"),
    CheckConstraint("cohort_digest ~ '^sha256:[0-9a-f]{64}$'", name="cohort_digest_format"),
)

judge_packet = Table(
    "judge_packet",
    metadata,
    pk(),
    fk("evaluation_id", "evaluation.id"),
    Column("packet_digest", String(71), nullable=False),
    Column("rubric_digest", String(71), nullable=False),
    Column("panel_digest", String(71), nullable=False),
    Column("required_votes", Integer, nullable=False),
    fk("packet_artifact_id", "artifact.id"),
    fk("cohort_id", "judge_cohort.id", nullable=True),
    Column("language", String(16), nullable=False),
    Column("packet_role", String(16), nullable=False, server_default=text("'scored'")),
    created_at(),
    UniqueConstraint("evaluation_id", "packet_digest", "panel_digest"),
    CheckConstraint("required_votes >= 3", name="minimum_votes"),
    CheckConstraint("language IN ('python','rust')", name="language"),
    CheckConstraint("packet_role IN ('scored','calibration')", name="packet_role"),
    CheckConstraint("packet_digest ~ '^sha256:[0-9a-f]{64}$'", name="packet_digest_format"),
)

judge_delivery = Table(
    "judge_delivery",
    metadata,
    pk(),
    fk("packet_id", "judge_packet.id"),
    Column("vote_index", Integer, nullable=False),
    Column("delivery_index", Integer, nullable=False),
    fk("call_delivery_id", "call_delivery.id", nullable=True),
    fk("call_intent_id", "call_intent.id", nullable=True),
    fk("raw_artifact_id", "artifact.id", nullable=True),
    Column("status", String(24), nullable=False),
    Column("invalid_reason", String(48), nullable=True),
    Column("detail", Text, nullable=False, server_default=text("''")),
    Column("judge_revision", String(128), nullable=False),
    Column("seed", BigInteger, nullable=True),
    Column("seed_supported", Boolean, nullable=False, server_default=text("false")),
    Column("repair_instruction_id", String(64), nullable=True),
    Column("raw_response_digest", String(71), nullable=True),
    created_at(),
    UniqueConstraint("packet_id", "vote_index", "delivery_index"),
    CheckConstraint("vote_index >= 0", name="vote_index_nonnegative"),
    CheckConstraint("delivery_index >= 0 AND delivery_index <= 2", name="delivery_index_bound"),
    CheckConstraint("status IN ('valid','invalid','transport_failure')", name="status"),
    CheckConstraint(
        "(status = 'invalid') = (invalid_reason IS NOT NULL)", name="invalid_reason_matches_status"
    ),
    CheckConstraint("seed IS NULL OR seed >= 0", name="seed_nonnegative"),
    Index("ix_judge_delivery_packet_status", "packet_id", "status"),
)

judge_vote = Table(
    "judge_vote",
    metadata,
    pk(),
    fk("packet_id", "judge_packet.id"),
    Column("vote_index", Integer, nullable=False),
    fk("call_delivery_id", "call_delivery.id"),
    fk("normalized_artifact_id", "artifact.id", nullable=True),
    Column("status", String(24), nullable=False),
    created_at(),
    UniqueConstraint("packet_id", "vote_index"),
    CheckConstraint("vote_index >= 0", name="vote_index_nonnegative"),
    CheckConstraint("status = 'valid'", name="status_is_valid"),
)

judge_result = Table(
    "judge_result",
    metadata,
    pk(),
    fk("packet_id", "judge_packet.id"),
    fk("evaluation_id", "evaluation.id"),
    Column("status", String(24), nullable=False),
    fk("report_artifact_id", "artifact.id"),
    Column("report_digest", String(71), nullable=False),
    Column("result_index", Integer, nullable=False, server_default=text("0")),
    created_at(),
    UniqueConstraint("packet_id", "result_index"),
    CheckConstraint("status IN ('ready','needs_review','infra_blocked')", name="status"),
    CheckConstraint("result_index >= 0", name="result_index_nonnegative"),
    CheckConstraint("report_digest ~ '^sha256:[0-9a-f]{64}$'", name="report_digest_format"),
)

judge_item_result = Table(
    "judge_item_result",
    metadata,
    pk(),
    fk("result_id", "judge_result.id"),
    Column("item_id", String(64), nullable=False),
    Column("dimension", String(32), nullable=False),
    Column("status", String(24), nullable=False),
    Column("mean_score", Numeric(12, 6), nullable=True),
    Column("vote_count", Integer, nullable=False),
    Column("required_votes", Integer, nullable=False),
    Column("source", String(24), nullable=False),
    fk("adjudication_id", "adjudication.id", nullable=True),
    Column("triggers", JSONB, nullable=False),
    Column("vote_scores", JSONB, nullable=False),
    created_at(),
    UniqueConstraint("result_id", "item_id"),
    CheckConstraint("status IN ('ready','needs_review','infra_blocked')", name="status"),
    CheckConstraint("source IN ('judge_votes','adjudication')", name="source"),
    CheckConstraint(
        "mean_score IS NULL OR (mean_score >= 0 AND mean_score <= 1)", name="mean_range"
    ),
    CheckConstraint("vote_count >= 0 AND vote_count <= required_votes", name="vote_count_range"),
    CheckConstraint(
        "(source = 'adjudication') = (adjudication_id IS NOT NULL)",
        name="adjudication_matches_source",
    ),
)

calibration_label = Table(
    "calibration_label",
    metadata,
    pk(),
    fk("cohort_id", "judge_cohort.id"),
    fk("packet_id", "judge_packet.id"),
    Column("packet_digest", String(71), nullable=False),
    Column("item_id", String(64), nullable=False),
    Column("score", Numeric(12, 6), nullable=False),
    Column("labeler_subject", String(255), nullable=False),
    Column("qualification", String(64), nullable=False),
    Column("rationale", Text, nullable=False),
    Column("cited_anchor_ids", JSONB, nullable=False),
    Column("labeled_at", DateTime(timezone=True), nullable=False),
    created_at(),
    UniqueConstraint("packet_digest", "item_id", "labeler_subject"),
    CheckConstraint("score >= 0 AND score <= 1", name="score_range"),
    CheckConstraint("packet_digest ~ '^sha256:[0-9a-f]{64}$'", name="packet_digest_format"),
)

adjudication = Table(
    "adjudication",
    metadata,
    pk(),
    fk("evaluation_id", "evaluation.id"),
    Column("target_kind", String(48), nullable=False),
    Column("target_id", String(255), nullable=False),
    fk("resolution_artifact_id", "artifact.id"),
    Column("reviewer_subject", String(255), nullable=False),
    Column("reason", Text, nullable=False),
    fk("supersedes_id", "adjudication.id", nullable=True),
    created_at(),
)

bug_annotation = Table(
    "bug_annotation",
    metadata,
    pk(),
    fk("task_version_id", "task_version.id"),
    Column("oracle_digest", String(71), nullable=False),
    Column("bug_key", String(255), nullable=False),
    fk("private_artifact_id", "artifact.id"),
    created_at(),
    UniqueConstraint("task_version_id", "oracle_digest", "bug_key"),
)

bug_match = Table(
    "bug_match",
    metadata,
    pk(),
    fk("evaluation_id", "evaluation.id"),
    Column("submitted_finding_id", String(255), nullable=False),
    fk("bug_annotation_id", "bug_annotation.id", nullable=True),
    Column("decision", String(24), nullable=False),
    fk("evidence_artifact_id", "artifact.id"),
    fk("adjudication_id", "adjudication.id", nullable=True),
    created_at(),
    CheckConstraint(
        "decision IN ('accepted','rejected','unverified','duplicate')", name="decision"
    ),
)

scorecard = Table(
    "scorecard",
    metadata,
    pk(),
    fk("evaluation_id", "evaluation.id"),
    Column("scorer_digest", String(71), nullable=False),
    Column("evidence_digest", String(71), nullable=False),
    fk("artifact_id", "artifact.id"),
    Column("gate", String(24), nullable=False),
    Column("composite", Numeric(12, 8), nullable=True),
    created_at(),
    UniqueConstraint("evaluation_id", "scorer_digest", "evidence_digest"),
    CheckConstraint("gate IN ('pass','fail','unknown','not_applicable')", name="gate"),
    CheckConstraint(
        "composite IS NULL OR (composite >= 0 AND composite <= 1)", name="composite_range"
    ),
)

score_item = Table(
    "score_item",
    metadata,
    pk(),
    fk("scorecard_id", "scorecard.id"),
    Column("dimension", String(64), nullable=False),
    Column("item_id", String(160), nullable=False),
    Column("status", String(32), nullable=False),
    Column("applicable", Boolean, nullable=False),
    Column("raw_value", Numeric(12, 8), nullable=True),
    Column("effective_weight", Numeric(12, 8), nullable=True),
    Column("contribution", Numeric(16, 10), nullable=True),
    Column("reason", Text, nullable=True),
    Column("evidence_refs", JSONB, nullable=False),
    created_at(),
    UniqueConstraint("scorecard_id", "dimension", "item_id"),
    CheckConstraint("raw_value IS NULL OR raw_value BETWEEN 0 AND 1", name="raw_value_range"),
    CheckConstraint(
        "effective_weight IS NULL OR effective_weight BETWEEN 0 AND 1", name="weight_range"
    ),
    CheckConstraint(
        "contribution IS NULL OR contribution BETWEEN 0 AND 1", name="contribution_range"
    ),
)

release = Table(
    "release",
    metadata,
    pk(),
    Column("slug", String(160), nullable=False),
    Column("version", Integer, nullable=False),
    Column("state", String(24), nullable=False),
    fk("policy_config_id", "config_document.id"),
    Column("membership_digest", String(71), nullable=True),
    Column("validation_digest", String(71), nullable=True),
    Column("approval_digest", String(71), nullable=True),
    fk("public_manifest_id", "artifact.id", nullable=True),
    fk("supersedes_id", "release.id", nullable=True),
    Column("withdrawal_reason", Text, nullable=True),
    Column("row_version", BigInteger, nullable=False, server_default=text("0")),
    created_at(),
    UniqueConstraint("slug", "version"),
    CheckConstraint("version > 0", name="version_positive"),
    CheckConstraint(
        "state IN ('draft','validating','review_required','approved','published','withdrawn')",
        name="state",
    ),
    CheckConstraint("row_version >= 0", name="row_version_nonnegative"),
)

release_entry = Table(
    "release_entry",
    metadata,
    Column(
        "release_id",
        Uuid(as_uuid=True),
        ForeignKey("release.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "model_config_id",
        Uuid(as_uuid=True),
        ForeignKey("config_document.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "scorecard_id",
        Uuid(as_uuid=True),
        ForeignKey("scorecard.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    PrimaryKeyConstraint("release_id", "model_config_id", "scorecard_id"),
)

publication_pointer = Table(
    "publication_pointer",
    metadata,
    pk(),
    Column("board_slug", String(160), nullable=False),
    fk("release_id", "release.id"),
    Column("generation", BigInteger, nullable=False, server_default=text("0")),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("board_slug"),
    CheckConstraint("generation >= 0", name="generation_nonnegative"),
)

# The API reads only these signed, allowlisted snapshots. Publication review state, validation
# receipts, approval records and source score tables remain outside the API's database role.
public_release_document = Table(
    "public_release_document",
    metadata,
    Column("release_id", String(120), primary_key=True),
    Column("document", JSONB, nullable=False),
    Column("content_digest", String(71), nullable=False),
    Column("state", String(24), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
    created_at(),
    CheckConstraint("state IN ('published','withdrawn')", name="state"),
    CheckConstraint("content_digest ~ '^sha256:[0-9a-f]{64}$'", name="content_digest_format"),
    CheckConstraint("jsonb_typeof(document) = 'object'", name="document_object"),
    CheckConstraint(
        "document ?& ARRAY['id','version','state','content','projection',"
        "'content_digest','manifest']",
        name="document_required_public_fields",
    ),
    CheckConstraint(
        "(document - ARRAY['id','slug','version','state','content','projection',"
        "'content_digest','manifest','withdrawal']) = '{}'::jsonb",
        name="document_public_allowlist",
    ),
    CheckConstraint(
        "NOT (document ?| ARRAY['validation','review','approval'])",
        name="document_no_private_workflow_fields",
    ),
    CheckConstraint("document->>'id' = release_id", name="document_identity"),
    CheckConstraint("document->>'state' = state", name="document_state"),
    CheckConstraint("document->>'content_digest' = content_digest", name="document_digest"),
    CheckConstraint(
        "jsonb_typeof(document->'version') = 'number' AND (document->>'version')::integer > 0",
        name="document_version_positive",
    ),
    CheckConstraint(
        "jsonb_typeof(document->'content') = 'object' AND "
        "jsonb_typeof(document->'projection') = 'object' AND "
        "jsonb_typeof(document->'manifest') = 'object'",
        name="document_public_payload_objects",
    ),
    CheckConstraint(
        "(state = 'published' AND NOT (document ? 'withdrawal')) OR "
        "(state = 'withdrawn' AND jsonb_typeof(document->'withdrawal') = 'object' AND "
        "(document->'withdrawal') ? 'reason' AND "
        "((document->'withdrawal') - ARRAY['reason','replacement_id']) = '{}'::jsonb AND "
        "COALESCE(NULLIF(btrim((document->'withdrawal')->>'reason'), ''), '') <> '')",
        name="document_withdrawal_allowlist",
    ),
)

public_release_pointer = Table(
    "public_release_pointer",
    metadata,
    Column("target", String(160), primary_key=True),
    Column("generation", BigInteger, nullable=False),
    Column(
        "release_id",
        String(120),
        ForeignKey("public_release_document.release_id", ondelete="RESTRICT"),
        nullable=True,
    ),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("generation >= 1", name="generation_positive"),
)

model_submission = Table(
    "model_submission",
    metadata,
    pk(),
    Column("requester_subject", String(255), nullable=False),
    fk("metadata_artifact_id", "artifact.id", nullable=True),
    Column("status", String(24), nullable=False),
    Column("request_document", JSONB, nullable=True),
    Column("reviewer_subject", String(255), nullable=True),
    Column("rejection_reason", Text, nullable=True),
    Column("approval_document", JSONB, nullable=True),
    Column("approval_digest", String(71), nullable=True),
    fk("resulting_run_id", "run.id", nullable=True),
    Column("row_version", BigInteger, nullable=False, server_default=text("0")),
    created_at(),
    CheckConstraint(
        "status IN ('pending','under_review','approved','rejected','withdrawn')", name="status"
    ),
    CheckConstraint("row_version >= 0", name="row_version_nonnegative"),
    CheckConstraint(
        "approval_digest IS NULL OR approval_digest ~ '^sha256:[0-9a-f]{64}$'",
        name="approval_digest_format",
    ),
    CheckConstraint(
        "metadata_artifact_id IS NOT NULL OR request_document IS NOT NULL",
        name="submission_content_present",
    ),
    Index("ix_model_submission_requester", "requester_subject", "created_at"),
)

idempotency_record = Table(
    "idempotency_record",
    metadata,
    pk(),
    Column("subject", String(255), nullable=False),
    Column("route", String(255), nullable=False),
    Column("key", String(255), nullable=False),
    Column("request_digest", String(71), nullable=False),
    Column("state", String(24), nullable=False),
    Column("response_code", Integer, nullable=True),
    Column("response_payload", JSONB, nullable=True),
    fk("response_artifact_id", "artifact.id", nullable=True),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    created_at(),
    UniqueConstraint("subject", "route", "key"),
    CheckConstraint("request_digest ~ '^sha256:[0-9a-f]{64}$'", name="request_digest_format"),
    CheckConstraint("state IN ('in_progress','completed')", name="state"),
    CheckConstraint(
        "(state = 'completed' AND response_code IS NOT NULL AND response_payload IS NOT NULL) OR "
        "state = 'in_progress'",
        name="response_shape",
    ),
)

audit_event = Table(
    "audit_event",
    metadata,
    pk(),
    Column("actor_subject", String(255), nullable=False),
    Column("action", String(160), nullable=False),
    Column("resource_type", String(96), nullable=False),
    Column("resource_id", String(255), nullable=False),
    Column("before_digest", String(71), nullable=True),
    Column("after_digest", String(71), nullable=True),
    Column("request_id", String(128), nullable=False),
    Column("details", JSONB, nullable=False),
    created_at(),
    Index("ix_audit_resource", "resource_type", "resource_id", "created_at"),
    Index("ix_audit_actor", "actor_subject", "created_at"),
)

subject_role = Table(
    "subject_role",
    metadata,
    Column("subject_id", String(255), nullable=False),
    Column("role", String(32), nullable=False),
    Column("granted_by", String(255), nullable=False),
    Column("row_version", BigInteger, nullable=False, server_default=text("0")),
    created_at("granted_at"),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    PrimaryKeyConstraint("subject_id", "role"),
    CheckConstraint(
        "role IN ('submitter','curator','operator','reviewer','publisher','administrator')",
        name="role",
    ),
    CheckConstraint("row_version >= 0", name="row_version_nonnegative"),
)

attempt_event = Table(
    "attempt_event",
    metadata,
    pk(),
    fk("attempt_id", "attempt.id"),
    Column("event_seq", BigInteger, nullable=False),
    Column("event_kind", String(64), nullable=False),
    fk("payload_artifact_id", "artifact.id"),
    created_at(),
    UniqueConstraint("attempt_id", "event_seq"),
    CheckConstraint("event_seq >= 0", name="event_seq_nonnegative"),
)

attempt_checkpoint = Table(
    "attempt_checkpoint",
    metadata,
    pk(),
    fk("attempt_id", "attempt.id"),
    Column("event_seq", BigInteger, nullable=False),
    fk("workspace_manifest_id", "artifact.id"),
    fk("transcript_manifest_id", "artifact.id"),
    Column("accumulated_budget", JSONB, nullable=False),
    Column("pending_call_ids", JSONB, nullable=False),
    Column("protocol_digest", String(71), nullable=False),
    Column("workspace_digest", String(71), nullable=False),
    Column("transcript_digest", String(71), nullable=False),
    Column("binding_digest", String(71), nullable=False),
    created_at(),
    UniqueConstraint("attempt_id", "event_seq"),
    CheckConstraint(
        "protocol_digest ~ '^sha256:[0-9a-f]{64}$' AND workspace_digest ~ '^sha256:[0-9a-f]{64}$' "
        "AND transcript_digest ~ '^sha256:[0-9a-f]{64}$' "
        "AND binding_digest ~ '^sha256:[0-9a-f]{64}$'",
        name="digest_format",
    ),
)

# The artifact/execution cycle is deferred until all referenced tables exist.
artifact.append_constraint(
    ForeignKeyConstraint(
        ["producer_execution_id"],
        ["stage_execution.id"],
        name="fk_artifact_producer_execution_id_stage_execution",
        ondelete="RESTRICT",
        use_alter=True,
    )
)

TABLE_GROUPS: dict[str, tuple[str, ...]] = {
    "core": (
        "task",
        "task_version",
        "task_set",
        "task_set_member",
        "config_document",
        "model_revision",
        "endpoint_registration",
    ),
    "runs": ("campaign", "run", "attempt", "candidate", "evaluation"),
    "jobs": (
        "stage_job",
        "stage_dependency",
        "stage_execution",
        "stage_job_event",
        "worker_registration",
        "capacity_slot",
    ),
    "artifacts": (
        "artifact",
        "artifact_quota",
        "artifact_upload",
        "artifact_retention_hold",
        "artifact_declassification",
        "artifact_projection_approval",
        "artifact_edge",
        "observation",
        "judge_packet",
        "judge_vote",
        "adjudication",
        "bug_annotation",
        "bug_match",
    ),
    "scoring_publication": (
        "scorecard",
        "score_item",
        "release",
        "release_entry",
        "publication_pointer",
        "model_submission",
    ),
    "accounting": (
        "budget_account",
        "budget_resource",
        "budget_reservation",
        "call_intent",
        "call_delivery",
        "usage_record",
        "accounting_entry",
    ),
    "identity_events": (
        "subject_role",
        "audit_event",
        "idempotency_record",
        "attempt_event",
        "attempt_checkpoint",
    ),
}

IMMUTABLE_TABLES = (
    "task_version",
    "config_document",
    "model_revision",
    "candidate",
    "observation",
    "judge_packet",
    "judge_vote",
    "adjudication",
    "bug_annotation",
    "bug_match",
    "scorecard",
    "score_item",
    "release_entry",
    "accounting_entry",
    "audit_event",
    "attempt_event",
    "attempt_checkpoint",
    "stage_dependency",
    "stage_job_event",
)
