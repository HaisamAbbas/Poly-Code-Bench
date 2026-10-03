import type { Metadata } from "next";
import {
  EmptyState,
  LanguageFilter,
  PageIntro,
  ReleaseNotice,
  ReleaseSelector,
  ResourceState,
  SectionHeading,
  SortableLeaderboard,
} from "@/components/public-ui";
import {
  loadReleaseContext,
  orderedEntries,
  publicApi,
  type ApiEnvelope,
  type LeaderboardEntry,
  type ReleaseSummary,
} from "@/lib/public-api";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Leaderboard",
  description: "Browse model configuration scores from versioned public releases.",
};

type Search = Promise<Record<string, string | string[] | undefined>>;

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

export default async function LeaderboardPage({ searchParams }: { searchParams: Search }) {
  const query = await searchParams;
  const requestedRelease = first(query.release);
  const selectedLanguage = first(query.language);
  const sort = first(query.sort_metric);
  const requestedDirection = first(query.direction);
  const direction = requestedDirection === "asc" ? "asc" : requestedDirection === "desc" ? "desc" : undefined;
  const releaseContext = await loadReleaseContext(requestedRelease);

  return (
    <>
      <PageIntro
        eyebrow="Public results · Release backed"
        title="Leaderboard"
        description="Compare disclosed model configurations inside one immutable release. Every score links to its published scorecard."
      />
      <ResourceState resource={releaseContext}>
        {(context) => <LeaderboardForRelease
          releaseId={context.releaseId}
          releaseSummary={context.summary}
          releases={context.releases}
          selectedLanguage={selectedLanguage}
          sort={sort}
          direction={direction}
        />}
      </ResourceState>
    </>
  );
}

async function LeaderboardForRelease({
  releaseId,
  releaseSummary,
  releases,
  selectedLanguage,
  sort,
  direction,
}: {
  releaseId: string;
  releaseSummary: ReleaseSummary;
  releases: readonly ReleaseSummary[];
  selectedLanguage?: string;
  sort?: string;
  direction?: "asc" | "desc";
}) {
  const result = await publicApi<ApiEnvelope<readonly LeaderboardEntry[]>>(
    `/leaderboard?release=${encodeURIComponent(releaseId)}&limit=200`,
  );
  if (result.state !== "ready") {
    return <ResourceState resource={result} />;
  }

  const allEntries = result.value.data;
  const languages = [...new Set(allEntries.flatMap((entry) => entry.languages))].sort();
  const metricIds = new Set(allEntries.flatMap((entry) => entry.metrics.map((metric) => metric.metric_id)));
  const filtered = selectedLanguage
    ? allEntries.filter((entry) => entry.languages.includes(selectedLanguage))
    : allEntries;
  const sortId = sort && metricIds.has(sort) ? sort : undefined;
  const rows = orderedEntries(filtered, sortId, direction ?? "desc");
  const definitions = result.value.meta.registry?.definitions ?? [];

  return (
    <>
      <div className="page-toolbar">
        <ReleaseSelector
          releases={releases}
          releaseId={releaseId}
          action="/leaderboard"
          preserved={{ language: selectedLanguage, sort_metric: sort, direction }}
        />
        <div className="scope-facts">
          <span><b>Release scope</b>{releaseSummary.scope.replaceAll("_", " ")}</span>
          <span><b>Configurations</b>{filtered.length}</span>
          <span><b>Coverage languages</b>{languages.length}</span>
          {selectedLanguage ? <span><b>Filter</b>{selectedLanguage}</span> : null}
        </div>
      </div>
      <ReleaseNotice release={releaseSummary} />
      <section className="section-card compare-launcher" aria-labelledby="compare-launcher-title">
        <div><h2 id="compare-launcher-title">Start a configuration comparison</h2><p>Select two to four configurations in this release. The comparison checks protocol, budget and exact public task scorecards.</p></div>
        <form action="/compare" method="get">
          <input type="hidden" name="release" value={releaseId} />
          <label htmlFor="compare-models-launcher">Configurations <span>(select 2–4)</span></label>
          <select id="compare-models-launcher" name="models" multiple size={Math.min(5, Math.max(3, allEntries.length))}>
            {allEntries.map((entry) => <option key={entry.model_config_id} value={entry.model_config_id}>{entry.label} · {entry.model_config_id}</option>)}
          </select>
          <button type="submit">Open comparison</button>
          <a href={`/compare?release=${encodeURIComponent(releaseId)}`}>Comparison filters and details</a>
        </form>
      </section>
      <div className="language-filter-row">
        <LanguageFilter
          languages={languages}
          releaseId={releaseId}
          selected={selectedLanguage}
          sort={sort}
          direction={direction}
        />
        <nav className="language-index" aria-label="Languages published in this release">
          {languages.map((language) => (
            <a key={language} href={`/languages/${encodeURIComponent(language)}?release=${encodeURIComponent(releaseId)}`}>
              {language}
            </a>
          ))}
        </nav>
      </div>
      <section className="section-card" aria-labelledby="leaderboard-table-title">
        <SectionHeading
          id="leaderboard-table-title"
          title={selectedLanguage ? `${selectedLanguage} configurations` : "Published configurations"}
          description="Sort by any published metric. Exploratory releases show no rank; missing and inapplicable values remain distinct."
        />
        {rows.length ? (
          <SortableLeaderboard
            entries={rows}
            metricDefinitions={definitions}
            releaseId={releaseId}
            language={selectedLanguage}
            sort={sortId}
            direction={direction}
          />
        ) : (
          <EmptyState title={selectedLanguage ? `No configurations cover ${selectedLanguage}` : "No configurations in this release"}>
            This release contains no public rows for the selected view. No score was inferred for an uncovered language.
          </EmptyState>
        )}
        {sortId ? <p className="sort-explainer">Sorted by {definitions.find((metric) => metric.metric_id === sortId)?.label ?? sortId}. The URL records this sort and release.</p> : null}
      </section>
    </>
  );
}
