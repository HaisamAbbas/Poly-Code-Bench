import type { Metadata } from "next";
import Link from "next/link";
import { LeaderboardBars } from "@/components/leaderboard-bars";
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
  asUrlQuery,
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
  description: "Compare model configuration results across published programming language coverage.",
};

type Search = Promise<Record<string, string | string[] | undefined>>;

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

export default async function LeaderboardPage({ searchParams }: { searchParams: Search }) {
  const query = await searchParams;
  const requestedRelease = first(query.release);
  const selectedLanguage = first(query.language);
  const searchQuery = first(query.q)?.trim() || undefined;
  const sort = first(query.sort_metric);
  const requestedDirection = first(query.direction);
  const direction = requestedDirection === "asc" || requestedDirection === "desc" ? requestedDirection : undefined;
  const view = first(query.view) === "bars" ? "bars" : "table";
  const releaseContext = await loadReleaseContext(requestedRelease);

  return (
    <>
      <PageIntro
        eyebrow="PolyCodeBench / Public results"
        title="Coding capability, by language."
        description="Compare disclosed model configurations inside one immutable release. Every published measure links to its source scorecard."
      />
      <ResourceState resource={releaseContext}>
        {(context) => (
          <LeaderboardForRelease
            releaseId={context.releaseId}
            releaseSummary={context.summary}
            releases={context.releases}
            selectedLanguage={selectedLanguage}
            searchQuery={searchQuery}
            sort={sort}
            direction={direction}
            view={view}
          />
        )}
      </ResourceState>
    </>
  );
}

async function LeaderboardForRelease({
  releaseId,
  releaseSummary,
  releases,
  selectedLanguage,
  searchQuery,
  sort,
  direction,
  view,
}: {
  releaseId: string;
  releaseSummary: ReleaseSummary;
  releases: readonly ReleaseSummary[];
  selectedLanguage?: string;
  searchQuery?: string;
  sort?: string;
  direction?: "asc" | "desc";
  view: "table" | "bars";
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
  const definitions = result.value.meta.registry?.definitions ?? [];
  const languageEntries = selectedLanguage
    ? allEntries.filter((entry) => entry.languages.includes(selectedLanguage))
    : allEntries;
  const normalizedSearch = searchQuery?.toLowerCase();
  const filtered = normalizedSearch
    ? languageEntries.filter((entry) =>
        entry.label.toLowerCase().includes(normalizedSearch) ||
        entry.model_config_id.toLowerCase().includes(normalizedSearch),
      )
    : languageEntries;
  const sortId = sort && metricIds.has(sort) ? sort : undefined;
  const sortDefinition = definitions.find((definition) => definition.metric_id === sortId);
  const activeDirection: "asc" | "desc" = direction ?? (sortDefinition?.direction === "lower" ? "asc" : "desc");
  const rows = orderedEntries(filtered, sortId, activeDirection);
  const resetHref = `/leaderboard${asUrlQuery({ release: releaseId, view: view === "bars" ? view : undefined })}`;

  return (
    <>
      <div className="page-toolbar">
        <ReleaseSelector
          releases={releases}
          releaseId={releaseId}
          action="/leaderboard"
          preserved={{ language: selectedLanguage, q: searchQuery, sort_metric: sortId, direction: sortId ? activeDirection : undefined, view: view === "bars" ? view : undefined }}
        />
        <div className="scope-facts" aria-label="Current result coverage">
          <span><b>Configurations</b>{filtered.length}</span>
          <span><b>Languages</b>{languages.length}</span>
        </div>
      </div>
      <ReleaseNotice release={releaseSummary} />

      <div className="language-filter-row">
        <LanguageFilter
          languages={languages}
          releaseId={releaseId}
          selected={selectedLanguage}
          query={searchQuery}
          sort={sortId}
          direction={sortId ? activeDirection : undefined}
          view={view === "bars" ? view : undefined}
        />
        <nav className="language-index" aria-label="Filter by declared language">
          <span className="language-index-label">Language view</span>
          <Link
            className={!selectedLanguage ? "is-active" : undefined}
            href={`/leaderboard${asUrlQuery({ release: releaseId, q: searchQuery, sort_metric: sortId, direction: sortId ? activeDirection : undefined, view: view === "bars" ? view : undefined })}`}
            aria-current={!selectedLanguage ? "page" : undefined}
          >
            All
          </Link>
          {languages.map((language) => (
            <Link
              key={language}
              className={selectedLanguage === language ? "is-active" : undefined}
              href={`/leaderboard${asUrlQuery({ release: releaseId, language, q: searchQuery, sort_metric: sortId, direction: sortId ? activeDirection : undefined, view: view === "bars" ? view : undefined })}`}
              aria-current={selectedLanguage === language ? "page" : undefined}
            >
              {language}
            </Link>
          ))}
        </nav>
      </div>

      <section className="section-card results-section" aria-labelledby="leaderboard-table-title">
        <div className="results-heading">
          <SectionHeading
            id="leaderboard-table-title"
            title={selectedLanguage ? `${selectedLanguage} configurations` : "Published configurations"}
            description={selectedLanguage
              ? `This filters configurations by declared ${selectedLanguage} coverage. Metrics remain release-wide; open the language view for language-specific measurements.`
              : "Choose a language to narrow declared coverage. Sort any published metric; values retain their release scope."}
          />
          <p className="result-count" aria-live="polite">
            {rows.length} {rows.length === 1 ? "configuration" : "configurations"}
          </p>
          <nav className="leaderboard-view-toggle" aria-label="Results display">
            <Link
              href={`/leaderboard${asUrlQuery({ release: releaseId, language: selectedLanguage, q: searchQuery, sort_metric: sortId, direction: sortId ? activeDirection : undefined })}`}
              aria-current={view === "table" ? "page" : undefined}
            >
              Table
            </Link>
            <Link
              href={`/leaderboard${asUrlQuery({ release: releaseId, language: selectedLanguage, q: searchQuery, sort_metric: sortId, direction: sortId ? activeDirection : undefined, view: "bars" })}`}
              aria-current={view === "bars" ? "page" : undefined}
            >
              Bars
            </Link>
          </nav>
        </div>
        {rows.length && view === "bars" ? (
          <LeaderboardBars
            entries={filtered}
            definitions={definitions}
            releaseId={releaseId}
            language={selectedLanguage}
            searchQuery={searchQuery}
            selectedMetricId={sortId}
            direction={sortId ? activeDirection : undefined}
          />
        ) : rows.length ? (
          <SortableLeaderboard
            entries={rows}
            metricDefinitions={definitions}
            releaseId={releaseId}
            language={selectedLanguage}
            searchQuery={searchQuery}
            sort={sortId}
            direction={activeDirection}
          />
        ) : (
          <EmptyState title={searchQuery ? "No configurations match this search" : selectedLanguage ? `No configurations cover ${selectedLanguage}` : "No configurations in this release"}>
            {searchQuery && selectedLanguage
              ? `No public configuration matching "${searchQuery}" declares ${selectedLanguage} coverage. Clear the filters or try another name. No score was inferred for an uncovered language.`
              : searchQuery
                ? `No public configuration matches "${searchQuery}" in this release. Clear the search or try a model name or configuration ID.`
                : selectedLanguage
                  ? `This release contains no public configurations that declare ${selectedLanguage} coverage. No score was inferred for an uncovered language.`
                  : "This release contains no public configuration rows."}
            {searchQuery || selectedLanguage ? <> <Link href={resetHref}>Reset search and filters.</Link></> : null}
          </EmptyState>
        )}
        {sortId ? (
          <p className="sort-explainer">
            Sorted by {sortDefinition?.label ?? sortId} ({activeDirection === "asc" ? "ascending" : "descending"}).
            {sortDefinition ? ` ${sortDefinition.direction === "lower" ? "Lower" : "Higher"} values are better.` : " Metric direction is not included in this release registry."}
            {" "}The URL records this sort and release.
          </p>
        ) : null}
      </section>

      <section className="section-card compare-launcher" aria-labelledby="compare-launcher-title">
        <div>
          <p className="eyebrow">Same release · exact task evidence</p>
          <h2 id="compare-launcher-title">Compare configurations</h2>
          <p>Select two to four configurations. The comparison checks protocol, budget, and shared public task scorecards.</p>
        </div>
        <form action="/compare" method="get">
          <input type="hidden" name="release" value={releaseId} />
          <label htmlFor="compare-models-launcher">Configurations <span>(select 2–4)</span></label>
          <select id="compare-models-launcher" name="models" multiple size={Math.min(5, Math.max(3, allEntries.length))}>
            {allEntries.map((entry) => <option key={entry.model_config_id} value={entry.model_config_id}>{entry.label} · {entry.model_config_id}</option>)}
          </select>
          <p className="form-help">Use Ctrl (Windows/Linux) or Command (Mac) to select multiple configurations.</p>
          <button type="submit">Open comparison</button>
          <Link href={`/compare?release=${encodeURIComponent(releaseId)}`}>Comparison filters and details</Link>
        </form>
      </section>
    </>
  );
}
