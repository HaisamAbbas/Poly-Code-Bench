import Link from "next/link";
import type { PublicAuditAttestationView, Resource } from "@/lib/public-api";
import { PageIntro } from "@/components/public-ui";

function formatUtc(value: string): string {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return value;
  return `${new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  }).format(date)} UTC`;
}

function verificationLabel(result: string): string {
  const labels: Record<string, string> = {
    current_scoped_attestation: "Signature valid; current scoped publication verified",
    development_key_not_endorsed: "Development signature valid; not an endorsement",
    signature_valid_revocation_stale: "Signature valid; revocation information is stale",
    signature_valid_revocation_unchecked: "Signature valid; current revocation state was not checked",
    expired: "Attestation expired",
    revoked: "Attestation revoked",
    superseded: "Attestation superseded by a correction",
    not_published: "Attestation is not published",
    untrusted_signer: "Signing key is not trusted",
    invalid_signature: "Signature is invalid",
    scope_digest_mismatch: "Attestation scope digest does not match",
  };
  return labels[result] ?? "Attestation status is unavailable";
}

function verificationNote(view: PublicAuditAttestationView): string {
  if (!view.verification.signature_valid) {
    return "Signature verification failed; claims and signer status are not endorsed.";
  }
  if (view.verification.current_endorsement) {
    return "Current trust and revocation checks support only this finite published scope.";
  }
  if (view.verification.revocation_freshness === "offline") {
    return "Current trust or revocation data was unavailable. Signature validity alone is not a current endorsement.";
  }
  if (view.verification.revocation_freshness === "stale") {
    return "Trust store or revocation data is stale. Signature validity alone is not a current endorsement.";
  }
  return "The signature is valid, but the current key or lifecycle state does not provide an endorsement.";
}

export function PublicAuditAttestation({ view }: { view: PublicAuditAttestationView }) {
  const { attestation, verification, successor_id: successorId } = view;
  const { claims } = attestation;
  const verified = verification.signature_valid;
  const scopeCounts = [
    ["Selected tasks", claims.health.selected_tasks],
    ["Assessed tasks", claims.health.assessed_tasks],
    ["Complete scope", claims.health.complete_tasks],
    ["Partial scope", claims.health.partial_tasks],
    ["Unknown", claims.health.unknown_tasks],
    ["Unscanned", claims.health.unscanned_tasks],
    ["Blocked", claims.health.blocked_tasks],
  ] as const;
  const riskCounts = [
    ["Low observed risk", claims.health.low_risk_tasks],
    ["Medium observed risk", claims.health.medium_risk_tasks],
    ["High observed risk", claims.health.high_risk_tasks],
    ["Insufficient evidence", claims.health.insufficient_risk_tasks],
  ] as const;

  return (
    <>
      <PageIntro
        eyebrow="Public signature verification"
        title="Benchmark Audit Attestation"
        description="A signed statement about one scoped benchmark audit. Signature validity authenticates the signed bytes; it does not certify unseen data or establish model training membership."
      />
      <section className="attestation-verification" aria-labelledby="attestation-verification-heading">
        <span className={verification.current_endorsement ? "attestation-state attestation-state-current" : "attestation-state"}>
          {verification.current_endorsement ? "Current scoped publication" : "Qualified verification result"}
        </span>
        <h2 id="attestation-verification-heading">{verificationLabel(verification.result)}</h2>
        <p>Algorithm: {attestation.signature_algorithm}. Key ID: <code>{attestation.key_id}</code>.</p>
        <p>Trust and revocation check: {verification.revocation_freshness}. {verificationNote(view)}</p>
        {verification.result === "superseded" && successorId ? (
          <p>
            Corrected by <Link href={`/audit-attestations/${encodeURIComponent(successorId)}`}>{successorId}</Link>.
          </p>
        ) : null}
      </section>

      {verified ? (
        <>
          <p className="attestation-digest">Signed scope digest: <code>{claims.attestation_digest}</code></p>
          <section className="audit-report-heading" aria-label="Attested benchmark and validity window">
            <div>
              <span className="audit-report-status">Signed public scope</span>
              <h2>{claims.health.benchmark_label}</h2>
              <p>{claims.health.benchmark_version ?? "Version not specified"}</p>
            </div>
            <dl className="audit-report-window">
              <div><dt>Source window start</dt><dd>{formatUtc(claims.health.source_window_start)}</dd></div>
              <div><dt>Source window end</dt><dd>{formatUtc(claims.health.source_window_end)}</dd></div>
              <div><dt>Issued</dt><dd>{formatUtc(claims.issued_at)}</dd></div>
              <div><dt>Expires</dt><dd>{formatUtc(claims.expires_at)}</dd></div>
            </dl>
          </section>
          <section className="section-card" aria-labelledby="attestation-scope-heading">
            <div className="section-heading">
              <h2 id="attestation-scope-heading">Signed scope and missingness</h2>
              <p>These counts describe the signed finite scope. Unknown, unscanned, partial, and blocked work remains visible.</p>
              <p>
                {claims.model_context_bound
                  ? "A model context is bound in the signed scope; this public view does not infer model eligibility."
                  : "This attestation is model-agnostic and makes no model-specific eligibility claim."}
              </p>
            </div>
            <dl className="audit-count-grid">
              {scopeCounts.map(([label, count]) => (
                <div className="audit-count-card" key={label}><dt>{label}</dt><dd>{count.toLocaleString("en")}</dd></div>
              ))}
            </dl>
          </section>
          <section className="section-card" aria-labelledby="attestation-risk-heading">
            <div className="section-heading">
              <h2 id="attestation-risk-heading">Observed risk counts</h2>
              <p>Descriptive observed-risk counts; they are not probabilities, a clean verdict, or model-specific eligibility.</p>
            </div>
            <dl className="audit-risk-list">
              {riskCounts.map(([label, count]) => <div key={label}><dt>{label}</dt><dd>{count.toLocaleString("en")}</dd></div>)}
            </dl>
          </section>
        </>
      ) : (
        <section className="audit-unavailable" role="alert">
          <h2>Signed claims withheld</h2>
          <p>The signature or signer could not be verified. Claim values are not presented as trusted evidence.</p>
        </section>
      )}

      <section className="audit-qualification" aria-labelledby="attestation-limitations-heading">
        <h2 id="attestation-limitations-heading">Authority and limitations</h2>
        <p>This attestation is scoped to the signed report and source window. It is not an independent certification authority, a guarantee about unseen data, or proof that a model did or did not train on these tasks.</p>
        <p>Issue and expiry timestamps are signed claim values; they are not independently timestamp-proven.</p>
        <p>Public disclosure of the report is separate from publication of benchmark task text. Private source references, task content, answers, vectors, and sealed evidence are not included.</p>
        {verified ? (
          <ul>
            {(claims.claim_limitations ?? []).map((item) => (
              <li key={item}>{item.replaceAll("_", " ")}</li>
            ))}
          </ul>
        ) : null}
      </section>
      <p className="audit-report-back"><Link href="/audit-attestations">Verify another attestation</Link></p>
    </>
  );
}

export function PublicAuditAttestationState({ resource }: { resource: Resource<PublicAuditAttestationView> }) {
  if (resource.state === "ready") return <PublicAuditAttestation view={resource.value} />;
  const message = resource.state === "error"
    ? resource.message
    : resource.state === "empty"
      ? resource.message
      : "The public attestation is loading.";
  return (
    <section className="audit-unavailable" role={resource.state === "error" ? "alert" : "status"}>
      <span className="state-kicker">Public attestation unavailable</span>
      <h1>{resource.state === "error" ? resource.title : "No public attestation is available"}</h1>
      <p>{message}</p>
      {resource.state === "error" && resource.requestId ? <small>Request {resource.requestId}</small> : null}
      <Link className="button-link" href="/audit-attestations">Return to attestation lookup</Link>
    </section>
  );
}
