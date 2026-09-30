-- Run after migrations as the database administrator. No passwords or login
-- role names are stored here; attach managed login roles to one group role.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO pcb_migrator;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO pcb_migrator;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO pcb_migrator;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC;

-- Public readers can only see published projections, never source tables.
GRANT SELECT ON public_published_release, public_release_entry TO pcb_public_reader;

GRANT USAGE ON SCHEMA public TO
    pcb_public_reader, pcb_submitter, pcb_curator, pcb_operator, pcb_reviewer,
    pcb_publisher, pcb_scheduler, pcb_solve_supervisor, pcb_evaluator,
    pcb_scorer, pcb_artifact_finalizer, pcb_administrator;

GRANT SELECT, INSERT ON model_submission TO pcb_submitter;
GRANT SELECT ON model_submission TO pcb_reviewer, pcb_administrator;
GRANT UPDATE (status, reviewer_subject, rejection_reason, resulting_run_id, row_version)
    ON model_submission TO pcb_reviewer, pcb_administrator;
ALTER TABLE model_submission ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_submission FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS model_submission_submitter_scope ON model_submission;
CREATE POLICY model_submission_submitter_scope ON model_submission
    FOR ALL TO pcb_submitter
    USING (requester_subject = current_setting('pcb.subject_id', true))
    WITH CHECK (requester_subject = current_setting('pcb.subject_id', true));
DROP POLICY IF EXISTS model_submission_reviewer_scope ON model_submission;
CREATE POLICY model_submission_reviewer_scope ON model_submission
    FOR ALL TO pcb_reviewer, pcb_administrator USING (true) WITH CHECK (true);

GRANT SELECT, INSERT, UPDATE ON task TO pcb_curator, pcb_administrator;
GRANT SELECT, INSERT ON task_version, task_set_member TO pcb_curator, pcb_administrator;
GRANT SELECT, INSERT, UPDATE ON task_set TO pcb_curator, pcb_administrator;

GRANT SELECT ON task_set, task_set_member, task_version, config_document, model_revision, campaign TO pcb_operator;
GRANT INSERT, SELECT ON run, attempt, idempotency_record TO pcb_operator;
GRANT UPDATE ON idempotency_record TO pcb_operator;
GRANT INSERT ON audit_event TO pcb_operator;

GRANT SELECT, INSERT, UPDATE ON stage_job, capacity_slot, worker_registration TO pcb_scheduler;
GRANT SELECT, INSERT ON stage_execution TO pcb_scheduler;
GRANT UPDATE (finished_at, result, failure_class, output_manifest_id) ON stage_execution TO pcb_scheduler;
GRANT SELECT, INSERT ON stage_dependency, stage_job_event TO pcb_scheduler;
GRANT SELECT ON attempt, run, campaign, evaluation, release, config_document, artifact TO pcb_scheduler;
GRANT UPDATE (state, failure_class, row_version) ON attempt TO pcb_scheduler;
GRANT UPDATE (state, failure_class, row_version) ON evaluation TO pcb_scheduler;
GRANT INSERT ON audit_event TO pcb_scheduler;

GRANT SELECT ON run, task_version, task_set, task_set_member, config_document, model_revision TO pcb_solve_supervisor;
GRANT SELECT, UPDATE ON attempt TO pcb_solve_supervisor;
GRANT INSERT, SELECT ON candidate, attempt_event, attempt_checkpoint TO pcb_solve_supervisor;
GRANT INSERT ON audit_event TO pcb_solve_supervisor;

GRANT SELECT ON attempt, task_version, task_set, task_set_member, config_document, candidate, artifact, stage_execution TO pcb_evaluator;
GRANT SELECT, INSERT, UPDATE ON evaluation TO pcb_evaluator;
GRANT INSERT, SELECT ON observation, judge_packet, call_intent, call_delivery, usage_record, adjudication, bug_match TO pcb_evaluator;
GRANT INSERT ON audit_event TO pcb_evaluator;

-- The finalizer is a dedicated service identity. Object-store IAM remains an
-- independently provisioned, visibility-scoped control outside PostgreSQL.
GRANT SELECT, INSERT ON artifact, artifact_upload TO pcb_artifact_finalizer;
GRANT UPDATE (status) ON artifact TO pcb_artifact_finalizer;
GRANT UPDATE (state, failure_code, artifact_id) ON artifact_upload TO pcb_artifact_finalizer;
GRANT SELECT ON artifact_quota TO pcb_artifact_finalizer;
GRANT UPDATE (used_bytes, reserved_bytes, row_version) ON artifact_quota TO pcb_artifact_finalizer;
GRANT SELECT, INSERT ON artifact_edge TO pcb_artifact_finalizer;
GRANT SELECT ON artifact_projection_approval TO pcb_artifact_finalizer;
GRANT SELECT, INSERT ON artifact_declassification TO pcb_artifact_finalizer;
GRANT INSERT ON audit_event TO pcb_artifact_finalizer;

GRANT SELECT, INSERT ON artifact_retention_hold TO pcb_reviewer, pcb_administrator;
GRANT UPDATE (released_by, released_at) ON artifact_retention_hold TO pcb_reviewer, pcb_administrator;
GRANT SELECT ON artifact_declassification TO pcb_reviewer, pcb_publisher, pcb_administrator;
GRANT INSERT ON artifact_declassification TO pcb_publisher, pcb_administrator;
GRANT SELECT ON artifact_projection_approval TO pcb_reviewer, pcb_publisher, pcb_administrator;
GRANT INSERT ON artifact_projection_approval TO pcb_reviewer, pcb_administrator;
GRANT INSERT ON artifact TO pcb_publisher, pcb_administrator;
GRANT UPDATE (status) ON artifact TO pcb_publisher, pcb_administrator;
GRANT SELECT, INSERT ON artifact_quota TO pcb_publisher, pcb_administrator;
GRANT UPDATE (used_bytes, reserved_bytes, row_version) ON artifact_quota TO pcb_publisher, pcb_administrator;
GRANT INSERT ON audit_event TO pcb_reviewer, pcb_publisher;

GRANT SELECT ON evaluation, observation, candidate, task_version, config_document TO pcb_scorer;
GRANT SELECT, INSERT ON scorecard, score_item TO pcb_scorer;
GRANT INSERT ON audit_event TO pcb_scorer;

GRANT SELECT ON evaluation, observation, judge_packet, judge_vote, artifact, scorecard, score_item, release TO pcb_reviewer;
GRANT INSERT, SELECT ON adjudication TO pcb_reviewer;
GRANT INSERT ON audit_event TO pcb_reviewer;

GRANT SELECT ON release, release_entry, scorecard, score_item, config_document TO pcb_publisher;
GRANT SELECT, INSERT, UPDATE ON release, release_entry, publication_pointer TO pcb_publisher;
GRANT INSERT ON audit_event TO pcb_publisher;

GRANT SELECT ON ALL TABLES IN SCHEMA public TO pcb_administrator;
GRANT INSERT, UPDATE, DELETE ON task, task_set, campaign, endpoint_registration, model_submission, subject_role TO pcb_administrator;
GRANT INSERT ON audit_event TO pcb_administrator;

-- Future objects created by the migration identity remain private by default.
ALTER DEFAULT PRIVILEGES FOR ROLE pcb_migrator IN SCHEMA public
    REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE pcb_migrator IN SCHEMA public
    REVOKE ALL ON SEQUENCES FROM PUBLIC;
