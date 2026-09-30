"""Artifact upload lifecycle, quota, retention and declassification records."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a4f04c4f5a12"
down_revision: str | None = "5c9545180d80"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE artifact_quota (
        visibility varchar(16) NOT NULL,
        encryption_domain varchar(128) NOT NULL,
        max_bytes bigint NOT NULL,
        used_bytes bigint NOT NULL DEFAULT 0,
        reserved_bytes bigint NOT NULL DEFAULT 0,
        row_version bigint NOT NULL DEFAULT 0,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT pk_artifact_quota PRIMARY KEY (visibility, encryption_domain),
        CONSTRAINT ck_artifact_quota_visibility CHECK (visibility IN ('public','internal','hidden')),
        CONSTRAINT ck_artifact_quota_max_bytes_nonnegative CHECK (max_bytes >= 0),
        CONSTRAINT ck_artifact_quota_usage_nonnegative CHECK (used_bytes >= 0 AND reserved_bytes >= 0),
        CONSTRAINT ck_artifact_quota_within_quota CHECK (used_bytes + reserved_bytes <= max_bytes),
        CONSTRAINT ck_artifact_quota_row_version_nonnegative CHECK (row_version >= 0)
    );
    CREATE TABLE artifact_upload (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_subject varchar(255) NOT NULL,
        visibility varchar(16) NOT NULL,
        encryption_domain varchar(128) NOT NULL,
        expected_digest varchar(71) NOT NULL,
        expected_size_bytes bigint NOT NULL,
        media_type varchar(255) NOT NULL,
        provisional_key text NOT NULL,
        state varchar(24) NOT NULL,
        failure_code varchar(64),
        artifact_id uuid REFERENCES artifact(id) ON DELETE RESTRICT,
        expires_at timestamptz NOT NULL,
        garbage_collect_after timestamptz NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT uq_artifact_upload_provisional_key UNIQUE (provisional_key),
        CONSTRAINT ck_artifact_upload_visibility CHECK (visibility IN ('public','internal','hidden')),
        CONSTRAINT ck_artifact_upload_digest_format CHECK (expected_digest ~ '^sha256:[0-9a-f]{64}$'),
        CONSTRAINT ck_artifact_upload_size_nonnegative CHECK (expected_size_bytes >= 0),
        CONSTRAINT ck_artifact_upload_state CHECK (state IN ('reserved','uploaded','finalizing','verified','rejected','expired')),
        CONSTRAINT ck_artifact_upload_artifact_link_state CHECK ((state = 'verified' AND artifact_id IS NOT NULL) OR (state <> 'verified' AND artifact_id IS NULL)),
        CONSTRAINT ck_artifact_upload_gc_after_expiry CHECK (garbage_collect_after >= expires_at)
    );
    CREATE INDEX ix_artifact_upload_expiry ON artifact_upload(state, expires_at);
    CREATE INDEX ix_artifact_upload_gc ON artifact_upload(state, garbage_collect_after);
    CREATE TABLE artifact_retention_hold (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        artifact_id uuid NOT NULL REFERENCES artifact(id) ON DELETE RESTRICT,
        reason text NOT NULL,
        held_by varchar(255) NOT NULL,
        held_at timestamptz NOT NULL DEFAULT now(),
        released_by varchar(255),
        released_at timestamptz,
        CONSTRAINT ck_artifact_retention_hold_reason_nonempty CHECK (length(trim(reason)) > 0),
        CONSTRAINT ck_artifact_retention_hold_release_shape CHECK ((released_by IS NULL AND released_at IS NULL) OR (released_by IS NOT NULL AND released_at IS NOT NULL))
    );
    CREATE INDEX ix_artifact_hold_active ON artifact_retention_hold(artifact_id) WHERE released_at IS NULL;
    CREATE TABLE artifact_declassification (
        source_artifact_id uuid PRIMARY KEY REFERENCES artifact(id) ON DELETE RESTRICT,
        public_artifact_id uuid NOT NULL REFERENCES artifact(id) ON DELETE RESTRICT,
        review_digest varchar(71) NOT NULL,
        approved_by varchar(255) NOT NULL,
        reason text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT ck_artifact_declassification_distinct_artifacts CHECK (source_artifact_id <> public_artifact_id),
        CONSTRAINT ck_artifact_declassification_review_digest_format CHECK (review_digest ~ '^sha256:[0-9a-f]{64}$'),
        CONSTRAINT ck_artifact_declassification_reason_nonempty CHECK (length(trim(reason)) > 0)
    );

    CREATE FUNCTION pcb_guard_artifact_upload_change() RETURNS trigger AS $$
    BEGIN
      IF TG_OP = 'DELETE' THEN RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'artifact upload rows cannot be deleted'; END IF;
      IF ROW(NEW.owner_subject, NEW.visibility, NEW.encryption_domain, NEW.expected_digest,
             NEW.expected_size_bytes, NEW.media_type, NEW.provisional_key, NEW.expires_at,
             NEW.garbage_collect_after, NEW.created_at)
         IS DISTINCT FROM ROW(OLD.owner_subject, OLD.visibility, OLD.encryption_domain, OLD.expected_digest,
             OLD.expected_size_bytes, OLD.media_type, OLD.provisional_key, OLD.expires_at,
             OLD.garbage_collect_after, OLD.created_at)
      THEN RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'artifact upload identity is immutable'; END IF;
      IF NOT ((OLD.state = 'reserved' AND NEW.state IN ('uploaded','rejected','expired')) OR
              (OLD.state = 'uploaded' AND NEW.state IN ('finalizing','rejected','expired')) OR
              (OLD.state = 'finalizing' AND NEW.state IN ('verified','rejected','uploaded','expired')) OR
              (OLD.state = NEW.state AND OLD.state IN ('verified','rejected','expired'))) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'invalid artifact upload transition';
      END IF;
      IF OLD.state IN ('verified','rejected','expired') AND
         ROW(NEW.state, NEW.failure_code, NEW.artifact_id) IS DISTINCT FROM
         ROW(OLD.state, OLD.failure_code, OLD.artifact_id)
      THEN RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'terminal artifact upload is immutable'; END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql;
    CREATE TRIGGER artifact_upload_state_guard BEFORE UPDATE OR DELETE ON artifact_upload
      FOR EACH ROW EXECUTE FUNCTION pcb_guard_artifact_upload_change();

    CREATE FUNCTION pcb_guard_artifact_edge_scope() RETURNS trigger AS $$
    DECLARE p artifact%ROWTYPE; c artifact%ROWTYPE; cycle_found boolean;
    BEGIN
      SELECT * INTO p FROM artifact WHERE id = NEW.parent_artifact_id FOR SHARE;
      SELECT * INTO c FROM artifact WHERE id = NEW.child_artifact_id FOR SHARE;
      IF p.id IS NULL OR c.id IS NULL OR p.status <> 'verified' OR c.status <> 'verified' OR
         p.visibility <> c.visibility OR p.encryption_domain <> c.encryption_domain THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'artifact edge requires verified artifacts in one visibility domain';
      END IF;
      -- Serialize edge writes globally so concurrent opposite edges cannot both pass.
      PERFORM pg_advisory_xact_lock(4839201);
      WITH RECURSIVE descendants(id) AS (
        SELECT child_artifact_id FROM artifact_edge WHERE parent_artifact_id = NEW.child_artifact_id
        UNION
        SELECT e.child_artifact_id FROM artifact_edge e JOIN descendants d ON e.parent_artifact_id = d.id
      ) SELECT EXISTS (SELECT 1 FROM descendants WHERE id = NEW.parent_artifact_id) INTO cycle_found;
      IF cycle_found THEN RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'artifact edge would create a cycle'; END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql;
    CREATE TRIGGER artifact_edge_scope_guard BEFORE INSERT ON artifact_edge
      FOR EACH ROW EXECUTE FUNCTION pcb_guard_artifact_edge_scope();

    CREATE FUNCTION pcb_guard_artifact_declassification() RETURNS trigger AS $$
    DECLARE src artifact%ROWTYPE; dst artifact%ROWTYPE;
    BEGIN
      SELECT * INTO src FROM artifact WHERE id = NEW.source_artifact_id;
      SELECT * INTO dst FROM artifact WHERE id = NEW.public_artifact_id;
      IF src.status <> 'verified' OR src.visibility = 'public' OR dst.status <> 'verified' OR dst.visibility <> 'public' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'declassification requires verified non-public source and verified public projection';
      END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql;
    CREATE TRIGGER artifact_declassification_scope_guard BEFORE INSERT ON artifact_declassification
      FOR EACH ROW EXECUTE FUNCTION pcb_guard_artifact_declassification();
    CREATE TRIGGER artifact_declassification_immutable BEFORE UPDATE OR DELETE ON artifact_declassification
      FOR EACH ROW EXECUTE FUNCTION pcb_reject_immutable_change();
    CREATE TRIGGER artifact_hold_immutable BEFORE DELETE ON artifact_retention_hold
      FOR EACH ROW EXECUTE FUNCTION pcb_reject_immutable_change();
    CREATE FUNCTION pcb_guard_artifact_hold_change() RETURNS trigger AS $$
    BEGIN
      IF OLD.released_at IS NOT NULL OR NEW.released_at IS NULL OR
         ROW(NEW.artifact_id, NEW.reason, NEW.held_by, NEW.held_at, NEW.id) IS DISTINCT FROM
         ROW(OLD.artifact_id, OLD.reason, OLD.held_by, OLD.held_at, OLD.id) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'retention hold may only be released once';
      END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql;
    CREATE TRIGGER artifact_hold_release_guard BEFORE UPDATE ON artifact_retention_hold
      FOR EACH ROW EXECUTE FUNCTION pcb_guard_artifact_hold_change();
    """)


def downgrade() -> None:
    raise NotImplementedError(
        "Artifact integrity and retention records must be preserved; use a reviewed retention plan."
    )
