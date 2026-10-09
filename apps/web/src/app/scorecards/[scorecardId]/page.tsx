import { KeyboardScrollRegion } from "@/components/keyboard-scroll-region";
import type { Metadata } from "next";
import Link from "next/link";
import { PageIntro, ReleaseNotice, ResourceState, SectionHeading } from "@/components/public-ui";
import { asUrlQuery, loadReleaseContext, publicApi, type ApiEnvelope, type MetricDefinition, type PublicScorecard, type Resource } from "@/lib/public-api";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Score evidence", description: "Trace a public metric through its released scorecard and evidence." };
type Search = Promise<Record<string, string | string[] | undefined>>;
function first(value: string | string[] | undefined): string | undefined { return Array.isArray(value) ? value[0] : value; }

export default async function ScorecardPage({ params, searchParams }: { params: Promise<{ scorecardId: string }>; searchParams: Search }) {
  const [{ scorecardId }, query] = await Promise.all([params, searchParams]);
  const releaseResource = await loadReleaseContext(first(query.release));
  let scorecardResource: Resource<{ card: PublicScorecard; definitions: readonly MetricDefinition[] }>;
  if (releaseResource.state !== "ready") scorecardResource = releaseResource;
  else {
    const fetched = await publicApi<ApiEnvelope<PublicScorecard>>(`/scorecards/${encodeURIComponent(scorecardId)}?release=${encodeURIComponent(releaseResource.value.releaseId)}`);
    scorecardResource = fetched.state === "ready"
      ? { state: "ready", value: { card: fetched.value.data, definitions: fetched.value.meta.registry?.definitions ?? [] } }
      : fetched;
  }

  return <>
    <PageIntro eyebrow="Public analysis · Score evidence" title="Scorecard evidence" description="Inspect source metric states and the contribution rows exactly as released. Values and formulas are never recomputed in this view." />
    <ResourceState resource={releaseResource}>{(context) => <>
      <div className="page-toolbar"><Link className="back-link" href={`/leaderboard${asUrlQuery({ release: context.releaseId })}`}>← Back to leaderboard</Link><span className="scope-facts"><b>Release</b> {context.releaseId}</span></div>
      <ReleaseNotice release={context.summary} />
      <ResourceState resource={scorecardResource}>
        {({ card, definitions }) => <ScorecardView card={card} definitions={definitions} releaseId={context.releaseId} methodologyVersion={context.summary.methodology_version} />}
      </ResourceState>
    </>}</ResourceState>
  </>;
}

function ScorecardView({ card, definitions, releaseId, methodologyVersion }: { card: PublicScorecard; definitions: readonly MetricDefinition[]; releaseId: string; methodologyVersion: string }) {
  const methodologyHref = `/methodology/${encodeURIComponent(methodologyVersion)}${asUrlQuery({ release: releaseId })}`;
  return <>
    <section className="section-card scorecard-summary">
      <SectionHeading title={card.scorecard_id} description="One public model configuration on one disclosed task version." />
      <dl className="identity-list"><dt>Configuration</dt><dd><Link href={`/models/${encodeURIComponent(card.model_config_id)}${asUrlQuery({ release: releaseId })}`}>{card.model_config_id}</Link></dd><dt>Task version</dt><dd><Link href={`/tasks/${encodeURIComponent(card.task_id)}${asUrlQuery({ release: releaseId })}`}>{card.task_id} · v{card.task_version}</Link></dd><dt>Gate state</dt><dd><span className={`status-tag status-${card.gating_status}`}>{card.gating_status.replaceAll("_", " ")}</span></dd><dt>Formula version</dt><dd><Link href={methodologyHref}>{card.formula_version}</Link></dd><dt>Policy digest</dt><dd><code>{card.policy_digest}</code></dd></dl>
    </section>

    <section className="section-card">
      <SectionHeading title="Released metric values" description="Decimal strings, intervals and missingness labels are the API's original scorecard values. N/A, gated zero, missing, insufficient and review states remain distinct." />
      <div className="metric-summary-grid">
        {card.metrics.map((metric) => {
          const definition = definitions.find((item) => item.metric_id === metric.metric_id);
          return <article className={`metric-summary-card status-${metric.status}`} id={`metric-${metric.metric_id}`} key={metric.metric_id}>
            <span>{metric.label}</span>
            <strong>{metric.status === "measured" ? metric.value : metric.status === "gated_zero" ? "0 · gated zero" : metric.status.replaceAll("_", " ")}</strong>
            {metric.status === "measured" ? <small>{metric.unit}{metric.interval_low !== null && metric.interval_high !== null ? ` · interval ${metric.interval_low}–${metric.interval_high}` : ""}</small> : null}
            {metric.reason ? <p>{metric.reason}</p> : null}
            {definition ? <details><summary>Metric definition</summary><p>{definition.applicability_rule}</p><p>Aggregation: {definition.sample_aggregation} per sample, {definition.task_aggregation} across tasks. Missingness: {definition.missingness_policy}.</p><p>Uncertainty: {definition.uncertainty_method}</p><p>Source score item IDs: {definition.source_score_item_ids.length ? definition.source_score_item_ids.join(", ") : "Not published"}</p></details> : <p>Metric definition is not included in this release registry.</p>}
          </article>;
        })}
      </div>
    </section>

    <section className="section-card">
      <SectionHeading title="Contribution and evidence chain" description="Each row is copied from the released scorecard, including nominal, effective and presentation weights and its recorded arithmetic." />
      {card.contributions.length ? <KeyboardScrollRegion className="table-wrap" label="Released score contributions">
        <table className="data-table compact-table"><caption className="sr-only">Published contribution values, effective weights, arithmetic and public evidence links.</caption>
          <thead><tr><th scope="col">Item</th><th scope="col">Dimension</th><th scope="col">Raw item value</th><th scope="col">Weights in basis points</th><th scope="col">Recorded arithmetic</th><th scope="col">Public evidence</th></tr></thead>
          <tbody>{card.contributions.map((row) => <tr id={`item-${row.item_id}`} key={row.item_id}>
            <th scope="row"><code>{row.item_id}</code></th><td>{row.dimension}</td><td>{row.value ?? "Not measured"}</td>
            <td><span>Nominal {row.nominal_weight_bp}</span><span className="row-subline">Effective {row.effective_weight_bp}</span><span className="row-subline">Presentation {row.presentation_weight_bp}</span></td>
            <td>{row.arithmetic}</td>
            <td>{row.evidence_refs.length ? <ul className="evidence-ref-list">{row.evidence_refs.map((ref) => <li key={ref}><Link href={`/tasks/${encodeURIComponent(card.task_id)}${asUrlQuery({ release: releaseId, open: "1" })}#evidence-${encodeURIComponent(ref)}`}>{ref}</Link></li>)}</ul> : <span>None published</span>}</td>
          </tr>)}</tbody>
        </table>
      </KeyboardScrollRegion> : <div className="empty-state"><h2>No code contribution rows</h2><p>This scorecard does not publish item-level code contributions. It may score an answer-only task; no code dimensions are inferred.</p></div>}
      {card.redacted_evidence_count ? <p className="privacy-note">{card.redacted_evidence_count} evidence reference(s) were omitted because they do not resolve to public task content in this release. Private and held-out identifiers are not returned.</p> : null}
      <p className="form-help">Reconstruct the released value from the unrounded contribution records and versioned formula at <Link href={methodologyHref}>{card.formula_version}</Link>. Display formatting does not replace the source decimal strings above.</p>
    </section>
  </>;
}
