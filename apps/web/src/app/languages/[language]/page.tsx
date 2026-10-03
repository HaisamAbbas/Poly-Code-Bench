import type { Metadata } from "next";
import Link from "next/link";
import { CodeRadar, DimensionTable } from "@/components/profile-charts";
import {
  EmptyState,
  PageIntro,
  ReleaseNotice,
  ReleaseSelector,
  ResourceState,
  SectionHeading,
  SortableLeaderboard,
} from "@/components/public-ui";
import {
  combineResources,
  loadReleaseContext,
  orderedEntries,
  publicApi,
  scoreEvidenceHref,
  type ApiEnvelope,
  type LanguageProfile,
  type LeaderboardEntry,
  type MetricDefinition,
  type ReleaseSummary,
} from "@/lib/public-api";

export const dynamic = "force-dynamic";

type Search = Promise<Record<string, string | string[] | undefined>>;

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

export async function generateMetadata({ params }: { params: Promise<{ language: string }> }): Promise<Metadata> {
  const { language } = await params;
  return {
    title: `${language} leaderboard`,
    description: `Language-specific measurements and tool coverage for ${language} in a selected public release.`,
  };
}

export default async function LanguagePage({
  params,
  searchParams,
}: {
  params: Promise<{ language: string }>;
  searchParams: Search;
}) {
  const [{ language }, query] = await Promise.all([params, searchParams]);
  const requestedRelease = first(query.release);
  const sort = first(query.sort_metric);
  const directionParam = first(query.direction);
  const direction = directionParam === "asc" || directionParam === "desc" ? directionParam : undefined;
  const context = await loadReleaseContext(requestedRelease);

  return (
    <>
      <Link className="back-link" href={context.state === "ready" ? `/leaderboard?release=${encodeURIComponent(context.value.releaseId)}` : "/leaderboard"}>
        ← All configurations
      </Link>
      <PageIntro
        eyebrow="Language leaderboard · Release backed"
        title={language}
        description="This view uses only language-specific dimensions and diagnostics declared by the release. Untested features have no plotted score."
      />
      <ResourceState resource={context}>
        {(releaseContext) => <LanguageForRelease
          language={language}
          releaseId={releaseContext.releaseId}
          releaseSummary={releaseContext.summary}
          releases={releaseContext.releases}
          sort={sort}
          direction={direction}
        />}
      </ResourceState>
    </>
  );
}

async function LanguageForRelease({
  language,
  releaseId,
  releaseSummary,
  releases,
  sort,
  direction,
}: {
  language: string;
  releaseId: string;
  releaseSummary: ReleaseSummary;
  releases: readonly ReleaseSummary[];
  sort?: string;
  direction?: "asc" | "desc";
}) {
  const [profileResource, boardResource] = await Promise.all([
    publicApi<ApiEnvelope<LanguageProfile>>(
      `/languages/${encodeURIComponent(language)}?release=${encodeURIComponent(releaseId)}`,
    ),
    publicApi<ApiEnvelope<readonly LeaderboardEntry[]>>(
      `/leaderboard?release=${encodeURIComponent(releaseId)}&language=${encodeURIComponent(language)}&limit=200`,
    ),
  ]);
  const result = combineResources(profileResource, boardResource);

  return (
    <ResourceState resource={result}>
      {([profileResponse, boardResponse]) => {
        const profile = profileResponse.data;
        const entries = boardResponse.data;
        const metricIds = new Set(entries.flatMap((entry) => entry.metrics.map((metric) => metric.metric_id)));
        const sortId = sort && metricIds.has(sort) ? sort : undefined;
        const rows = orderedEntries(entries, sortId, direction ?? "desc");
        const definitions: readonly MetricDefinition[] = boardResponse.meta.registry?.definitions ?? [];

        return (
          <>
            <div className="page-toolbar">
              <ReleaseSelector
                releases={releases}
                releaseId={releaseId}
                action={`/languages/${encodeURIComponent(language)}`}
                preserved={{ sort_metric: sort, direction }}
              />
              <div className="scope-facts">
                <span><b>Configurations</b>{profile.entries.length}</span>
                <span><b>Release scope</b>{releaseSummary.scope.replaceAll("_", " ")}</span>
              </div>
            </div>
            <ReleaseNotice release={releaseSummary} />
            <section className="section-card" aria-labelledby="language-board-title">
              <SectionHeading
                id="language-board-title"
                title={`${language} leaderboard`}
                description="Each metric links to its published scorecard. Rows retain their own coverage and run limits."
              />
              {rows.length ? (
                <SortableLeaderboard
                  entries={rows}
                  metricDefinitions={definitions}
                  releaseId={releaseId}
                  language={language}
                  sort={sortId}
                  direction={direction}
                  basePath={`/languages/${encodeURIComponent(language)}`}
                />
              ) : (
                <EmptyState title={`No published configurations cover ${language}`}>
                  The release does not include rows for this language. No language result was inferred.
                </EmptyState>
              )}
            </section>
            <section className="section-card" aria-labelledby="language-diagnostics-title">
              <SectionHeading
                id="language-diagnostics-title"
                title="Diagnostic profiles"
                description="Opportunity counts and tool coverage are shown per configuration, so one model's scans do not stand in for another's."
              />
              {profile.entries.length ? (
                <div className="profile-stack">
                  {profile.entries.map((entry, index) => (
                    <article className="profile-card" key={entry.model_config_id}>
                      <div className="profile-card-heading">
                        <div>
                          <h3><Link href={`/models/${encodeURIComponent(entry.model_config_id)}?release=${encodeURIComponent(releaseId)}`}>{entry.label}</Link></h3>
                          <p>{entry.model_config_id} · {language}</p>
                        </div>
                      </div>
                      <div className="profile-grid">
                        <div>
                          <h3 className="subsection-title">Language-specific dimensions</h3>
                          {entry.dimensions.length ? (
                            <CodeRadar
                              id={`language-radar-${index}`}
                              dimensions={entry.dimensions}
                              definitions={definitions}
                              evidenceUrl={entry.evidence_url}
                              releaseId={releaseId}
                            />
                          ) : (
                            <p className="plain-note">No language-specific code dimensions were published for this configuration.</p>
                          )}
                        </div>
                        <div>
                          <h3 className="subsection-title">Diagnostics</h3>
                          {entry.diagnostics.length ? (
                            <DimensionTable
                              dimensions={entry.diagnostics}
                              sourceUrl={entry.evidence_url}
                              releaseId={releaseId}
                              caption={`${entry.label} diagnostic measurements and opportunities`}
                            />
                          ) : (
                            <p className="plain-note">No diagnostic measurements were published for this configuration.</p>
                          )}
                        </div>
                      </div>
                      <div className="tool-coverage language-tool-coverage">
                        <h4>Tool coverage</h4>
                        {entry.tool_coverage.length ? (
                          <ul>
                            {entry.tool_coverage.map(([tool, coverage]) => (
                              <li key={tool}>
                                <span>{tool}</span>
                                <a href={scoreEvidenceHref(entry.evidence_url, releaseId)}>{coverage}</a>
                              </li>
                            ))}
                          </ul>
                        ) : <p className="plain-note">No tool coverage was disclosed.</p>}
                      </div>
                    </article>
                  ))}
                </div>
              ) : (
                <EmptyState title="No language profiles were published">The release includes no configuration-specific profile rows for this language.</EmptyState>
              )}
            </section>
          </>
        );
      }}
    </ResourceState>
  );
}
