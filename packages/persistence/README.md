# PostgreSQL persistence

`polycodebench_persistence.models.metadata` is the authoritative relational model. Versioned Alembic revisions ship in the Python package under `src/polycodebench_persistence/migrations/`.

## Provision and migrate

Use PostgreSQL 17.6, matching the repository's immutable development image reference. The database must be dedicated to PolyCodeBench. Supply a migration URL through the secret manager or local process environment; do not put credentials in checked-in files or command output.

```powershell
$env:PCB_MIGRATION_DATABASE_URL = '<managed migration connection URL>'
psql $env:PCB_MIGRATION_DATABASE_URL -v ON_ERROR_STOP=1 -f packages/persistence/sql/provision_roles.sql
uv run --locked alembic -c packages/persistence/alembic.ini upgrade head
psql $env:PCB_MIGRATION_DATABASE_URL -v ON_ERROR_STOP=1 -f packages/persistence/sql/grant_permissions.sql
uv run --locked alembic -c packages/persistence/alembic.ini check
```

The schema provisions non-login role groups. Deployment identity management attaches service-specific login identities to the narrow role group and supplies each service its own managed connection credential. The migration principal may create/alter schema objects; application principals do not. The administrator role inherits operational grants but receives no superuser or schema-creation privilege. No database password is stored in this repository.

The initial revision handles the artifact/execution foreign-key cycle by creating both tables and adding the cyclic edge afterward. Its downgrade intentionally refuses to drop provenance and evidence tables. Use a reviewed backup/retention plan for any destructive schema retirement.

## Artifact intake and visibility

`ArtifactRepository` reserves quota and creates an upload identity in PostgreSQL before the S3-compatible provisional object is written. Finalization reads the bounded staged object through an independent size/SHA-256 check, conditionally writes the content-addressed canonical object, reads that object back and verifies it again, then commits the immutable artifact record and upload state in one database transaction. A failed database commit is resumable using the same upload identity. The S3 ETag is never used as a content digest. A referenced artifact starts as `provisional` and can transition only to `verified` or `quarantined` under the database trigger.

Configure three distinct bucket names for hidden, internal and public artifacts. The adapter selects a bucket from the validated visibility and builds object keys itself; caller supplied URLs and storage keys are not download identifiers. Deduplication is unique within visibility and encryption domain. `ArtifactAccessService` checks the principal on each artifact UUID read and returns an inert attachment response. Solve identities have no database grant to enumerate `artifact`; the service additionally refuses hidden reads and requires an explicit artifact scope for internal reads. Artifact manifest edges must connect verified objects within one visibility/encryption domain and cannot form cycles.

The current public export flow first calls `PublicProjectionService.approve_metadata` with a reviewer principal. This stores an immutable approval for the exact canonical metadata digest and source artifact. A distinct publisher then calls `publish_reviewed_metadata` with that approval ID and the same projection. The public artifact reference and declassification record commit in one database transaction. Public downloads require both a verified artifact and a matching stored approval/declassification record; a raw public upload is not downloadable. Failed publication can leave an unreferenced canonical object for the 30-day orphan collector, but cannot leave a published database artifact. The source object's visibility, storage key and ACL do not change. Public API routes and their comprehensive privacy probes are later work and remain pending.

`ArtifactRepository.collect_garbage` expires abandoned reservations and releases their reserved quota at the upload deadline. It retains provisional staging bytes for the Technical Spec's default 30 days after expiry, then removes those provisional keys. It also removes unreferenced canonical objects after 30 days, which covers a crash between object write and database commit. Any canonical key present in the artifact table is retained, including referenced, published and held records. Failed object deletion is retried on a later collection pass.

### Local object-store integration

The Compose SeaweedFS service and its static credentials are development-only. Integration tests create three separate local buckets (`pcb-p04-hidden`, `pcb-p04-internal`, and `pcb-p04-public`) and exercise actual S3 requests. This does **not** validate production bucket policies, role separation, KMS/encryption configuration, lifecycle rules, or IAM denial behavior. Before production, provision visibility-scoped identities and verify them against the selected production object store with independent role-denial tests; do not treat local emulation as that evidence. Never call object ACL mutation APIs to publish an artifact.

## PostgreSQL integration tests

Run tests only against a dedicated database whose name contains `test`; the test fixture refuses other database names. Apply the migration and grants first, then:

```powershell
$env:PCB_TEST_DATABASE_URL = '<managed dedicated test connection URL>'
uv run --locked pytest -q -p no:cacheprovider tests/test_persistence_postgres.py
```

The CI workflow provisions a disposable PostgreSQL 17.6 service and runs role setup, migration, schema-drift check, and these tests. Local integration evidence is environment-specific and does not claim that every application/API permission route is implemented or tested.
