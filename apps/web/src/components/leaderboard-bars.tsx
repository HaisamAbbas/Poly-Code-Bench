import Link from "next/link";
import {
  asUrlQuery,
  metricFor,
  orderedEntries,
  scoreEvidenceHref,
  type LeaderboardEntry,
  type MetricDefinition,
} from "@/lib/public-api";
import { MetricValue } from "@/components/public-ui";

function numericDomain(definition: MetricDefinition): readonly [number, number] | null {
  const low = Number(definition.domain[0]);
  const high = Number(definition.domain[1]);
  return Number.isFinite(low) && Number.isFinite(high) && high > low ? [low, high] : null;
}

export function LeaderboardBars({
  entries,
  definitions,
  releaseId,
  language,
  searchQuery,
  selectedMetricId,
  direction,
}: {
  entries: readonly LeaderboardEntry[];
  definitions: readonly MetricDefinition[];
  releaseId: string;
  language?: string;
  searchQuery?: string;
  selectedMetricId?: string;
  direction?: "asc" | "desc";
}) {
  const available = definitions.filter((definition) =>
    numericDomain(definition) && entries.some((entry) => {
      const metric = metricFor(entry, definition.metric_id);
      const value = metric?.value === null || !metric ? Number.NaN : Number(metric.value);
      const domain = numericDomain(definition);
      return metric?.status === "measured" && domain !== null && Number.isFinite(value) && value >= domain[0] && value <= domain[1];
    }),
  );
  const selected = available.find((definition) => definition.metric_id === selectedMetricId) ?? available[0];

  if (!selected) {
    return (
      <div className="leaderboard-bars-empty">
        <h3>No declared numeric metric scale</h3>
        <p>This release has no measured values within a published metric domain to draw as bars. Use the table view for the reported states.</p>
      </div>
    );
  }

  const selectedDirection = selected.metric_id === selectedMetricId
    ? direction ?? (selected.direction === "lower" ? "asc" : "desc")
    : selected.direction === "lower" ? "asc" : "desc";
  const [low, high] = numericDomain(selected)!;
  const ordered = orderedEntries(entries, selected.metric_id, selectedDirection);
  const evidenceLabel = selected.direction === "lower" ? "Lower values are better" : "Higher values are better";

  return (
    <section className="leaderboard-bars" aria-labelledby="leaderboard-bars-title">
      <div className="leaderboard-bars-heading">
        <div>
          <h3 id="leaderboard-bars-title">Metric comparison</h3>
          <p>
            Bars show values on the release-declared scale ({low}–{high} {selected.unit}); {evidenceLabel.toLowerCase()}.
            Exact values and unmeasured states remain visible.
          </p>
        </div>
        <form className="leaderboard-bars-controls" action="/leaderboard" method="get">
          <input type="hidden" name="release" value={releaseId} />
          <input type="hidden" name="view" value="bars" />
          {language ? <input type="hidden" name="language" value={language} /> : null}
          {searchQuery ? <input type="hidden" name="q" value={searchQuery} /> : null}
          <label htmlFor="leaderboard-bar-metric">Metric</label>
          <select id="leaderboard-bar-metric" name="sort_metric" defaultValue={selected.metric_id}>
            {available.map((definition) => (
              <option key={definition.metric_id} value={definition.metric_id}>
                {definition.label} · {definition.unit}
              </option>
            ))}
          </select>
          <label htmlFor="leaderboard-bar-direction">Order</label>
          <select id="leaderboard-bar-direction" name="direction" defaultValue={selectedDirection}>
            <option value="desc">Descending values</option>
            <option value="asc">Ascending values</option>
          </select>
          <button type="submit">Update bars</button>
        </form>
      </div>

      <div className="leaderboard-bars-axis" aria-hidden="true">
        <span>Configuration</span>
        <span className="leaderboard-bars-axis-scale"><span>{low}</span><span>{high}</span></span>
        <span className="leaderboard-bars-axis-value">{selected.label}</span>
      </div>
      <ol className="leaderboard-bars-list" aria-label={`${selected.label} values, ${selectedDirection === "asc" ? "ascending" : "descending"}`}>
        {ordered.map((entry) => {
          const metric = metricFor(entry, selected.metric_id);
          const value = metric?.status === "measured" && metric.value !== null ? Number(metric.value) : Number.NaN;
          const inDomain = Number.isFinite(value) && value >= low && value <= high;
          const percentage = inDomain ? ((value - low) / (high - low)) * 100 : null;
          const profileHref = `/models/${encodeURIComponent(entry.model_config_id)}${asUrlQuery({ release: releaseId })}`;
          const valueLabel = metric?.value ?? metric?.status.replaceAll("_", " ") ?? "not reported";

          return (
            <li className="leaderboard-bars-row" key={entry.model_config_id}>
              <div className="leaderboard-bars-model">
                <Link className="model-link" href={profileHref}>{entry.label}</Link>
                <span className="model-id">{entry.model_config_id}</span>
              </div>
              <div className="leaderboard-bars-track-cell">
                {percentage !== null ? (
                  <Link
                    className="leaderboard-bars-hit-area"
                    href={profileHref}
                    aria-label={`Open ${entry.label} profile. ${selected.label}: ${metric?.value} ${selected.unit}. Published scale ${low} to ${high}. ${evidenceLabel}.`}
                    title={`${entry.label}: ${metric?.value} ${selected.unit}`}
                  >
                    <span className="leaderboard-bars-track" aria-hidden="true">
                      <span className="leaderboard-bars-fill" style={{ width: `${percentage}%` }} />
                    </span>
                  </Link>
                ) : (
                  <span className="leaderboard-bars-unavailable">
                    {metric && metric.status !== "measured" ? metric.status.replaceAll("_", " ") : metric ? "Outside declared scale" : "Not reported"}
                  </span>
                )}
              </div>
              <div className="leaderboard-bars-value">
                {metric ? (
                  <MetricValue metric={metric} sourceUrl={scoreEvidenceHref(entry.evidence_url, releaseId)} compact />
                ) : (
                  <span className="unreported" aria-label={`${selected.label}: not reported`}>{valueLabel}</span>
                )}
              </div>
            </li>
          );
        })}
      </ol>
      <p className="leaderboard-bars-note">
        Bar length encodes the raw metric value within its declared range; it does not compare different metrics or estimate missing results. Select a bar or configuration name to open its profile.
      </p>
    </section>
  );
}
