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

## PostgreSQL integration tests

Run tests only against a dedicated database whose name contains `test`; the test fixture refuses other database names. Apply the migration and grants first, then:

```powershell
$env:PCB_TEST_DATABASE_URL = '<managed dedicated test connection URL>'
uv run --locked pytest -q -p no:cacheprovider tests/test_persistence_postgres.py
```

The CI workflow provisions a disposable PostgreSQL 17.6 service and runs role setup, migration, schema-drift check, and these tests. Local integration evidence is environment-specific and does not claim that every application/API permission route is implemented or tested.
