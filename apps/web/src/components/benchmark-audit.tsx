import Link from "next/link";
import type { PublicBenchmarkHealth, Resource } from "@/lib/public-api";
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

function limitationLabel(value: string): string {
  return value.replaceAll("_", " ");
}

export function PublicAuditReport({ report }: { report: PublicBenchmarkHealth }) {
  const scopeCounts = [
    ["Selected tasks", report.selected_tasks],
    ["Assessed tasks", report.assessed_tasks],
    ["Complete scope", report.complete_tasks],
    ["Partial scope", report.partial_tasks],
    ["Unknown", report.unknown_tasks],
    ["Unscanned", report.unscanned_tasks],
    ["Blocked", report.blocked_tasks],
  ] as const;
  const riskCounts = [
    ["Low observed risk", report.low_risk_tasks],
    ["Medium observed risk", report.medium_risk_tasks],
    ["High observed risk", report.high_risk_tasks],
    ["Insufficient evidence", report.insufficient_risk_tasks],
  ] as const;

  return (
    <>
      <PageIntro
        eyebrow="Public benchmark audit"
        title="Benchmark health report"
        description="A reviewed aggregate view of one benchmark version, with its selected scope, evidence coverage, unknowns, and limitations kept visible."
      />

      <section className="audit-report-heading" aria-label="Report identity and source window">
        <div>
          <span className="audit-report-status">Published aggregate report</span>
          <h2>{report.benchmark_label}</h2>
          <p>{report.benchmark_version ?? "Version not specified in this projection"}</p>
        </div>
        <dl className="audit-report-window">
          <div><dt>Source window start</dt><dd>{formatUtc(report.source_window_start)}</dd></div>
          <div><dt>Source window end</dt><dd>{formatUtc(report.source_window_end)}</dd></div>
        </dl>
      </section>

      <section className="section-card" aria-labelledby="audit-scope-heading">
        <div className="section-heading">
          <h2 id="audit-scope-heading">Audit scope and missingness</h2>
          <p>Every selected task remains represented, including work that is unknown, unscanned, partial, or blocked.</p>
        </div>
        <dl className="audit-count-grid">
          {scopeCounts.map(([label, count]) => (
            <div className="audit-count-card" key={label}>
              <dt>{label}</dt>
              <dd>{count.toLocaleString("en")}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section className="section-card" aria-labelledby="audit-risk-heading">
        <div className="section-heading">
          <h2 id="audit-risk-heading">Observed risk index distribution</h2>
          <p>These are descriptive task counts. The index is not a probability, a benchmark verdict, or a model-specific eligibility decision.</p>
        </div>
        <dl className="audit-risk-list">
          {riskCounts.map(([label, count]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{count.toLocaleString("en")}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section className="audit-qualification" aria-labelledby="audit-limits-heading">
        <h2 id="audit-limits-heading">Evidence limits</h2>
        <p>This aggregate does not establish the absence of contamination or model-specific cutoff eligibility. Individual tasks, answers, fingerprints, vectors, and sealed evidence are not exposed here.</p>
        <p>Comparable trends, source timelines, corrected evidence, derived versions, and attestations are not included in this public projection.</p>
        <details>
          <summary>Recorded limitations</summary>
          <ul>{report.limitations.map((item) => <li key={item}>{limitationLabel(item)}</li>)}</ul>
        </details>
      </section>

      <p className="audit-report-back"><Link href="/audit-reports">Look up another public report</Link></p>
    </>
  );
}
export function PublicAuditReportState({ resource }: { resource: Resource<PublicBenchmarkHealth> }) {
  if (resource.state === "ready") return <PublicAuditReport report={resource.value} />;

  const message = resource.state === "error"
    ? resource.message
    : resource.state === "empty"
      ? resource.message
      : "The public report is loading.";

  return (
    <section className="audit-unavailable" role={resource.state === "error" ? "alert" : "status"}>
      <span className="state-kicker">Public report unavailable</span>
      <h1>{resource.state === "error" ? resource.title : "No public report is available"}</h1>
      <p>{message}</p>
      {resource.state === "error" && resource.requestId ? <small>Request {resource.requestId}</small> : null}
      <Link className="button-link" href="/audit-reports">Return to report lookup</Link>
    </section>
  );
}
