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
  contact_email: "",
  endpoint_url: "",
  source_url: "",
  source_license: "",
  permission_attested: false,
};

export function ModelSubmissionForm() {
  const [form, setForm] = useState(EMPTY_FORM);
  const [token, setToken] = useState("");
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
    if (sending || result) return;
    setSending(true);
    setSubmissionState(null);
    idempotencyKey.current ??= crypto.randomUUID();
    const payload: ModelSubmissionRequest = {
      kind: "model_submission_input",
      model_name: form.model_name.trim(),
      provider: form.provider.trim(),
      ...(form.organization.trim() ? { organization: form.organization.trim() } : {}),
      contact_email: form.contact_email.trim(),
      endpoint_url: form.endpoint_url.trim(),
      source_url: form.source_url.trim(),
      source_license: form.source_license.trim(),
      permission_attested: form.permission_attested,
    };
    const response = await sendModelSubmission(token, idempotencyKey.current, payload);
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
    if (checking) return;
    setChecking(true);
    setStatusState(null);
    const response = await loadModelSubmissionStatus(token, submissionId.trim());
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
        <label className="submission-field">
          <span>Verified account access token</span>
          <input
            autoComplete="off"
            name="verified-account-token"
            type="password"
            value={token}
            onChange={(event) => setToken(event.currentTarget.value)}
            aria-describedby="submission-token-help"
          />
        </label>
        <p id="submission-token-help" className="submission-help">
          Use the short-lived token from your verified PolyCodeBench account. It stays in this page&#39;s
          memory and is sent only as an authorization header. Never enter a provider API key here.
        </p>
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
            <input required type="email" maxLength={320} autoComplete="email" value={form.contact_email} onChange={(event) => update("contact_email", event.currentTarget.value)} />
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
        <form className="submission-track-form" onSubmit={checkStatus}>
          <label className="submission-field">
            <span>Request ID</span>
            <input required autoComplete="off" value={submissionId} onChange={(event) => setSubmissionId(event.currentTarget.value)} />
          </label>
          <p className="submission-help">Status requests use the verified account token entered in the request form. It remains only in page memory.</p>
          <button type="submit" disabled={checking || !submissionId.trim()}>
            {checking ? "Checking…" : "Check status"}
          </button>
        </form>
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
      </dl>
    </div>
  );
}
