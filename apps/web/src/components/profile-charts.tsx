import type {
  DimensionBreakdown,
  LanguageEntryProfile,
  MetricDefinition,
} from "@/lib/public-api";
import { scoreEvidenceHref } from "@/lib/public-api";
import { MetricValue, SectionHeading } from "@/components/public-ui";

export function CodeRadar({
  dimensions,
  definitions,
  evidenceUrl,
  releaseId,
  id = "code-radar",
}: {
  dimensions: readonly DimensionBreakdown[];
  definitions: readonly MetricDefinition[];
  evidenceUrl: string;
  releaseId: string;
  id?: string;
}) {
  const width = 560;
  const height = 330;
  const centerX = width / 2;
  const centerY = 145;
  const radius = 102;
  const definitionById = new Map(definitions.map((definition) => [definition.metric_id, definition]));
  const axisCount = Math.max(dimensions.length, 1);
  const points = dimensions.map((dimension, index) => {
    const angle = -Math.PI / 2 + (2 * Math.PI * index) / axisCount;
    const definition = definitionById.get(dimension.metric.metric_id);
    const value = dimension.metric.status === "measured" ? Number(dimension.metric.value) : null;
    const low = definition ? Number(definition.domain[0]) : Number.NaN;
    const high = definition ? Number(definition.domain[1]) : Number.NaN;
    const valid = value !== null && Number.isFinite(value) && high > low && value >= low && value <= high;
    const ratio = valid ? (value! - low) / (high - low) : null;
    return {
      dimension,
      angle,
      x: centerX + Math.cos(angle) * radius * (ratio ?? 0),
      y: centerY + Math.sin(angle) * radius * (ratio ?? 0),
      plotted: valid,
    };
  });

  if (!dimensions.length) {
    return (
      <div className="chart-empty">
        <span className="state-kicker">No code dimensions</span>
        <p>No generated-code dimensions were published for this configuration.</p>
      </div>
    );
  }

  return (
    <div className="chart-panel">
      <div className="chart-title-row">
        <div>
          <span className="chart-kicker">Code-only profile</span>
          <h3>Dimension radar</h3>
        </div>
        <p>Points show measured values on each API-declared metric domain. No polygon fills missing dimensions.</p>
      </div>
      <div className="radar-scroll" role="region" aria-label="Scrollable code-only dimension radar" tabIndex={0}>
        <svg
          className="radar-svg"
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-labelledby={`${id}-title ${id}-desc`}
        >
          <title id={`${id}-title`}>Code-only dimension radar</title>
          <desc id={`${id}-desc`}>
            Measured score points use the metric domains published by this release. Unmeasured states appear in the table below.
          </desc>
          {[0.25, 0.5, 0.75, 1].map((fraction) => (
            <circle
              key={fraction}
              className="radar-ring"
              cx={centerX}
              cy={centerY}
              r={radius * fraction}
            />
          ))}
          {points.map((point) => {
            const labelRadius = radius + 28;
            const labelX = centerX + Math.cos(point.angle) * labelRadius;
            const labelY = centerY + Math.sin(point.angle) * labelRadius;
            return (
              <g key={point.dimension.dimension}>
                <line
                  className="radar-axis"
                  x1={centerX}
                  y1={centerY}
                  x2={centerX + Math.cos(point.angle) * radius}
                  y2={centerY + Math.sin(point.angle) * radius}
                />
                <text
                  className="radar-label"
                  x={labelX}
                  y={labelY}
                  textAnchor={Math.cos(point.angle) > 0.25 ? "start" : Math.cos(point.angle) < -0.25 ? "end" : "middle"}
                  dominantBaseline="middle"
                >
                  {point.dimension.dimension.replaceAll("_", " ")}
                </text>
                {point.plotted ? (
                  <circle className="radar-point" cx={point.x} cy={point.y} r="5">
                    <title>{`${point.dimension.dimension}: ${point.dimension.metric.value}`}</title>
                  </circle>
                ) : null}
              </g>
            );
          })}
        </svg>
      </div>
      <DimensionTable
        dimensions={dimensions}
        sourceUrl={evidenceUrl}
        releaseId={releaseId}
        caption="Code dimensions and their source score evidence"
      />
    </div>
  );
}

export function LanguageHeatmap({
  profiles,
  releaseId,
}: {
  profiles: readonly LanguageEntryProfile[];
  releaseId: string;
}) {
  const languages = [...new Set(profiles.map((profile) => profile.language_id))];
  const dimensions = [...new Set(profiles.flatMap((profile) => profile.dimensions.map((row) => row.dimension)))];
  if (!profiles.length || !dimensions.length) {
    return (
      <div className="chart-empty">
        <span className="state-kicker">No language dimension matrix</span>
        <p>Language-specific code dimensions were not published for this configuration.</p>
      </div>
    );
  }
  const lookup = new Map<string, DimensionBreakdown>();
  for (const profile of profiles) {
    for (const row of profile.dimensions) lookup.set(`${profile.language_id}\u0000${row.dimension}`, row);
  }
  return (
    <div className="table-wrap" role="region" aria-label="Language by dimension coverage" tabIndex={0}>
      <table className="data-table heatmap-table">
        <caption className="sr-only">
          Language by dimension source metrics. A blank cell means this release published no language-specific measurement for that combination.
        </caption>
        <thead>
          <tr><th scope="col">Language</th>{dimensions.map((dimension) => <th key={dimension} scope="col">{dimension.replaceAll("_", " ")}</th>)}</tr>
        </thead>
        <tbody>
          {languages.map((language) => {
            const profile = profiles.find((item) => item.language_id === language)!;
            return (
              <tr key={language}>
                <th scope="row">{language}</th>
                {dimensions.map((dimension) => {
                  const metric = lookup.get(`${language}\u0000${dimension}`);
                  return (
                    <td key={dimension}>
                      {metric ? (
                        <MetricValue metric={metric.metric} sourceUrl={scoreEvidenceHref(profile.evidence_url, releaseId)} compact />
                      ) : <span className="unreported">Not tested</span>}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function DimensionTable({
  dimensions,
  sourceUrl,
  releaseId,
  caption,
}: {
  dimensions: readonly DimensionBreakdown[];
  sourceUrl: string;
  releaseId: string;
  caption: string;
}) {
  return (
    <div className="table-wrap" role="region" aria-label={caption} tabIndex={0}>
      <table className="data-table dimension-table">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Dimension / diagnostic</th>
            <th scope="col">Published metric</th>
            <th scope="col">Applicable tasks</th>
            <th scope="col">Opportunities</th>
          </tr>
        </thead>
        <tbody>
          {dimensions.map((row) => (
            <tr key={`${row.dimension}:${row.metric.metric_id}`}>
              <th scope="row">{row.dimension.replaceAll("_", " ")}</th>
              <td><MetricValue metric={row.metric} sourceUrl={scoreEvidenceHref(sourceUrl, releaseId)} compact /></td>
              <td><a href={scoreEvidenceHref(sourceUrl, releaseId)}>{row.applicable_tasks}</a></td>
              <td><a href={scoreEvidenceHref(sourceUrl, releaseId)}>{row.opportunity_count}</a></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ProfileSectionTitle({ title, detail }: { title: string; detail: string }) {
  return <SectionHeading title={title} description={detail} />;
}
