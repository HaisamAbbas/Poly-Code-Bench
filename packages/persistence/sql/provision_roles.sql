-- Run once as a PostgreSQL database administrator before Alembic migrations.
-- These are non-login groups. Platform secret management creates login roles
-- and grants exactly one application group to each service credential.
DO $roles$
DECLARE
    role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY[
        'pcb_migrator', 'pcb_public_reader', 'pcb_submitter', 'pcb_curator',
        'pcb_operator', 'pcb_reviewer', 'pcb_publisher', 'pcb_scheduler',
        'pcb_solve_supervisor', 'pcb_evaluator', 'pcb_scorer', 'pcb_artifact_finalizer', 'pcb_model_gateway', 'pcb_administrator'
    ] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = role_name) THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN INHERIT', role_name);
        END IF;
    END LOOP;
END
$roles$;

GRANT pcb_curator, pcb_operator, pcb_reviewer, pcb_publisher, pcb_scheduler,
      pcb_solve_supervisor, pcb_evaluator, pcb_scorer TO pcb_administrator;
GRANT pcb_artifact_finalizer TO pcb_administrator;
GRANT USAGE, CREATE ON SCHEMA public TO pcb_migrator;
