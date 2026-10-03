import type { Metadata } from "next";
import Link from "next/link";
import { PageIntro, ReleaseNotice, ReleaseSelector, ResourceState, SectionHeading } from "@/components/public-ui";
import { asUrlQuery, loadReleaseContext, publicApi, type ApiEnvelope, type ReleaseSummary, type TaskSummary } from "@/lib/public-api";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Task explorer", description: "Browse disclosed public task versions in a frozen release." };
type Search = Promise<Record<string, string | string[] | undefined>>;
function first(value: string | string[] | undefined): string | undefined { return Array.isArray(value) ? value[0] : value; }

export default async function TasksPage({ searchParams }: { searchParams: Search }) {
  const query = await searchParams;
  const releaseResource = await loadReleaseContext(first(query.release));
  return <>
    <PageIntro eyebrow="Public analysis · Disclosed tasks" title="Task explorer" description="Browse only task versions explicitly disclosed by a published release. Statements, sources, patches and findings load only after opening a task." />
    <ResourceState resource={releaseResource}>{(context) => <TaskList
      release={context.summary}
      releases={context.releases}
      language={first(query.language)}
      family={first(query.family)}
      difficulty={first(query.difficulty)}
      cursor={first(query.cursor)}
    />}</ResourceState>
  </>;
}

async function TaskList({
  release, releases, language, family, difficulty, cursor,
}: {
  release: ReleaseSummary;
  releases: readonly ReleaseSummary[];
  language?: string;
  family?: string;
  difficulty?: string;
  cursor?: string;
}) {
  const query = new URLSearchParams({ release: release.release_id, limit: "50" });
  if (language) query.set("language", language);
  if (family) query.set("family", family);
  if (difficulty) query.set("difficulty", difficulty);
  if (cursor) query.set("cursor", cursor);
  const resource = await publicApi<ApiEnvelope<readonly TaskSummary[]>>(`/tasks?${query.toString()}`);
  const filterQuery = { release: release.release_id, language, family, difficulty };
  return <>
    <div className="page-toolbar">
      <ReleaseSelector releases={releases} releaseId={release.release_id} action="/tasks" preserved={{ language, family, difficulty }} />
      <div className="scope-facts"><span><b>Release</b>{release.release_id}</span><span><b>Task versions</b>{resource.state === "ready" ? resource.value.meta.total ?? resource.value.data.length : "—"}</span><span><b>Page limit</b>50</span></div>
    </div>
    <ReleaseNotice release={release} />
    <section className="section-card">
      <SectionHeading title="Disclosed task versions" description="Task metadata is paginated and release-bound. Source, patch and tool payloads are fetched separately when a task is opened." />
      <form action="/tasks" method="get" className="task-filters">
        <input type="hidden" name="release" value={release.release_id} />
        <label>Language <input name="language" defaultValue={language ?? ""} maxLength={64} placeholder="Any language" /></label>
        <label>Task family <input name="family" defaultValue={family ?? ""} maxLength={64} placeholder="Any family" /></label>
        <label>Difficulty <input name="difficulty" defaultValue={difficulty ?? ""} maxLength={32} placeholder="Any difficulty" /></label>
        <button type="submit">Filter tasks</button>
        {language || family || difficulty ? <Link href={`/tasks${asUrlQuery({ release: release.release_id })}`}>Clear filters</Link> : null}
      </form>
      <ResourceState resource={resource}>
        {(page) => page.data.length ? <>
          <div className="task-list" aria-label="Disclosed task list">
            {page.data.map((task) => <article className="task-row" key={`${task.task_id}@${task.version}`}>
              <div className="task-row-title"><h3><Link href={`/tasks/${encodeURIComponent(task.task_id)}${asUrlQuery({ ...filterQuery, cursor: undefined })}`}>{task.task_id} · v{task.version}</Link></h3><span className="task-classification">{task.language_id} · {task.family} · {task.difficulty}</span></div>
              <p>{task.statement_summary}</p>
              <div className="task-counts"><span>{task.source_version_count} public source versions</span><span>{task.patch_count} submitted patches</span><span>{task.finding_count} tool findings</span></div>
            </article>)}
          </div>
          <nav className="pagination" aria-label="Task list pages">
            {cursor ? <Link href={`/tasks${asUrlQuery(filterQuery)}`}>First page</Link> : <span>First page</span>}
            {page.meta.next_cursor ? <Link rel="next" href={`/tasks${asUrlQuery({ ...filterQuery, cursor: page.meta.next_cursor })}`}>Next page</Link> : <span>End of disclosed task list</span>}
          </nav>
          <p className="form-help">Showing {page.meta.returned ?? page.data.length} of {page.meta.total ?? page.data.length} disclosed task versions. Large source and diff payloads are excluded from this list response.</p>
        </> : <div className="empty-state"><span className="state-kicker">No task rows</span><h2>No disclosed task matches this view</h2><p>This release has no disclosed task versions for the selected filters. A private or unknown task is never added to the list.</p></div>}
      </ResourceState>
    </section>
  </>;
}
