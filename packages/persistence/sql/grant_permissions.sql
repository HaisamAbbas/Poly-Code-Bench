-- Run after migrations as the database administrator. No passwords or login
-- role names are stored here; attach managed login roles to one group role.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO pcb_migrator;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO pcb_migrator;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO pcb_migrator;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC;

-- Public readers can only see published projections, never source tables.
GRANT SELECT ON public_published_release, public_release_entry,
    public_release_document, public_release_pointer, public_artifact_catalog TO pcb_public_reader;

GRANT USAGE ON SCHEMA public TO
    pcb_public_reader, pcb_submitter, pcb_submission_reviewer, pcb_submission_approver,
    pcb_endpoint_administrator, pcb_curator, pcb_operator, pcb_reviewer,
    pcb_publisher, pcb_scheduler, pcb_solve_supervisor, pcb_evaluator,
    pcb_scorer, pcb_artifact_finalizer, pcb_administrator;
GRANT USAGE ON SCHEMA public TO pcb_solve_worker;

GRANT SELECT, INSERT ON model_submission TO pcb_submitter;
GRANT SELECT, INSERT, UPDATE, DELETE ON idempotency_record TO pcb_submitter;
GRANT INSERT ON audit_event TO pcb_submitter;
GRANT SELECT ON model_submission TO
    pcb_submission_reviewer, pcb_submission_approver, pcb_reviewer, pcb_administrator;
GRANT UPDATE (status, reviewer_subject, rejection_reason, row_version)
    ON model_submission TO pcb_submission_reviewer;
GRANT UPDATE (status, reviewer_subject, approval_document, approval_digest, resulting_run_id, row_version)
    ON model_submission TO pcb_submission_approver;
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
DROP POLICY IF EXISTS model_submission_public_review_scope ON model_submission;
CREATE POLICY model_submission_public_review_scope ON model_submission
    FOR ALL TO pcb_submission_reviewer, pcb_submission_approver
    USING (true) WITH CHECK (true);
GRANT INSERT ON audit_event TO pcb_submission_reviewer;

-- The public API's approval path has permission only to validate one bounded run plan,
-- create its queued run and attempts, and expose that run's lifecycle summary to its owner.
GRANT SELECT ON campaign, task_set, task_set_member, task_version, config_document,
    model_revision, endpoint_registration, run, attempt, budget_account
    TO pcb_submission_approver;
GRANT INSERT, SELECT ON run, attempt TO pcb_submission_approver;
GRANT SELECT ON stage_job TO pcb_submission_approver;
GRANT INSERT ON stage_job, stage_job_event TO pcb_submission_approver, pcb_operator;
GRANT SELECT, INSERT, UPDATE, DELETE ON idempotency_record TO pcb_submission_approver;
GRANT SELECT, INSERT ON budget_account, budget_resource TO pcb_submission_approver;
GRANT INSERT ON audit_event TO pcb_submission_approver;

-- MFA-gated endpoint administration is isolated from general database administration.
GRANT SELECT, INSERT, UPDATE ON endpoint_registration TO pcb_endpoint_administrator;
GRANT SELECT, INSERT, UPDATE, DELETE ON idempotency_record TO pcb_endpoint_administrator;
GRANT INSERT ON audit_event TO pcb_endpoint_administrator;

GRANT SELECT, INSERT, UPDATE ON task TO pcb_curator, pcb_administrator;
GRANT SELECT, INSERT ON task_version, task_set_member TO pcb_curator, pcb_administrator;
GRANT SELECT, INSERT, UPDATE ON task_set TO pcb_curator, pcb_administrator;

-- Run creation re-checks that the bound endpoint registration is still approved.
GRANT SELECT ON task_set, task_set_member, task_version, config_document, model_revision, campaign,
    endpoint_registration TO pcb_operator;
GRANT INSERT, SELECT ON run, attempt, idempotency_record TO pcb_operator;
GRANT UPDATE, DELETE ON idempotency_record TO pcb_operator;
GRANT SELECT, INSERT ON audit_document, benchmark_registry, benchmark_snapshot,
    benchmark_item, benchmark_import_manifest, benchmark_item_lineage,
    audit_component, fingerprint, corpus_source, corpus_snapshot,
    corpus_document, audit_run, audit_query, audit_checkpoint, match_candidate,
    match_review, risk_assessment, temporal_assessment TO pcb_operator, pcb_administrator;
GRANT UPDATE (state, row_version) ON audit_run TO pcb_operator;
GRANT SELECT ON artifact TO pcb_operator;
GRANT INSERT ON audit_event TO pcb_operator;

GRANT SELECT, INSERT, UPDATE ON stage_job, capacity_slot, worker_registration TO pcb_scheduler;
GRANT SELECT, INSERT ON stage_execution TO pcb_scheduler;
GRANT UPDATE (finished_at, result, failure_class, output_manifest_id) ON stage_execution TO pcb_scheduler;
GRANT SELECT, INSERT ON stage_dependency, stage_job_event TO pcb_scheduler;
GRANT SELECT ON attempt, run, campaign, evaluation, release, config_document, artifact TO pcb_scheduler;
GRANT INSERT ON evaluation TO pcb_scheduler;
GRANT UPDATE (status, row_version) ON run TO pcb_scheduler;
GRANT UPDATE (state, failure_class, row_version) ON attempt TO pcb_scheduler;
GRANT UPDATE (state, failure_class, gate, evidence_manifest_id, row_version)
    ON evaluation TO pcb_scheduler;
GRANT SELECT ON audit_run, curation_round, discovery_search TO pcb_scheduler;
GRANT UPDATE (state, current_stage, row_version) ON audit_run TO pcb_scheduler;
GRANT UPDATE (state, row_version) ON curation_round, discovery_search TO pcb_scheduler;
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
GRANT SELECT ON attempt, run TO pcb_scorer;
GRANT INSERT ON audit_event TO pcb_scorer;

GRANT SELECT ON evaluation, observation, judge_packet, judge_vote, artifact, scorecard, score_item, release TO pcb_reviewer;
GRANT INSERT, SELECT ON adjudication TO pcb_reviewer;
GRANT INSERT ON audit_event TO pcb_reviewer;

GRANT SELECT ON release, release_entry, scorecard, score_item, config_document TO pcb_publisher;
GRANT SELECT, INSERT, UPDATE ON release, release_entry, publication_pointer TO pcb_publisher;
GRANT SELECT, INSERT, UPDATE ON public_release_document, public_release_pointer TO pcb_publisher;
GRANT INSERT ON audit_event TO pcb_publisher;

GRANT SELECT ON ALL TABLES IN SCHEMA public TO pcb_administrator;
GRANT INSERT, UPDATE, DELETE ON task, task_set, campaign, endpoint_registration, model_submission, subject_role TO pcb_administrator;
GRANT INSERT ON audit_event TO pcb_administrator;

-- Model gateway: it reads approved endpoints/configs and writes only call accounting. It has
-- no access to secrets (only references), tasks, hidden bundles or scoring tables.
GRANT USAGE ON SCHEMA public TO pcb_model_gateway;
GRANT SELECT ON endpoint_registration, model_revision, config_document, attempt, run, campaign, evaluation, artifact, artifact_quota TO pcb_model_gateway;
GRANT SELECT, INSERT ON call_intent, call_delivery, usage_record, budget_reservation, accounting_entry TO pcb_model_gateway;
GRANT UPDATE (state) ON call_intent TO pcb_model_gateway;
GRANT UPDATE (status, responded_at, provider_request_id, failure_code, raw_response_artifact_id, normalized_response_artifact_id) ON call_delivery TO pcb_model_gateway;
GRANT UPDATE (state) ON budget_reservation TO pcb_model_gateway;
GRANT SELECT ON budget_account, budget_resource TO pcb_model_gateway;
GRANT UPDATE (spent_confirmed, reserved_open, uncertain_committed, row_version) ON budget_account, budget_resource TO pcb_model_gateway;
GRANT SELECT ON audit_run TO pcb_model_gateway;
GRANT INSERT ON audit_event TO pcb_model_gateway;
GRANT SELECT, INSERT ON budget_account, budget_resource TO pcb_operator, pcb_administrator;
GRANT SELECT ON call_intent, call_delivery, usage_record, budget_reservation, accounting_entry TO pcb_operator, pcb_reviewer;
GRANT SELECT ON audit_run, budget_account TO pcb_submission_approver;

-- Future objects created by the migration identity remain private by default.
ALTER DEFAULT PRIVILEGES FOR ROLE pcb_migrator IN SCHEMA public
    REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE pcb_migrator IN SCHEMA public
    REVOKE ALL ON SEQUENCES FROM PUBLIC;
