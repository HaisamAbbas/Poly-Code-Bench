import type { Metadata } from "next";
import Link from "next/link";
import { CodeRadar, LanguageHeatmap } from "@/components/profile-charts";
import {
  EmptyState,
  MetricValue,
  PageIntro,
  ReleaseNotice,
  ReleaseSelector,
  ResourceState,
  SectionHeading,
  StatLink,
  formatMicros,
} from "@/components/public-ui";
import {
  loadReleaseContext,
  publicApi,
  scoreEvidenceHref,
  type ApiEnvelope,
  type MetricDefinition,
  type ModelProfile,
  type ReleaseSummary,
} from "@/lib/public-api";

export const dynamic = "force-dynamic";

type Search = Promise<Record<string, string | string[] | undefined>>;

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

export async function generateMetadata({ params }: { params: Promise<{ modelConfigId: string }> }): Promise<Metadata> {
  const { modelConfigId } = await params;
  return { title: "Model profile", description: `Release-backed profile for ${modelConfigId}.` };
}

export default async function ModelPage({
  params,
  searchParams,
}: {
  params: Promise<{ modelConfigId: string }>;
  searchParams: Search;
}) {
  const [{ modelConfigId }, query] = await Promise.all([params, searchParams]);
  const context = await loadReleaseContext(first(query.release));

  return (
    <>
      <Link className="back-link" href={context.state === "ready" ? `/leaderboard?release=${encodeURIComponent(context.value.releaseId)}` : "/leaderboard"}>
        ← Back to leaderboard
      </Link>
      <PageIntro
        eyebrow="Model configuration · Release backed"
        title="Model profile"
        description="Published measurements for one configuration. Scores, coverage, cost, and latency link to the release's source scorecard."
      />
      <ResourceState resource={context}>
        {(releaseContext) => <ModelForRelease
          modelConfigId={modelConfigId}
          releaseId={releaseContext.releaseId}
          releaseSummary={releaseContext.summary}
          releases={releaseContext.releases}
        />}
      </ResourceState>
    </>
  );
}

async function ModelForRelease({
  modelConfigId,
  releaseId,
  releaseSummary,
  releases,
}: {
  modelConfigId: string;
  releaseId: string;
  releaseSummary: ReleaseSummary;
  releases: readonly ReleaseSummary[];
}) {
  const result = await publicApi<ApiEnvelope<ModelProfile>>(
    `/models/${encodeURIComponent(modelConfigId)}?release=${encodeURIComponent(releaseId)}`,
  );

  return (
    <ResourceState resource={result}>
      {(response) => {
        const profile = response.data;
        const definitions: readonly MetricDefinition[] = response.meta.registry?.definitions ?? [];
        const evidenceUrl = scoreEvidenceHref(profile.evidence_url, releaseId);
        const measured = profile.dimensions.filter((row) => row.metric.status === "measured" && row.metric.value !== null);
        const summaryMetric = measured.reduce<(typeof measured)[number] | undefined>((current, row) => {
          if (!current) return row;
          const direction = definitions.find((definition) => definition.metric_id === row.metric.metric_id)?.direction ?? row.metric.direction;
          const better = direction === "lower"
            ? Number(row.metric.value) < Number(current.metric.value)
            : Number(row.metric.value) > Number(current.metric.value);
          return better ? row : current;
        }, undefined);
        const summaryDirection = summaryMetric
          ? definitions.find((definition) => definition.metric_id === summaryMetric.metric.metric_id)?.direction ?? summaryMetric.metric.direction
          : "higher";

        return (
          <>
            <div className="page-toolbar">
              <ReleaseSelector
                releases={releases}
                releaseId={releaseId}
                action={`/models/${encodeURIComponent(modelConfigId)}`}
              />
              <div className="scope-facts">
                <span><b>Release scope</b>{releaseSummary.scope.replaceAll("_", " ")}</span>
                <span><b>Run mode</b>{profile.run_mode ?? "Not disclosed"}</span>
                <span><b>Budget profile</b>{profile.budget_profile_id ?? "Not disclosed"}</span>
              </div>
            </div>
            <ReleaseNotice release={releaseSummary} />
            <div className="profile-topline">
              <div>
                <p className="eyebrow">{profile.model_config_id}</p>
                <h2>{profile.label}</h2>
                <p className="model-capabilities">
                  Published capabilities: {profile.capabilities.length ? profile.capabilities.join(", ") : "not disclosed"}
                </p>
              </div>
              <div className="language-index" aria-label="Languages declared by this configuration">
                {profile.languages.map((language) => (
                  <Link key={language} href={`/languages/${encodeURIComponent(language)}?release=${encodeURIComponent(releaseId)}`}>
                    {language}
                  </Link>
                ))}
              </div>
            </div>
            <div className="profile-stats" aria-label="Generation and coverage metrics">
              <StatLink
                label="Generation cost"
                value={profile.generation_cost_micros === null ? "Not reported" : formatMicros(profile.generation_cost_micros)}
                href={evidenceUrl}
                detail="Source scorecard"
              />
              <StatLink
                label="Generation latency p50"
                value={profile.latency_ms_p50 === null ? "Not reported" : `${profile.latency_ms_p50.toLocaleString()} ms`}
                href={evidenceUrl}
                detail="Source scorecard"
              />
              <StatLink
                label="Generation latency p95"
                value={profile.latency_ms_p95 === null ? "Not reported" : `${profile.latency_ms_p95.toLocaleString()} ms`}
                href={evidenceUrl}
                detail="Source scorecard"
              />
              <StatLink
                label="Task coverage"
                value={profile.coverage === null ? "Not reported" : String(profile.coverage.tasks)}
                href={evidenceUrl}
                detail={profile.coverage ? `${profile.coverage.samples} samples · ${profile.coverage.independent_clusters} clusters` : "Source scorecard"}
              />
            </div>

            <section className="section-card" aria-labelledby="model-summary-title">
              <SectionHeading
                id="model-summary-title"
                title="Published metric summary"
                description="These statements describe reported values and coverage only; they do not infer causes or unsupported capabilities."
              />
              {summaryMetric ? (
                <div className="fact-summary">
                  <h2>{summaryDirection === "lower" ? "Lowest measured code dimension" : "Highest measured code dimension"}</h2>
                  <p>
                    {summaryMetric.dimension.replaceAll("_", " ")} is reported at {summaryMetric.metric.value} {summaryMetric.metric.unit}, with {summaryMetric.opportunity_count} opportunities across {summaryMetric.applicable_tasks} applicable tasks.
                  </p>
                </div>
              ) : (
                <div className="fact-summary">
                  <h2>No measured code dimensions</h2>
                  <p>This release publishes no measured generated-code dimension for this configuration. No code score is inferred from answer-only metrics.</p>
                </div>
              )}
              {profile.metrics.length ? (
                <div className="metric-summary-grid">
                  {profile.metrics.map((metric) => (
                    <div className="metric-summary-card" key={metric.metric_id}>
                      <span>{metric.label}</span>
                      <MetricValue metric={metric} sourceUrl={evidenceUrl} />
                    </div>
                  ))}
                </div>
              ) : <EmptyState title="No profile metrics">The release published no metrics for this configuration.</EmptyState>}
            </section>

            <section className="section-card" aria-labelledby="model-code-title">
              <SectionHeading
                id="model-code-title"
                title="Code-only profile"
                description="The radar includes only dimensions present in this release. Answer-only tasks do not receive generated-code dimensions."
              />
              <CodeRadar
                dimensions={profile.dimensions}
                definitions={definitions}
                evidenceUrl={profile.evidence_url}
                releaseId={releaseId}
              />
            </section>

            <section className="section-card" aria-labelledby="model-language-title">
              <SectionHeading
                id="model-language-title"
                title="Language × dimension coverage"
                description="Only explicitly published language-specific dimensions appear. A blank cell means the release did not publish that language/dimension pair."
              />
              {profile.language_profiles.length ? (
                <LanguageHeatmap profiles={profile.language_profiles} releaseId={releaseId} />
              ) : (
                <EmptyState title="No language-specific dimensions">
                  Language names are declared, but this release includes no language-specific code dimensions for this configuration.
                </EmptyState>
              )}
            </section>
          </>
        );
      }}
    </ResourceState>
  );
}
