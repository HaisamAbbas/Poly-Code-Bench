import Link from "next/link";
import {
  asUrlQuery,
  metricFor,
  orderedEntries,
  scoreEvidenceHref,
  type LeaderboardEntry,
  type MetricDefinition,
} from "@/lib/public-api";
import { metricDomain, metricScalePosition } from "@/lib/metric-scale";
import { KeyboardScrollRegion } from "@/components/keyboard-scroll-region";

function displayMetricValue(value: string, unit: string): string {
  const numeric = Number(value);
  return unit === "score" && Number.isFinite(numeric) ? numeric.toFixed(2) : value;
}

function statusLabel(status: string): string {
  return status.replaceAll("_", " ");
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
    metricDomain(definition) && entries.some((entry) => metricScalePosition(metricFor(entry, definition.metric_id), definition)),
  );
  const selected = available.find((definition) => definition.metric_id === selectedMetricId) ?? available[0];

  if (!selected) {
    return (
      <div className="leaderboard-bars-empty">
        <h3>No declared numeric metric scale</h3>
        <p>This release has no measured values within a published metric domain to chart. Use the table view to inspect all reported states.</p>
      </div>
    );
  }

  const selectedDirection = selected.metric_id === selectedMetricId
    ? direction ?? (selected.direction === "lower" ? "asc" : "desc")
    : selected.direction === "lower" ? "asc" : "desc";
  const [low, high] = metricDomain(selected)!;
  const bestEndpoint = selected.direction === "lower" ? low : high;
  const worstEndpoint = selected.direction === "lower" ? high : low;
  const ordered = orderedEntries(entries, selected.metric_id, selectedDirection);
  const bestFirstDirection = selected.direction === "lower" ? "asc" : "desc";
  const axisOrder = selectedDirection === bestFirstDirection ? "Best first" : "Worst first";
  const evidenceLabel = selected.direction === "lower" ? "lower values are better" : "higher values are better";

  return (
    <section className="leaderboard-bars" aria-labelledby="leaderboard-bars-title">
      <div className="leaderboard-bars-heading">
        <div>
          <h3 id="leaderboard-bars-title">Metric comparison</h3>
          <p>
            Bar height follows this release’s metric direction, so taller means better. The scale spans {low}–{high} {selected.unit}; {evidenceLabel}.
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

      <div className="leaderboard-bars-axis-summary" aria-hidden="true">
        <span>Better <strong>{bestEndpoint} {selected.unit}</strong></span>
        <span>{axisOrder}</span>
        <span>Worse <strong>{worstEndpoint} {selected.unit}</strong></span>
      </div>
      <KeyboardScrollRegion
        className="leaderboard-bars-scroll"
        label={`${selected.label} metric chart; scroll horizontally to compare every configuration`}
      >
        <ol className="leaderboard-bars-columns" aria-label={`${selected.label} comparison, ${axisOrder.toLowerCase()}`}>
          {ordered.map((entry) => {
            const metric = metricFor(entry, selected.metric_id);
            const position = metricScalePosition(metric, selected);
            const profileHref = `/models/${encodeURIComponent(entry.model_config_id)}${asUrlQuery({ release: releaseId })}`;
            const evidenceHref = scoreEvidenceHref(entry.evidence_url, releaseId);
            const scoreLabel = metric?.status === "measured" && metric.value !== null
              ? displayMetricValue(metric.value, selected.unit)
              : null;
            const details = [
              scoreLabel ? `${scoreLabel} ${selected.unit}` : metric ? statusLabel(metric.status) : "Not reported",
              metric && metric.interval_low !== null && metric.interval_high !== null
                ? `reported interval ${metric.interval_low} to ${metric.interval_high}`
                : null,
              metric?.reason ?? null,
            ].filter(Boolean).join("; ");

            return (
              <li className="leaderboard-bars-column" key={entry.model_config_id}>
                <div className="leaderboard-bars-score-cell">
                  {position && scoreLabel ? (
                    <Link
                      className="leaderboard-bars-score"
                      href={evidenceHref}
                      title={details}
                      aria-label={`${entry.label}, ${selected.label}: ${details}. Open source score evidence.`}
                    >
                      {scoreLabel}
                    </Link>
                  ) : (
                    <span className="leaderboard-bars-unavailable" title={metric?.reason ?? undefined}>
                      {metric ? statusLabel(metric.status) : "Not reported"}
                    </span>
                  )}
                </div>
                <div className="leaderboard-bars-plot">
                  <div className="leaderboard-bars-grid" aria-hidden="true" />
                  {position && scoreLabel ? (
                    <Link
                      className="leaderboard-bars-hit-area"
                      href={profileHref}
                      aria-label={`Open ${entry.label} profile. ${selected.label}: ${details}. Taller bars indicate better performance on this release’s declared scale.`}
                      title={`Open ${entry.label} profile`}
                    >
                      <span
                        className="leaderboard-bars-fill"
                        aria-hidden="true"
                        style={{ height: `${position.performanceRatio * 100}%` }}
                      />
                    </Link>
                  ) : (
                    <span className="leaderboard-bars-empty-space" aria-hidden="true" />
                  )}
                </div>
                <div className="leaderboard-bars-model">
                  <Link className="model-link" href={profileHref}>{entry.label}</Link>
                  <span className="model-id">{entry.model_config_id}</span>
                </div>
              </li>
            );
          })}
        </ol>
      </KeyboardScrollRegion>
      <p className="leaderboard-bars-note">
        Each bar uses the selected metric’s published range and direction. Values link to score evidence; bars and configuration names open model profiles. Unmeasured configurations remain in the chart without a score bar.
      </p>
    </section>
  );
}
