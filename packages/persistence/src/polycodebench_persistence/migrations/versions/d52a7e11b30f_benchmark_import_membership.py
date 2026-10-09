"""Preserve frozen external benchmark imports and their item-level completeness."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d52a7e11b30f"
down_revision: str | None = "c3a4e14f8b29"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id() -> sa.Column[UUID]:
    return sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False)


def _create_import_tables() -> None:
    op.create_table(
        "benchmark_import_manifest",
        _id(),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("source_member", sa.String(1024), nullable=False),
        sa.Column("revision", sa.String(64), nullable=False),
        sa.Column("variant", sa.String(24), nullable=False),
        sa.Column("importer_version", sa.String(64), nullable=False),
        sa.Column("parser_config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("parser_config_digest", sa.String(71), nullable=False),
        sa.Column("sample_seed", sa.String(20), nullable=False),
        sa.Column("selected_membership", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("membership_digest", sa.String(71), nullable=False),
        sa.Column("source_digest", sa.String(71), nullable=False),
        sa.Column("source_artifact_id", sa.Uuid(), nullable=False),
        sa.Column("source_visibility", sa.String(16), nullable=False),
        sa.Column("storage_visibility", sa.String(16), nullable=False),
        sa.Column("rights_state", sa.String(24), nullable=False),
        sa.Column("rights_evidence_digest", sa.String(71), nullable=False),
        sa.Column("rights_evidence_artifact_id", sa.Uuid(), nullable=False),
        sa.Column("result_state", sa.String(24), nullable=False),
        sa.Column("source_error_codes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("revision ~ '^[0-9a-f]{40}([0-9a-f]{24})?$'", name="revision_format"),
        sa.CheckConstraint(
            "parser_config_digest ~ '^sha256:[0-9a-f]{64}$'", name="parser_config_digest"
        ),
        sa.CheckConstraint("membership_digest ~ '^sha256:[0-9a-f]{64}$'", name="membership_digest"),
        sa.CheckConstraint("source_digest ~ '^sha256:[0-9a-f]{64}$'", name="source_digest"),
        sa.CheckConstraint(
            "rights_evidence_digest ~ '^sha256:[0-9a-f]{64}$'", name="rights_evidence_digest"
        ),
        sa.CheckConstraint(
            "variant IN ('official','original','sanitized','verified')", name="variant"
        ),
        sa.CheckConstraint("length(importer_version) > 0", name="importer_version_nonempty"),
        sa.CheckConstraint(
            "source_visibility IN ('public','restricted','private')", name="source_visibility"
        ),
        sa.CheckConstraint(
            "storage_visibility IN ('private','restricted')", name="storage_visibility"
        ),
        sa.CheckConstraint("rights_state = 'approved'", name="approved_rights_only"),
        sa.CheckConstraint("result_state IN ('complete','partial','blocked')", name="result_state"),
        sa.CheckConstraint("jsonb_typeof(parser_config) = 'object'", name="parser_config_object"),
        sa.CheckConstraint(
            "jsonb_typeof(selected_membership) = 'array' AND jsonb_array_length(selected_membership) = 100",
            name="selected_membership_100",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(source_error_codes) = 'array'", name="source_errors_array"
        ),
        sa.CheckConstraint("sample_seed ~ '^(0|[1-9][0-9]{0,19})$'", name="sample_seed_format"),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "membership_digest"],
            ["benchmark_snapshot.id", "benchmark_snapshot.membership_digest"],
            name="fk_benchmark_import_manifest_snapshot_membership",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_artifact_id"],
            ["artifact.id"],
            name="fk_benchmark_import_manifest_source_artifact_id_artifact",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rights_evidence_artifact_id"],
            ["artifact.id"],
            name="fk_benchmark_import_rights_artifact",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_benchmark_import_manifest"),
        sa.UniqueConstraint("snapshot_id", name="uq_benchmark_import_manifest_snapshot_id"),
    )

    op.create_table(
        "benchmark_item_lineage",
        _id(),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("parent_benchmark_slug", sa.String(96), nullable=False),
        sa.Column("parent_item_key", sa.String(255), nullable=False),
        sa.Column("relation", sa.String(24), nullable=False),
        sa.Column("evidence_digest", sa.String(71), nullable=False),
        sa.Column("evidence_artifact_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "relation IN ('variant_of','derived_from','translated_from')", name="relation"
        ),
        sa.CheckConstraint("evidence_digest ~ '^sha256:[0-9a-f]{64}$'", name="evidence_digest"),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["benchmark_item.id"],
            name="fk_benchmark_item_lineage_item_id_benchmark_item",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_artifact_id"],
            ["artifact.id"],
            name="fk_benchmark_item_lineage_evidence_artifact_id_artifact",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_benchmark_item_lineage"),
        sa.UniqueConstraint(
            "item_id",
            "parent_benchmark_slug",
            "parent_item_key",
            "relation",
            name="uq_benchmark_item_lineage_parent",
        ),
    )
    op.create_index(
        "ix_benchmark_item_lineage_parent",
        "benchmark_item_lineage",
        ["parent_benchmark_slug", "parent_item_key"],
    )


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_benchmark_snapshot_id_membership_digest",
        "benchmark_snapshot",
        ["id", "membership_digest"],
    )
    op.drop_constraint(op.f("ck_benchmark_item_source_digest"), "benchmark_item", type_="check")
    op.alter_column("benchmark_item", "task_version_id", existing_type=sa.Uuid(), nullable=True)
    op.alter_column("benchmark_item", "source_digest", existing_type=sa.String(71), nullable=True)
    op.create_check_constraint(
        "source_digest",
        "benchmark_item",
        "source_digest IS NULL OR source_digest ~ '^sha256:[0-9a-f]{64}$'",
    )
    op.add_column(
        "benchmark_item",
        sa.Column(
            "import_state",
            sa.String(24),
            server_default=sa.text("'legacy_unverified'"),
            nullable=False,
        ),
    )
    op.add_column(
        "benchmark_item",
        sa.Column(
            "error_codes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "benchmark_item",
        sa.Column("source_date_evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "benchmark_item",
        sa.Column(
            "source_urls",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column("benchmark_item", sa.Column("self_source_exposure", sa.Boolean(), nullable=True))
    op.add_column(
        "benchmark_item", sa.Column("source_public_exposure", sa.Boolean(), nullable=True)
    )
    op.add_column(
        "benchmark_item", sa.Column("independent_duplicate_eligible", sa.Boolean(), nullable=True)
    )
    op.create_check_constraint(
        "import_state",
        "benchmark_item",
        "import_state IN ('legacy_unverified','imported','incomplete','missing','blocked')",
    )
    op.create_check_constraint(
        "error_codes_array", "benchmark_item", "jsonb_typeof(error_codes) = 'array'"
    )
    op.create_check_constraint(
        "source_urls_array", "benchmark_item", "jsonb_typeof(source_urls) = 'array'"
    )
    op.create_check_constraint(
        "import_state_shape",
        "benchmark_item",
        "import_state = 'legacy_unverified' OR "
        "(import_state = 'imported' AND source_digest IS NOT NULL AND task_version_id IS NULL "
        "AND original_artifact_id IS NOT NULL "
        "AND jsonb_array_length(error_codes) = 0) OR "
        "(import_state = 'incomplete' AND source_digest IS NOT NULL AND task_version_id IS NULL "
        "AND original_artifact_id IS NOT NULL "
        "AND jsonb_array_length(error_codes) > 0) OR "
        "(import_state = 'missing' AND source_digest IS NULL AND task_version_id IS NULL "
        "AND original_artifact_id IS NULL "
        "AND jsonb_array_length(error_codes) > 0) OR "
        "(import_state = 'blocked' AND task_version_id IS NULL "
        "AND jsonb_array_length(error_codes) > 0)",
    )
    op.create_check_constraint(
        "exposure_fields_together",
        "benchmark_item",
        "(self_source_exposure IS NULL AND source_public_exposure IS NULL "
        "AND independent_duplicate_eligible IS NULL) OR "
        "(self_source_exposure IS NOT NULL AND source_public_exposure IS NOT NULL "
        "AND independent_duplicate_eligible IS NOT NULL)",
    )
    op.create_index(
        "ix_benchmark_item_import_membership_index",
        "benchmark_item",
        ["snapshot_id", "membership_index"],
        unique=True,
        postgresql_where=sa.text("import_state <> 'legacy_unverified'"),
    )
    _create_import_tables()

    op.execute(
        """
        CREATE FUNCTION pcb_validate_benchmark_import_membership() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            actual_ids text[];
            expected_ids text[];
            source_row record;
            rights_row record;
            snapshot_registry_id uuid;
        BEGIN
            SELECT array_agg(item_key ORDER BY membership_index)
            INTO actual_ids
            FROM benchmark_item
            WHERE snapshot_id = NEW.snapshot_id;
            SELECT ARRAY(SELECT jsonb_array_elements_text(NEW.selected_membership))
            INTO expected_ids;
            IF COALESCE(cardinality(actual_ids), 0) <> 100 OR actual_ids IS DISTINCT FROM expected_ids THEN
                RAISE EXCEPTION 'benchmark import rows do not match its frozen 100-ID membership';
            END IF;
            IF NEW.result_state = 'complete' AND EXISTS (
                SELECT 1 FROM benchmark_item WHERE snapshot_id = NEW.snapshot_id
                AND import_state <> 'imported'
            ) THEN
                RAISE EXCEPTION 'complete benchmark import contains non-imported membership';
            END IF;
            IF NEW.result_state = 'blocked' AND EXISTS (
                SELECT 1 FROM benchmark_item WHERE snapshot_id = NEW.snapshot_id
                AND import_state <> 'blocked'
            ) THEN
                RAISE EXCEPTION 'blocked benchmark import has a non-blocked membership item';
            END IF;
            IF NEW.result_state = 'blocked' AND jsonb_array_length(NEW.source_error_codes) = 0 THEN
                RAISE EXCEPTION 'blocked benchmark import requires a source error code';
            END IF;
            IF NEW.result_state = 'partial'
            AND jsonb_array_length(NEW.source_error_codes) = 0
            AND NOT EXISTS (
                SELECT 1 FROM benchmark_item WHERE snapshot_id = NEW.snapshot_id
                AND import_state <> 'imported'
            ) THEN
                RAISE EXCEPTION 'partial benchmark import has no recorded incompleteness';
            END IF;
            IF EXISTS (
                SELECT 1 FROM benchmark_item AS i
                WHERE i.snapshot_id = NEW.snapshot_id AND i.import_state = 'imported'
                AND NOT EXISTS (SELECT 1 FROM audit_component AS c WHERE c.item_id = i.id)
            ) THEN
                RAISE EXCEPTION 'imported benchmark item has no parsed component records';
            END IF;
            SELECT s.registry_id INTO snapshot_registry_id
            FROM benchmark_snapshot AS s WHERE s.id = NEW.snapshot_id;
            IF NOT EXISTS (
                SELECT 1 FROM benchmark_registry AS r WHERE r.id = snapshot_registry_id
                AND r.status IN ('importable','audit_conformant')
            ) THEN
                RAISE EXCEPTION 'benchmark registry has not cleared its import rights gate';
            END IF;
            SELECT content_digest, visibility, status INTO source_row
            FROM artifact WHERE id = NEW.source_artifact_id;
            IF source_row.status <> 'verified' OR source_row.content_digest <> NEW.source_digest
            OR source_row.visibility <> (CASE NEW.storage_visibility
                WHEN 'private' THEN 'hidden' ELSE 'internal' END) THEN
                RAISE EXCEPTION 'benchmark source artifact is not verified against its manifest';
            END IF;
            SELECT content_digest, visibility, status INTO rights_row
            FROM artifact WHERE id = NEW.rights_evidence_artifact_id;
            IF rights_row.status <> 'verified' OR rights_row.content_digest <> NEW.rights_evidence_digest
            OR rights_row.visibility = 'public' THEN
                RAISE EXCEPTION 'benchmark rights evidence is missing or publicly visible';
            END IF;
            IF EXISTS (
                SELECT 1 FROM benchmark_item AS i
                LEFT JOIN artifact AS a ON a.id = i.original_artifact_id
                WHERE i.snapshot_id = NEW.snapshot_id AND (
                    i.self_source_exposure IS DISTINCT FROM TRUE OR
                    i.source_public_exposure IS DISTINCT FROM (NEW.source_visibility = 'public') OR
                    i.independent_duplicate_eligible IS DISTINCT FROM FALSE OR
                    (i.original_artifact_id IS NOT NULL AND (
                        a.status <> 'verified' OR a.content_digest <> i.source_digest OR
                        a.visibility <> source_row.visibility
                    ))
                )
            ) THEN
                RAISE EXCEPTION 'benchmark item artifact or self-source exposure is inconsistent';
            END IF;
            IF EXISTS (
                SELECT 1 FROM audit_component AS c
                JOIN benchmark_item AS i ON i.id = c.item_id
                LEFT JOIN artifact AS a ON a.id = c.content_artifact_id
                WHERE i.snapshot_id = NEW.snapshot_id AND (
                    c.content_artifact_id IS NULL OR a.status <> 'verified' OR
                    a.content_digest <> c.component_digest OR a.visibility <> source_row.visibility OR
                    c.visibility <> NEW.storage_visibility
                )
            ) THEN
                RAISE EXCEPTION 'benchmark component artifact is not verified against its digest';
            END IF;
            IF EXISTS (
                SELECT 1 FROM benchmark_item_lineage AS l
                LEFT JOIN artifact AS a ON a.id = l.evidence_artifact_id
                JOIN benchmark_item AS i ON i.id = l.item_id
                WHERE i.snapshot_id = NEW.snapshot_id AND (
                    a.status <> 'verified' OR a.content_digest <> l.evidence_digest
                )
            ) THEN
                RAISE EXCEPTION 'benchmark lineage evidence artifact is not verified';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER benchmark_import_membership_guard "
        "AFTER INSERT ON benchmark_import_manifest DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION pcb_validate_benchmark_import_membership()"
    )

    # benchmark_snapshot, benchmark_item and audit_component are already immutable (c3a4e14f8b29).
    for table_name in ("benchmark_import_manifest", "benchmark_item_lineage"):
        op.execute(
            f"CREATE TRIGGER immutable_{table_name} BEFORE UPDATE OR DELETE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION pcb_reject_immutable_change()"
        )

    op.execute(
        "GRANT SELECT, INSERT ON benchmark_import_manifest, benchmark_item_lineage "
        "TO pcb_operator, pcb_administrator"
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        raise RuntimeError("benchmark import downgrade requires an online PostgreSQL safety check")
    has_import_data = connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM benchmark_import_manifest) OR "
            "EXISTS (SELECT 1 FROM benchmark_item WHERE task_version_id IS NULL "
            "OR source_digest IS NULL OR import_state <> 'legacy_unverified' "
            "OR self_source_exposure IS NOT NULL OR source_public_exposure IS NOT NULL "
            "OR independent_duplicate_eligible IS NOT NULL) OR "
            "EXISTS (SELECT 1 FROM benchmark_item_lineage)"
        )
    ).scalar_one()
    if has_import_data:
        raise RuntimeError("benchmark import rows exist; export and quiesce them before downgrade")

    op.execute(
        "REVOKE SELECT, INSERT ON benchmark_import_manifest, benchmark_item_lineage FROM pcb_operator, pcb_administrator"
    )
    for table_name in ("benchmark_item_lineage", "benchmark_import_manifest"):
        op.execute(f"DROP TRIGGER IF EXISTS immutable_{table_name} ON {table_name}")
    op.drop_index("ix_benchmark_item_lineage_parent", table_name="benchmark_item_lineage")
    op.drop_table("benchmark_item_lineage")
    op.drop_table("benchmark_import_manifest")
    op.execute("DROP FUNCTION pcb_validate_benchmark_import_membership()")
    op.drop_constraint(
        op.f("uq_benchmark_snapshot_id_membership_digest"),
        "benchmark_snapshot",
        type_="unique",
    )
    op.drop_index("ix_benchmark_item_import_membership_index", table_name="benchmark_item")
    for constraint in (
        "ck_benchmark_item_exposure_fields_together",
        "ck_benchmark_item_import_state_shape",
        "ck_benchmark_item_error_codes_array",
        "ck_benchmark_item_source_urls_array",
        "ck_benchmark_item_import_state",
        "ck_benchmark_item_source_digest",
    ):
        op.drop_constraint(op.f(constraint), "benchmark_item", type_="check")
    for column in (
        "independent_duplicate_eligible",
        "source_public_exposure",
        "self_source_exposure",
        "source_urls",
        "source_date_evidence",
        "error_codes",
        "import_state",
    ):
        op.drop_column("benchmark_item", column)
    op.alter_column("benchmark_item", "task_version_id", existing_type=sa.Uuid(), nullable=False)
    op.alter_column("benchmark_item", "source_digest", existing_type=sa.String(71), nullable=False)
    op.create_check_constraint(
        "source_digest", "benchmark_item", "source_digest ~ '^sha256:[0-9a-f]{64}$'"
    )
