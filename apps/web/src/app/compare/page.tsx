import type { Metadata } from "next";
import Link from "next/link";
import {
  PageIntro,
  ReleaseNotice,
  ReleaseSelector,
  ResourceState,
  SectionHeading,
  formatMicros,
} from "@/components/public-ui";
import {
  asUrlQuery,
  loadReleaseContext,
  publicApi,
  type ApiEnvelope,
  type ComparisonResult,
  type LeaderboardEntry,
  type PairedTaskDelta,
  type ReleaseSummary,
  type Resource,
  scorecardPageHref,
} from "@/lib/public-api";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Compare configurations",
  description: "Compare compatible configurations on the same release and disclosed task versions.",
};

type Search = Promise<Record<string, string | string[] | undefined>>;

function values(input: string | string[] | undefined): string[] {
  if (input === undefined) return [];
  return Array.isArray(input) ? input : [input];
}

function single(input: string | string[] | undefined): string | undefined {
  return Array.isArray(input) ? input[0] : input;
}

function comparisonQuery(params: Record<string, string | string[] | undefined>, releaseId: string): string {
  const query = new URLSearchParams({ release: releaseId });
  for (const key of ["models", "language", "family", "difficulty"]) {
    for (const value of values(params[key])) query.append(key, value);
  }
  return query.toString();
}

export default async function ComparePage({ searchParams }: { searchParams: Search }) {
  const query = await searchParams;
  const selectedModels = [...new Set(values(query.models))];
  const releaseContext = await loadReleaseContext(single(query.release));
  return (
    <>
      <PageIntro
        eyebrow="Public analysis · Exact release cohort"
        title="Compare configurations"
        description="Select two to four configurations and inspect API-paired results for the same disclosed task versions. Release, filters and selections remain in the URL."
      />
      <ResourceState resource={releaseContext}>
        {(context) => <CompareRelease
          release={context.summary}
          releases={context.releases}
          selectedModels={selectedModels}
          query={query}
        />}
      </ResourceState>
    </>
  );
}

async function CompareRelease({
  release,
  releases,
  selectedModels,
  query,
}: {
  release: ReleaseSummary;
  releases: readonly ReleaseSummary[];
  selectedModels: readonly string[];
  query: Record<string, string | string[] | undefined>;
}) {
  const board = await publicApi<ApiEnvelope<readonly LeaderboardEntry[]>>(
    `/leaderboard?release=${encodeURIComponent(release.release_id)}&limit=200`,
  );
  const compareResource: Resource<ComparisonResult> = selectedModels.length < 2 || selectedModels.length > 4
    ? {
        state: "error",
        title: selectedModels.length > 4 ? "Choose at most four configurations" : "Choose at least two configurations",
        message: "Comparison requires two to four distinct configurations from one release.",
      }
    : await publicApi<ApiEnvelope<ComparisonResult>>(`/compare?${comparisonQuery(query, release.release_id)}`).then((result) =>
        result.state === "ready" ? { state: "ready", value: result.value.data } : result,
      );

  return (
    <>
      <div className="page-toolbar">
        <ReleaseSelector
          releases={releases}
          releaseId={release.release_id}
          action="/compare"
          preserved={{
            models: selectedModels,
            language: single(query.language),
            family: single(query.family),
            difficulty: single(query.difficulty),
          }}
        />
        <div className="scope-facts"><span><b>Release cohort</b>{release.cohort_digest}</span><span><b>Scope</b>{release.scope.replaceAll("_", " ")}</span></div>
      </div>
      <ReleaseNotice release={release} />
      <ResourceState resource={board}>
        {(boardData) => <>
          <section className="section-card compare-controls">
            <SectionHeading title="Configuration selection" description="Configuration IDs and labels come from this release's public leaderboard projection." />
            <form action="/compare" method="get" className="compare-form">
              <input type="hidden" name="release" value={release.release_id} />
              {(["language", "family", "difficulty"] as const).map((key) => values(query[key]).map((value) =>
                <input key={`${key}-${value}`} type="hidden" name={key} value={value} />,
              ))}
              <label htmlFor="compare-models">Configurations <span>(select 2–4)</span></label>
              <select id="compare-models" name="models" multiple size={Math.min(8, Math.max(3, boardData.data.length))} defaultValue={[...selectedModels]}>
                {boardData.data.map((entry) => <option key={entry.model_config_id} value={entry.model_config_id}>{entry.label} · {entry.model_config_id}</option>)}
              </select>
              <button type="submit">Compare selected configurations</button>
            </form>
            <form action="/compare" method="get" className="compare-filters">
              <input type="hidden" name="release" value={release.release_id} />
              {selectedModels.map((model) => <input key={model} type="hidden" name="models" value={model} />)}
              <label>Language <input name="language" defaultValue={single(query.language) ?? ""} maxLength={64} placeholder="Any common language" /></label>
              <label>Task family <input name="family" defaultValue={single(query.family) ?? ""} maxLength={64} placeholder="Any common family" /></label>
              <label>Difficulty <input name="difficulty" defaultValue={single(query.difficulty) ?? ""} maxLength={32} placeholder="Any common difficulty" /></label>
              <button type="submit">Apply task filters</button>
            </form>
            <p className="form-help">The API rejects mixed protocol or budget profiles and configurations without exact shared task scorecards. A visible task count is based on those scorecards, never an inferred minimum coverage count.</p>
          </section>
          <ResourceState resource={compareResource} emptyTitle="Comparison unavailable" emptyMessage="The selected release contains no comparable configuration rows.">
            {(result) => <ComparisonView result={result} release={release} />}
          </ResourceState>
        </>}
      </ResourceState>
    </>
  );
}

function ComparisonView({ result, release }: { result: ComparisonResult; release: ReleaseSummary }) {
  if (result.incompatibilities.length) {
    return (
      <section className="section-card">
        <SectionHeading title="Configurations are not comparable" description="The API returned typed incompatibilities and no comparison values." />
        <ul className="incompatibility-list">
          {result.incompatibilities.map((item, index) => <li key={`${item.code}-${item.model_config_id ?? index}`}><strong>{item.code.replaceAll("_", " ")}</strong>{item.model_config_id ? <span>{item.model_config_id}</span> : null}<p>{item.detail}</p></li>)}
        </ul>
        <p className="form-help">Change the selection or filters. No partial scores are shown for an incompatible set.</p>
      </section>
    );
  }

  const pairs = new Map<string, PairedTaskDelta[]>();
  for (const delta of result.paired_task_deltas) {
    const key = `${delta.task_id}@${delta.task_version}`;
    pairs.set(key, [...(pairs.get(key) ?? []), delta]);
  }
  const activeFilters = [
    ...result.applied_filters.languages.map((value) => `language: ${value}`),
    ...result.applied_filters.families.map((value) => `family: ${value}`),
    ...result.applied_filters.difficulties.map((value) => `difficulty: ${value}`),
  ];
  return (
    <>
      <section className="section-card">
        <SectionHeading title="Configuration identities" description="Each displayed release metric, coverage, cost and latency value is returned by the pinned release API and links to its source scorecard." />
        <p className="cohort-digest"><b>Release</b> {result.release_id} <b>Cohort digest</b> <code>{result.cohort_digest}</code></p>
        <div className="comparison-grid">
          {result.entries.map((entry) => {
            const scorecardHref = scorecardPageHref(entry.evidence_url, release.release_id);
            return <article className="comparison-model" key={entry.model_config_id}>
            <h3><Link href={`/models/${encodeURIComponent(entry.model_config_id)}${asUrlQuery({ release: release.release_id })}`}>{entry.label}</Link></h3>
            <code>{entry.model_config_id}</code>
            <dl>
              <dt>Run protocol</dt><dd>{entry.run_mode ?? "Not disclosed"}</dd>
              <dt>Budget profile</dt><dd>{entry.budget_profile_id ?? "Not disclosed"}</dd>
              <dt>Declared languages</dt><dd>{entry.languages.join(", ") || "Not disclosed"}</dd>
              <dt>Published coverage</dt><dd><Link href={scorecardHref}>{entry.coverage.tasks} tasks · {entry.coverage.samples} samples · {entry.coverage.independent_clusters} clusters</Link></dd>
              <dt>Generation cost</dt><dd><Link href={scorecardHref}>{entry.generation_cost_micros === null ? "Not reported" : formatMicros(entry.generation_cost_micros)}</Link></dd>
              <dt>Latency</dt><dd><Link href={scorecardHref}>{entry.latency_ms_p50 === null && entry.latency_ms_p95 === null ? "Not reported" : `p50 ${entry.latency_ms_p50 ?? "—"} ms · p95 ${entry.latency_ms_p95 ?? "—"} ms`}</Link></dd>
            </dl>
            <div className="release-metric-list">
              {entry.metrics.map((metric) => <div key={metric.metric_id} className={`release-metric status-${metric.status}`}>
                <Link href={`${scorecardHref}#metric-${encodeURIComponent(metric.metric_id)}`}>{metric.label}</Link>
                <strong><Link href={`${scorecardHref}#metric-${encodeURIComponent(metric.metric_id)}`}>{metric.status === "measured" ? metric.value : metric.status === "gated_zero" ? "0 · gated" : metric.status.replaceAll("_", " ")}</Link></strong>
                {metric.interval_low !== null && metric.interval_high !== null ? <small>API interval: {metric.interval_low}–{metric.interval_high}</small> : null}
              </div>)}
            </div>
          </article>;
          })}
        </div>
        <p className="form-help">The API reports {result.release_metric_scope.replaceAll("_", " ")} metrics and aggregate deltas. Task filters below ({activeFilters.length ? activeFilters.join(", ") : "none"}) apply only to {result.task_pair_scope.replaceAll("_", " ")} references and task deltas; release aggregates are not recomputed or reweighted.</p>
      </section>

      <section className="section-card">
        <SectionHeading id="common-task-versions" title="Exact common task versions" description="A row appears only when every selected configuration has a public scorecard for this task ID and version." />
        <p className="common-task-count"><a href="#common-task-versions"><strong>{result.common_tasks}</strong> common disclosed tasks</a>{result.common_independent_clusters === null ? " · common independent-cluster count not disclosed" : ` · ${result.common_independent_clusters} common independent clusters`}</p>
        {result.common_task_refs.length ? <div className="table-wrap" role="region" aria-label="Exact common task scorecards" tabIndex={0}>
          <table className="data-table compact-table"><caption className="sr-only">Common disclosed task versions with selected configuration scorecards.</caption>
            <thead><tr><th scope="col">Task version</th><th scope="col">Scope</th><th scope="col">Source scorecards</th></tr></thead>
            <tbody>{result.common_task_refs.map((task) => <tr key={`${task.task_id}@${task.task_version}`}>
              <th scope="row"><Link href={`/tasks/${encodeURIComponent(task.task_id)}${asUrlQuery({ release: result.release_id })}`}>{task.task_id} · v{task.task_version}</Link></th>
              <td>{task.language_id} · {task.family} · {task.difficulty}</td>
              <td><ul className="inline-scorecards">{task.scorecards.map((card) => <li key={card.model_config_id}><Link href={`/scorecards/${encodeURIComponent(card.scorecard_id)}${asUrlQuery({ release: result.release_id })}`}>{card.model_config_id}: {card.scorecard_id}</Link></li>)}</ul></td>
            </tr>)}</tbody>
          </table>
        </div> : <p className="payload-state">No public task scorecards matched this release and filter.</p>}
      </section>

      <section className="section-card">
        <SectionHeading title="Paired task differences" description="Candidate minus the first selected configuration, as calculated and returned by the release API for each shared task scorecard." />
        {pairs.size ? <div className="table-wrap" role="region" aria-label="Paired task metric differences" tabIndex={0}>
          <table className="data-table compact-table"><caption className="sr-only">API paired differences, reported interval difference bounds and source scorecards.</caption>
            <thead><tr><th scope="col">Task</th><th scope="col">Metric</th><th scope="col">Baseline → candidate</th><th scope="col">Published values</th><th scope="col">Difference</th><th scope="col">Interval statement</th><th scope="col">Status</th></tr></thead>
            <tbody>{[...pairs.entries()].flatMap(([key, rows]) => rows.map((delta) => <tr key={`${key}-${delta.candidate_model_config_id}-${delta.metric_id}`}>
              <th scope="row"><Link href={`/tasks/${encodeURIComponent(delta.task_id)}${asUrlQuery({ release: result.release_id })}`}>{delta.task_id} · v{delta.task_version}</Link><small className="row-subline"><Link href={`/scorecards/${encodeURIComponent(delta.baseline_scorecard_id)}${asUrlQuery({ release: result.release_id })}`}>{delta.baseline_scorecard_id}</Link> · <Link href={`/scorecards/${encodeURIComponent(delta.candidate_scorecard_id)}${asUrlQuery({ release: result.release_id })}`}>{delta.candidate_scorecard_id}</Link></small></th>
              <td>{delta.label} <small>({delta.metric_id})</small></td>
              <td>{delta.baseline_model_config_id} → {delta.candidate_model_config_id}</td>
              <td>{delta.baseline_value ?? "Not measured"} → {delta.candidate_value ?? "Not measured"}</td>
              <td>{delta.delta_value ?? "—"}</td>
              <td>{delta.interval_low !== null && delta.interval_high !== null ? `${delta.interval_low}–${delta.interval_high} · ${delta.interval_method.replaceAll("_", " ")}` : "No interval published"}</td>
              <td><span className={`status-tag status-${delta.status}`}>{delta.status.replaceAll("_", " ")}</span>{delta.reason ? <small className="row-subline">{delta.reason}</small> : null}</td>
            </tr>))}</tbody>
          </table>
        </div> : <p className="payload-state">The API did not return measured paired task differences for this common cohort.</p>}
        <p className="form-help">Interval bounds are only the API’s difference of the released scorecard interval endpoints. They are not described as a paired bootstrap confidence interval. See each source scorecard and the frozen methodology for its formula and limits.</p>
      </section>
      {result.limitations.length ? <section className="section-card"><SectionHeading title="Release limitations" /><ul>{result.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul></section> : null}
    </>
  );
}
