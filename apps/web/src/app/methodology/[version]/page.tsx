import type { Metadata } from "next";
import Link from "next/link";
import { PageIntro, ReleaseNotice, ResourceState, SectionHeading } from "@/components/public-ui";
import { asUrlQuery, loadReleaseContext, publicApi, type ApiEnvelope, type Methodology, type MetricDefinition, type ReleaseSummary, type Resource } from "@/lib/public-api";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Frozen methodology", description: "Read the versioned methods, formulas, limitations and release corrections." };
type Search = Promise<Record<string, string | string[] | undefined>>;
function first(value: string | string[] | undefined): string | undefined { return Array.isArray(value) ? value[0] : value; }

export default async function MethodologyPage({ params, searchParams }: { params: Promise<{ version: string }>; searchParams: Search }) {
  const [{ version }, query] = await Promise.all([params, searchParams]);
  const releaseResource = await loadReleaseContext(first(query.release));
  let methodResource: Resource<{ methodology: Methodology; definitions: readonly MetricDefinition[] }>;
  if (releaseResource.state !== "ready") methodResource = releaseResource;
  else {
    const path = `/methodology/${encodeURIComponent(version)}?release=${encodeURIComponent(releaseResource.value.releaseId)}`;
    const result = await publicApi<ApiEnvelope<Methodology>>(path);
    methodResource = result.state === "ready"
      ? { state: "ready", value: { methodology: result.value.data, definitions: result.value.meta.registry?.definitions ?? [] } }
      : result;
  }

  return <>
    <PageIntro eyebrow="Public explanation · Frozen release methods" title="Methodology and corrections" description="Methods and formula identities are loaded from the exact selected release. A historical URL stays pinned to its original release when later releases supersede or withdraw it." />
    <ResourceState resource={releaseResource}>{(context) => <>
      <div className="page-toolbar"><Link className="back-link" href={`/leaderboard${asUrlQuery({ release: context.releaseId })}`}>← Back to release results</Link><div className="scope-facts"><span><b>Release identity</b>{context.releaseId}</span><span><b>Frozen version</b>{version}</span></div></div>
      <ReleaseNotice release={context.summary} />
      <ResourceState resource={methodResource}>
        {({ methodology, definitions }) => <MethodologyView methodology={methodology} definitions={definitions} release={context.summary} />}
      </ResourceState>
    </>}</ResourceState>
  </>;
}

function MethodologyView({ methodology, definitions, release }: { methodology: Methodology; definitions: readonly MetricDefinition[]; release: ReleaseSummary }) {
  const visibleSections: readonly [string, readonly string[]][] = [
    ["Methods", methodology.methods],
    ["Versioned formulas", methodology.formulas],
    ["Tools and versions", methodology.tools],
    ["Protocol deviations", methodology.deviations],
    ["Limitations", methodology.limitations],
  ];
  return <>
    <section className="section-card methodology-identity">
      <SectionHeading title={`Methodology ${methodology.version}`} description="This document is resolved from the immutable content of the release shown below." />
      <dl className="identity-list"><dt>Methodology version</dt><dd><code>{methodology.version}</code></dd><dt>Original release</dt><dd><code>{release.release_id}</code></dd><dt>Cohort digest</dt><dd><code>{release.cohort_digest}</code></dd><dt>Release state</dt><dd>{release.state}{release.state === "withdrawn" && release.withdrawal_reason ? ` · ${release.withdrawal_reason}` : ""}</dd></dl>
      {release.replacement_release_id ? <p className="privacy-note">Successor release: <Link href={`/leaderboard${asUrlQuery({ release: release.replacement_release_id })}`}>{release.replacement_release_id}</Link>. This page and its methodology remain pinned to {release.release_id}.</p> : null}
    </section>
    {visibleSections.map(([title, rows]) => <section className="section-card" key={title}><SectionHeading title={title} />{rows.length ? <ul className="prose-list">{rows.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul> : <p className="plain-note">No {title.toLowerCase()} were published in this methodology version.</p>}</section>)}

    <section className="section-card">
      <SectionHeading title="Native and adapted benchmark records" description="The public release records the source classification and its explanation. The interface does not upgrade adapted work into a native result." />
      {methodology.native_benchmarks.length ? <div className="table-wrap" role="region" aria-label="Native and adapted benchmark descriptions" tabIndex={0}><table className="data-table compact-table"><caption className="sr-only">Published benchmark labels and their source or adaptation descriptions.</caption><thead><tr><th scope="col">Published classification</th><th scope="col">Record</th></tr></thead><tbody>{methodology.native_benchmarks.map(([classification, detail], index) => <tr key={`${classification}-${index}`}><th scope="row"><span className={`classification-badge classification-${classification.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`}>{classification}</span></th><td>{detail}</td></tr>)}</tbody></table></div> : <p className="plain-note">No native or adapted benchmark records were included in this release.</p>}
    </section>

    <section className="section-card">
      <SectionHeading title="Metric registry and source items" description="Definitions, aggregation policy, applicability, uncertainty and source item identifiers are read from this release's API registry." />
      {definitions.length ? <div className="metric-definition-grid">{definitions.map((definition) => <article className="metric-definition-card" key={definition.metric_id}><h3>{definition.label}</h3><p><code>{definition.metric_id}</code> · {definition.unit} · {definition.direction} is better</p><dl><dt>Domain</dt><dd>{definition.domain[0]}–{definition.domain[1]}</dd><dt>Applicability</dt><dd>{definition.applicability_rule}</dd><dt>Aggregation</dt><dd>{definition.sample_aggregation} samples · {definition.task_aggregation} tasks</dd><dt>Missingness</dt><dd>{definition.missingness_policy}</dd><dt>Uncertainty</dt><dd>{definition.uncertainty_method}</dd><dt>Source score items</dt><dd>{definition.source_score_item_ids.length ? definition.source_score_item_ids.map((id) => <code key={id}>{id}</code>) : "Not published"}</dd></dl></article>)}</div> : <p className="plain-note">No metric definitions are available in this release registry.</p>}
    </section>

    <section className="section-card">
      <SectionHeading title="Corrections and withdrawals" description="Release history is immutable and shown against the original release identity." />
      {methodology.correction_history.length ? <ol className="prose-list">{methodology.correction_history.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ol> : <p className="plain-note">No correction entry was published in this methodology version.</p>}
      {release.state === "withdrawn" ? <div className="withdrawal-panel"><h3>Release withdrawn</h3><p>{release.withdrawal_reason ?? "A withdrawal notice is recorded without a public reason."}</p>{release.replacement_release_id ? <p>Successor: <Link href={`/leaderboard${asUrlQuery({ release: release.replacement_release_id })}`}>{release.replacement_release_id}</Link></p> : null}</div> : <p className="plain-note">This release is not marked withdrawn.</p>}
    </section>
  </>;
}
