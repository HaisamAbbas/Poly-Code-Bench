import type { Metadata } from "next";
import Link from "next/link";
import {
  EmptyState,
  PageIntro,
  ReleaseNotice,
  ReleaseSelector,
  ResourceState,
  SectionHeading,
} from "@/components/public-ui";
import {
  loadReleaseContext,
  publicApi,
  type ApiEnvelope,
  type LeaderboardEntry,
  type ReleaseSummary,
} from "@/lib/public-api";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Languages",
  description: "Browse declared programming language coverage and open language-specific measurements in a public release.",
};

type Search = Promise<Record<string, string | string[] | undefined>>;

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

export default async function LanguagesPage({ searchParams }: { searchParams: Search }) {
  const query = await searchParams;
  const context = await loadReleaseContext(first(query.release));

  return (
    <>
      <PageIntro
        eyebrow="PolyCodeBench / Language coverage"
        title="Explore languages."
        description="Browse programming languages declared by configurations in a published release. Open a language to inspect its language-specific evidence."
      />
      <ResourceState resource={context}>
        {(releaseContext) => (
          <LanguagesForRelease
            releaseId={releaseContext.releaseId}
            releaseSummary={releaseContext.summary}
            releases={releaseContext.releases}
          />
        )}
      </ResourceState>
    </>
  );
}

async function LanguagesForRelease({
  releaseId,
  releaseSummary,
  releases,
}: {
  releaseId: string;
  releaseSummary: ReleaseSummary;
  releases: readonly ReleaseSummary[];
}) {
  const result = await publicApi<ApiEnvelope<readonly LeaderboardEntry[]>>(
    `/leaderboard?release=${encodeURIComponent(releaseId)}&limit=200`,
  );

  return (
    <ResourceState resource={result}>
      {(response) => {
        const coverage = new Map<string, number>();
        for (const entry of response.data) {
          for (const language of entry.languages) {
            coverage.set(language, (coverage.get(language) ?? 0) + 1);
          }
        }
        const languages = [...coverage].sort(([left], [right]) => left.localeCompare(right));

        return (
          <>
            <div className="page-toolbar">
              <ReleaseSelector releases={releases} releaseId={releaseId} action="/languages" />
              <div className="scope-facts" aria-label="Current language coverage">
                <span><b>Languages</b>{languages.length}</span>
                <span><b>Configurations</b>{response.data.length}</span>
              </div>
            </div>
            <ReleaseNotice release={releaseSummary} />
            <section className="section-card language-directory-section" aria-labelledby="language-directory-title">
              <SectionHeading
                id="language-directory-title"
                title="Declared language coverage"
                description="Counts show configurations that declare each language in this release. They do not imply language-specific scores."
              />
              {languages.length ? (
                <ul className="language-directory">
                  {languages.map(([language, count]) => (
                    <li key={language}>
                      <Link
                        className="language-directory-link"
                        href={`/languages/${encodeURIComponent(language)}?release=${encodeURIComponent(releaseId)}`}
                      >
                        <span className="language-directory-name">{language}</span>
                        <span className="language-directory-count">
                          {count} {count === 1 ? "configuration" : "configurations"}
                        </span>
                        <span className="language-directory-action">View measurements <span aria-hidden="true">→</span></span>
                      </Link>
                    </li>
                  ))}
                </ul>
              ) : (
                <EmptyState title="No declared languages in this release">
                  This release contains no public configurations with declared language coverage.
                </EmptyState>
              )}
            </section>
            <p className="plain-note language-directory-note">
              Language-specific measurements appear only when the release publishes supporting evidence. No result is inferred from the coverage list.
              {" "}<Link href={`/leaderboard?release=${encodeURIComponent(releaseId)}`}>Return to all configurations.</Link>
            </p>
          </>
        );
      }}
    </ResourceState>
  );
}
