"use client";

import { useRef, useState } from "react";
import type { FormEvent } from "react";
import {
  loadModelSubmissionStatus,
  sendModelSubmission,
  type ModelSubmissionRequest,
  type ModelSubmissionStatus,
  type Resource,
} from "@/lib/public-api";

const EMPTY_FORM = {
  model_name: "",
  provider: "",
  organization: "",
  endpoint_url: "",
  source_url: "",
  source_license: "",
  permission_attested: false,
};

export function ModelSubmissionForm({
  identity,
  loginAvailable,
}: {
  readonly identity: { readonly email: string } | null;
  readonly loginAvailable: boolean;
}) {
  const [form, setForm] = useState(EMPTY_FORM);
  const [submissionId, setSubmissionId] = useState("");
  const [result, setResult] = useState<ModelSubmissionStatus | null>(null);
  const [submissionState, setSubmissionState] = useState<Resource<ModelSubmissionStatus> | null>(null);
  const [statusState, setStatusState] = useState<Resource<ModelSubmissionStatus> | null>(null);
  const [sending, setSending] = useState(false);
  const [checking, setChecking] = useState(false);
  const idempotencyKey = useRef<string | null>(null);

  function update<K extends keyof typeof EMPTY_FORM>(field: K, value: (typeof EMPTY_FORM)[K]) {
    if (!sending) idempotencyKey.current = null;
    setForm((current) => ({ ...current, [field]: value }));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (sending || result || !identity) return;
    setSending(true);
    setSubmissionState(null);
    idempotencyKey.current ??= crypto.randomUUID();
    const payload: ModelSubmissionRequest = {
      kind: "model_submission_input",
      model_name: form.model_name.trim(),
      provider: form.provider.trim(),
      ...(form.organization.trim() ? { organization: form.organization.trim() } : {}),
      contact_email: identity.email,
      endpoint_url: form.endpoint_url.trim(),
      source_url: form.source_url.trim(),
      source_license: form.source_license.trim(),
      permission_attested: form.permission_attested,
    };
    const response = await sendModelSubmission(idempotencyKey.current, payload);
    if (response.state === "ready") {
      const accepted = response.value.data;
      setResult(accepted);
      setSubmissionId(accepted.submission_id);
      setSubmissionState({ state: "ready", value: accepted });
      idempotencyKey.current = null;
    } else {
      setSubmissionState(response);
    }
    setSending(false);
  }

  async function checkStatus(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (checking || !identity) return;
    setChecking(true);
    setStatusState(null);
    const response = await loadModelSubmissionStatus(submissionId.trim());
    if (response.state === "ready") {
      const status = response.value.data;
      setResult(status);
      setSubmissionId(status.submission_id);
      setSubmissionState(null);
      setStatusState({ state: "ready", value: status });
    } else {
      setStatusState(response);
    }
    setChecking(false);
  }

  return (
    <div className="submission-layout">
      <section className="section-card submission-form-card" aria-labelledby="submission-form-heading">
        <div className="section-heading">
          <div>
            <h2 id="submission-form-heading">Request review</h2>
            <p>Only model metadata, public endpoint/source URLs and the rights attestation are sent.</p>
          </div>
        </div>
        {identity ? (
          <div className="submission-help submission-identity">
            <span>Signed in with verified account <strong>{identity.email}</strong>.</span>
            <form action="/auth/sign-out" method="post"><button className="button-link" type="submit">Sign out</button></form>
          </div>
        ) : (
          <div className="submission-help submission-identity" role="status">
            <span>Sign in with a verified account to submit a request or check its status.</span>
            {loginAvailable ? <a className="button-link" href="/auth/sign-in?return_to=%2Fmodel-submissions">Sign in with your account</a> : <span>Account sign-in is not configured in this environment.</span>}
          </div>
        )}
        {identity ? (
        <form className="submission-fields" onSubmit={submit}>
          <label className="submission-field">
            <span>Model name</span>
            <input required minLength={1} maxLength={160} value={form.model_name} onChange={(event) => update("model_name", event.currentTarget.value)} />
          </label>
          <label className="submission-field">
            <span>Provider</span>
            <input required minLength={1} maxLength={120} value={form.provider} onChange={(event) => update("provider", event.currentTarget.value)} />
          </label>
          <label className="submission-field">
            <span>Organization <span className="submission-optional">Optional</span></span>
            <input maxLength={160} value={form.organization} onChange={(event) => update("organization", event.currentTarget.value)} />
          </label>
          <label className="submission-field">
            <span>Verified account email</span>
            <input type="email" autoComplete="email" value={identity.email} readOnly aria-describedby="submission-email-help" />
            <small id="submission-email-help">This address comes from your verified sign-in and cannot be changed here.</small>
          </label>
          <label className="submission-field submission-field-wide">
            <span>Provider endpoint URL</span>
            <input required type="url" maxLength={512} placeholder="https://api.example.com/v1" value={form.endpoint_url} onChange={(event) => update("endpoint_url", event.currentTarget.value)} />
            <small>HTTPS host URLs only. The endpoint is never contacted during submission or review.</small>
          </label>
          <label className="submission-field submission-field-wide">
            <span>Model/source documentation URL</span>
            <input required type="url" maxLength={512} placeholder="https://provider.example.com/model" value={form.source_url} onChange={(event) => update("source_url", event.currentTarget.value)} />
          </label>
          <label className="submission-field submission-field-wide">
            <span>Source license or permission basis</span>
            <input required maxLength={160} value={form.source_license} onChange={(event) => update("source_license", event.currentTarget.value)} />
          </label>
          <label className="submission-attestation submission-field-wide">
            <input required type="checkbox" checked={form.permission_attested} onChange={(event) => update("permission_attested", event.currentTarget.checked)} />
            <span>I am authorized to request evaluation of this model and its submitted source.</span>
          </label>
          <div className="submission-field-wide">
            <button type="submit" disabled={sending || Boolean(result)}>
              {sending ? "Sending request…" : result ? "Request received" : "Send for review"}
            </button>
          </div>
        </form>
        ) : null}
        {submissionState?.state === "error" ? (
          <p className="submission-error" role="alert">{submissionState.title}. {submissionState.message} {submissionState.requestId ? `Request ${submissionState.requestId}.` : ""}</p>
        ) : null}
        {submissionState?.state === "ready" ? <SubmissionStatus status={submissionState.value} /> : null}
        <p className="submission-safety-note">
          A request creates no model call, VM, benchmark run or charge. An administrator must verify
          source rights, approve a configured endpoint, and bind one model configuration, task set,
          run plan and fixed budget before a queued run can be created.
        </p>
      </section>

      <section className="section-card submission-status-card" aria-labelledby="submission-status-heading">
        <div className="section-heading">
          <div>
            <h2 id="submission-status-heading">Track your request</h2>
            <p>Only the verified account that submitted the request can read its status.</p>
          </div>
        </div>
        {identity ? <form className="submission-track-form" onSubmit={checkStatus}>
          <label className="submission-field">
            <span>Request ID</span>
            <input required autoComplete="off" value={submissionId} onChange={(event) => setSubmissionId(event.currentTarget.value)} />
          </label>
          <p className="submission-help">Only requests owned by {identity.email} can be shown here.</p>
          <button type="submit" disabled={checking || !submissionId.trim()}>
            {checking ? "Checking…" : "Check status"}
          </button>
        </form> : <p className="submission-help">Sign in above to check request status.</p>}
        {statusState?.state === "error" ? (
          <p className="submission-error" role="alert">{statusState.title}. {statusState.message} {statusState.requestId ? `Request ${statusState.requestId}.` : ""}</p>
        ) : null}
        {statusState?.state === "ready" ? <SubmissionStatus status={statusState.value} /> : null}
      </section>
    </div>
  );
}

function SubmissionStatus({ status }: { status: ModelSubmissionStatus }) {
  const copy = status.status === "pending"
    ? "Pending review. No endpoint has been contacted and no run or charge exists."
    : status.status === "rejected"
      ? `Rejected. ${status.rejection_reason ?? "The reviewer did not provide a public reason."}`
      : `Approved. The authorized bounded run is ${status.run_status ?? "queued"}.`;
  return (
    <div className={`submission-status status-${status.status}`} role="status" aria-live="polite">
      <div className="submission-status-heading">
        <strong>{status.status.replaceAll("_", " ")}</strong>
        <span>{status.model_name} · {status.provider}</span>
      </div>
      <p>{copy}</p>
      <dl>
        <dt>Request ID</dt><dd><code>{status.submission_id}</code></dd>
        <dt>Submitted</dt><dd>{new Date(status.submitted_at).toLocaleString()}</dd>
        {status.resulting_run_id ? <><dt>Run ID</dt><dd><code>{status.resulting_run_id}</code></dd></> : null}
        {status.run_progress ? <>
          <dt>Attempt states</dt><dd>{formatStateCounts(status.run_progress.attempt_states)}</dd>
          <dt>Solve job states</dt><dd>{formatStateCounts(status.run_progress.solve_job_states)}</dd>
        </> : null}
      </dl>
    </div>
  );
}

function formatStateCounts(states: Record<string, number>): string {
  const entries = Object.entries(states).sort(([left], [right]) => left.localeCompare(right));
  return entries.length
    ? entries.map(([state, count]) => `${state}: ${count}`).join(", ")
    : "No state counts yet.";
}
