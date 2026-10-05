import Link from "next/link";
import type { ReactNode } from "react";
import {
  asUrlQuery,
  type Coverage,
  type LeaderboardEntry,
  type MetricDefinition,
  type PublicMetric,
  type ReleaseSummary,
  type Resource,
  scoreEvidenceHref,
} from "@/lib/public-api";
import { KeyboardScrollRegion } from "@/components/keyboard-scroll-region";

export function AppHeader() {
  return (
    <header className="site-header">
      <Link className="brand" href="/leaderboard" aria-label="PolyCodeBench home">
        <span className="brand-mark" aria-hidden="true">P</span>
        <span>PolyCodeBench</span>
      </Link>
      <nav className="primary-nav" aria-label="Main navigation">
        <Link href="/leaderboard">Leaderboard</Link>
        <a href="/leaderboard#language-filter">Languages</a>
        <Link href="/compare">Compare</Link>
        <Link href="/tasks">Tasks</Link>
        <Link href="/model-submissions">Submit model</Link>
      </nav>
      <span className="header-caption">Public release explorer</span>
    </header>
  );
}

export function PageIntro({
  eyebrow,
  title,
  description,
}: {
  eyebrow: string;
  title: string;
  description: string;
}) {
  return (
    <div className="page-intro">
      <p className="eyebrow">{eyebrow}</p>
      <h1>{title}</h1>
      <p className="lede">{description}</p>
    </div>
  );
}

export function ResourceState<T>({
  resource,
  emptyTitle = "No public release data",
  emptyMessage = "There is no published release data for this view yet.",
  children,
}: {
  resource: Resource<T>;
  emptyTitle?: string;
  emptyMessage?: string;
  children?: (value: T) => ReactNode;
}) {
  if (resource.state === "loading") {
    return (
      <section className="state-card" aria-live="polite" aria-busy="true">
        <span className="state-kicker">Loading release data</span>
        <p>Connecting to the public release API…</p>
      </section>
    );
  }
  if (resource.state === "error") {
    return (
      <section className="state-card state-error" role="alert">
        <span className="state-kicker">Unable to load results</span>
        <h2>{resource.title}</h2>
        <p>{resource.message}</p>
        {resource.requestId ? <small>Request {resource.requestId}</small> : null}
        <a className="button-link" href="/leaderboard">Open the leaderboard</a>
      </section>
    );
  }
  if (resource.state === "empty") {
    return (
      <section className="state-card">
        <span className="state-kicker">No rows</span>
        <h2>{resource.title || emptyTitle}</h2>
        <p>{resource.message || emptyMessage}</p>
      </section>
    );
  }
  return <>{children?.(resource.value)}</>;
}

export function EmptyState({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="empty-state">
      <span className="state-kicker">No rows</span>
      <h2>{title}</h2>
      <p>{children}</p>
    </section>
  );
}

export function ReleaseSelector({
  releases,
  releaseId,
  action,
  preserved = {},
}: {
  releases: readonly ReleaseSummary[];
  releaseId: string;
  action: string;
  preserved?: Record<string, string | readonly string[] | undefined>;
}) {
  return (
    <form className="release-selector" action={action} method="get">
      <label htmlFor="release-select">Release</label>
      <select id="release-select" name="release" defaultValue={releaseId}>
        {releases.map((release) => (
          <option key={release.release_id} value={release.release_id}>
            v{release.version} · {release.scope} · {release.state}
          </option>
        ))}
      </select>
      {Object.entries(preserved).flatMap(([key, value]) => {
        if (!value || key === "release") return [];
        return (Array.isArray(value) ? value : [value]).map((item, index) =>
          <input key={`${key}-${index}`} type="hidden" name={key} value={item} />,
        );
      })}
      <button type="submit">Load release</button>
    </form>
  );
}

export function ReleaseNotice({ release }: { release: ReleaseSummary }) {
  const synthetic = release.fixture_kind === "synthetic_internal";
  return (
    <aside className={`release-notice${synthetic ? " release-notice-synthetic" : ""}`}>
      <div>
        <strong>{synthetic ? "Synthetic internal test data" : `Release v${release.version}`}</strong>
        <p>
          {synthetic
            ? "Development projection only. These are not live benchmark results."
            : `${release.scope === "exploratory" ? "Exploratory release" : "Ranked eligible release"} · ${release.state}`}
        </p>
      </div>
      <div className="release-facts" aria-label="Release scope and version">
        <span><b>Scope</b> {release.scope.replaceAll("_", " ")}</span>
        <span><b>Version</b> {release.version}</span>
      </div>
      {release.state === "withdrawn" ? (
        <p className="withdrawal-copy">
          This release has been withdrawn. {release.withdrawal_reason ?? "See its release notice."}
          {release.replacement_release_id ? (
            <> Successor release: <Link href={`/leaderboard${asUrlQuery({ release: release.replacement_release_id })}`}>{release.replacement_release_id}</Link>.</>
          ) : null}
        </p>
      ) : null}
      {release.limitations.length ? (
        <details className="limitations">
          <summary>Release limitations</summary>
          <ul>{release.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
        </details>
      ) : null}
      <Link
        className="methodology-link"
        href={`/methodology/${encodeURIComponent(release.methodology_version)}${asUrlQuery({ release: release.release_id })}`}
      >
        Frozen methodology · {release.methodology_version}
      </Link>
    </aside>
  );
}

export function CoverageBadge({ coverage, sourceUrl }: { coverage: Coverage; sourceUrl: string }) {
  return (
    <a
      className="coverage-link"
      href={sourceUrl}
      aria-label={`Open source score evidence: ${coverage.tasks} tasks, ${coverage.samples} samples, ${coverage.independent_clusters} independent clusters`}
    >
      <strong>{coverage.tasks}</strong> tasks
      <span>{coverage.samples} samples</span>
      <span>{coverage.independent_clusters} clusters</span>
    </a>
  );
}

const statusLabels: Record<PublicMetric["status"], string> = {
  measured: "Measured",
  gated_zero: "Gated zero",
  not_applicable: "N/A",
  insufficient_information: "Insufficient information",
  missing: "Missing",
  needs_review: "Pending review",
};

function displayValue(metric: PublicMetric): string {
  if (metric.status === "gated_zero") return "0";
  if (metric.status !== "measured" || metric.value === null) return "—";
  const numeric = Number(metric.value);
  if (!Number.isFinite(numeric)) return metric.value;
  return metric.unit === "score" ? numeric.toFixed(2) : metric.value;
}

function displayInterval(metric: PublicMetric): string | null {
  if (metric.interval_low === null || metric.interval_high === null) return null;
  return `${metric.interval_low}–${metric.interval_high}`;
}

export function MetricValue({
  metric,
  sourceUrl,
  compact = false,
}: {
  metric: PublicMetric;
  sourceUrl: string;
  compact?: boolean;
}) {
  const interval = displayInterval(metric);
  return (
    <div className={`metric-value${compact ? " metric-value-compact" : ""}`}>
      <a
        className={`metric-source status-${metric.status}`}
        href={sourceUrl}
        title={metric.reason ?? `${metric.label} source score`}
        aria-label={`${metric.label}: ${statusLabels[metric.status]}${metric.status === "measured" ? `, ${metric.value} ${metric.unit}` : ""}. Open source score and evidence.`}
      >
        <span className="status-label">{statusLabels[metric.status]}</span>
        <strong>{displayValue(metric)}</strong>
      </a>
      {interval ? (
        <span className="confidence-interval" title={`Reported interval: ${interval}`}>
          Interval {interval}
        </span>
      ) : null}
      {metric.reason ? <span className="metric-reason">{metric.reason}</span> : null}
    </div>
  );
}

export function SortableLeaderboard({
  entries,
  metricDefinitions,
  releaseId,
  language,
  sort,
  direction,
  basePath = "/leaderboard",
}: {
  entries: readonly LeaderboardEntry[];
  metricDefinitions: readonly MetricDefinition[];
  releaseId: string;
  language?: string;
  sort?: string;
  direction?: "asc" | "desc";
  basePath?: string;
}) {
  const metricIds = [...new Set(entries.flatMap((entry) => entry.metrics.map((row) => row.metric_id)))];
  const sortId = sort && metricIds.includes(sort) ? sort : undefined;
  const metricOrder = new Map(metricDefinitions.map((definition, index) => [definition.metric_id, index]));
  metricIds.sort((left, right) => (metricOrder.get(left) ?? Number.MAX_SAFE_INTEGER) - (metricOrder.get(right) ?? Number.MAX_SAFE_INTEGER));
  const selectedDirection = direction ?? "desc";
  const metrics = metricIds.map((metricId) => ({
    id: metricId,
    definition: metricDefinitions.find((definition) => definition.metric_id === metricId),
    label:
      metricDefinitions.find((definition) => definition.metric_id === metricId)?.label ??
      entries.flatMap((entry) => entry.metrics).find((metric) => metric.metric_id === metricId)?.label ??
      metricId,
  }));
  const showRank = entries.some((entry) => entry.rank !== null);

  return (
    <KeyboardScrollRegion className="table-wrap" label="Scrollable published configuration metrics">
      <p className="table-scroll-hint">
        If columns extend beyond the page, scroll this region horizontally to view the remaining metrics. Tab to the region and use the left and right arrow keys, or scroll with touch or pointer.
      </p>
      <table className="data-table">
        <caption className="sr-only">
          Published configuration metrics, missingness states, and coverage. Metric values link to their source scorecard.
        </caption>
        <thead>
          <tr>
            <th scope="col">Configuration</th>
            {showRank ? <th scope="col">Rank</th> : null}
            {metrics.map((metric) => {
              const active = sortId === metric.id;
              const nextDirection = active
                ? selectedDirection === "desc" ? "asc" : "desc"
                : metric.definition?.direction === "lower" ? "asc" : "desc";
              const query = asUrlQuery({
                release: releaseId,
                language,
                sort_metric: metric.id,
                direction: nextDirection,
              });
              return (
                <th
                  key={metric.id}
                  scope="col"
                  aria-sort={active ? (selectedDirection === "asc" ? "ascending" : "descending") : "none"}
                >
                  <Link className="sort-link" href={`${basePath}${query}`}>
                    {metric.label}{active ? <span aria-hidden="true"> {selectedDirection === "asc" ? "↑" : "↓"}</span> : null}
                    <span className="sr-only">{active ? `, sorted ${selectedDirection}` : ", sort by this metric"}</span>
                  </Link>
                </th>
              );
            })}
            <th scope="col">Coverage</th>
            <th scope="col">Generation cost</th>
            <th scope="col">Generation latency</th>
            <th scope="col">Mode / budget</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => (
            <tr key={entry.model_config_id}>
              <th scope="row">
                <Link className="model-link" href={`/models/${encodeURIComponent(entry.model_config_id)}${asUrlQuery({ release: releaseId })}`}>
                  {entry.label}
                </Link>
                <span className="model-id">{entry.model_config_id}</span>
                <span className="language-links">
                  {entry.languages.map((id) => (
                    <Link key={id} href={`/languages/${encodeURIComponent(id)}${asUrlQuery({ release: releaseId })}`}>
                      {id}
                    </Link>
                  ))}
                </span>
              </th>
              {showRank ? (
                <td>
                  <a className="detail-value" href={scoreEvidenceHref(entry.evidence_url, releaseId)}>
                    {entry.rank === null ? "Not ranked" : entry.rank}
                  </a>
                </td>
              ) : null}
              {metrics.map((metric) => {
                const value = entry.metrics.find((item) => item.metric_id === metric.id);
                return (
                  <td key={metric.id}>
                    {value ? (
                      <MetricValue metric={value} sourceUrl={scoreEvidenceHref(entry.evidence_url, releaseId)} compact />
                    ) : <span className="unreported" aria-label={`${metric.label}: not reported`}>Not reported</span>}
                  </td>
                );
              })}
              <td><CoverageBadge coverage={entry.coverage} sourceUrl={scoreEvidenceHref(entry.evidence_url, releaseId)} /></td>
              <td>
                <a className="detail-value" href={scoreEvidenceHref(entry.evidence_url, releaseId)}>
                  {entry.generation_cost_micros === null ? "Not reported" : formatMicros(entry.generation_cost_micros)}
                </a>
              </td>
              <td>
                <a className="detail-value" href={scoreEvidenceHref(entry.evidence_url, releaseId)}>
                  {entry.latency_ms_p50 === null && entry.latency_ms_p95 === null
                    ? "Not reported"
                    : `p50 ${entry.latency_ms_p50 ?? "—"} ms · p95 ${entry.latency_ms_p95 ?? "—"} ms`}
                </a>
              </td>
              <td>
                <a className="mode-budget" href={scoreEvidenceHref(entry.evidence_url, releaseId)}>
                  <span>{entry.run_mode ?? "Mode not disclosed"}</span>
                  <span>{entry.budget_profile_id ?? "Budget not disclosed"}</span>
                </a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </KeyboardScrollRegion>
  );
}

export function LanguageFilter({
  languages,
  releaseId,
  selected,
  sort,
  direction,
}: {
  languages: readonly string[];
  releaseId: string;
  selected?: string;
  sort?: string;
  direction?: string;
}) {
  return (
    <form id="language-filter" className="filter-form" action="/leaderboard" method="get">
      <input type="hidden" name="release" value={releaseId} />
      {sort ? <input type="hidden" name="sort_metric" value={sort} /> : null}
      {direction ? <input type="hidden" name="direction" value={direction} /> : null}
      <label htmlFor="language-select">Language coverage</label>
      <select id="language-select" name="language" defaultValue={selected ?? ""}>
        <option value="">All declared languages</option>
        {languages.map((id) => <option key={id} value={id}>{id}</option>)}
      </select>
      <button type="submit">Apply filter</button>
      {selected ? <Link href={`/leaderboard${asUrlQuery({ release: releaseId })}`}>Clear language filter</Link> : null}
    </form>
  );
}

export function ScopeFacts({ release, entry }: { release: ReleaseSummary; entry?: LeaderboardEntry }) {
  return (
    <div className="scope-facts">
      <span><b>Release scope</b> {release.scope.replaceAll("_", " ")}</span>
      {entry ? <span><b>Run mode</b> {entry.run_mode ?? "Not disclosed"}</span> : null}
      {entry ? <span><b>Budget profile</b> {entry.budget_profile_id ?? "Not disclosed"}</span> : null}
    </div>
  );
}

export function StatLink({ label, value, href, detail }: { label: string; value: string; href: string; detail?: string }) {
  return (
    <a className="stat-card" href={href}>
      <span>{label}</span>
      <strong>{value}</strong>
      {detail ? <small>{detail}</small> : null}
    </a>
  );
}

export function SectionHeading({ title, description, id }: { title: string; description?: string; id?: string }) {
  return (
    <div className="section-heading" id={id}>
      <h2>{title}</h2>
      {description ? <p>{description}</p> : null}
    </div>
  );
}

export function formatMicros(value: string): string {
  if (!/^(0|[1-9][0-9]*)$/.test(value)) return "Not reported";
  const micros = BigInt(value);
  const whole = micros / 1_000_000n;
  const remainder = String(micros % 1_000_000n).padStart(6, "0");
  return `$${whole}.${remainder}`;
}

export function ReleaseMethodologyLink({ release }: { release: ReleaseSummary }) {
  return (
    <Link
      className="methodology-link"
      href={`/methodology/${encodeURIComponent(release.methodology_version)}${asUrlQuery({ release: release.release_id })}`}
    >
      Frozen methodology · {release.methodology_version}
    </Link>
  );
}
