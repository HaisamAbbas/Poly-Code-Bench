import type { Metadata } from "next";
import Link from "next/link";
import { LazyTaskContent } from "@/components/public-evidence";
import { PageIntro, ReleaseNotice, ResourceState } from "@/components/public-ui";
import { asUrlQuery, loadReleaseContext, publicApi, type ApiEnvelope, type Resource, type TaskSummary } from "@/lib/public-api";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Public task detail", description: "Inspect a disclosed task version and its released public evidence." };
type Search = Promise<Record<string, string | string[] | undefined>>;
function first(value: string | string[] | undefined): string | undefined { return Array.isArray(value) ? value[0] : value; }

export default async function TaskDetailPage({ params, searchParams }: { params: Promise<{ taskId: string }>; searchParams: Search }) {
  const [{ taskId }, query] = await Promise.all([params, searchParams]);
  const releaseResource = await loadReleaseContext(first(query.release));
  let taskResource: Resource<TaskSummary>;
  switch (releaseResource.state) {
    case "ready": {
      const result = await publicApi<ApiEnvelope<TaskSummary>>(
        `/tasks/${encodeURIComponent(taskId)}?release=${encodeURIComponent(releaseResource.value.releaseId)}`,
      );
      taskResource = result.state === "ready" ? { state: "ready", value: result.value.data } : result;
      break;
    }
    case "loading": taskResource = { state: "loading" }; break;
    case "empty": taskResource = releaseResource; break;
    case "error": taskResource = releaseResource; break;
  }
  const autoOpen = first(query.open) === "1";

  return <>
    <PageIntro eyebrow="Public analysis · Disclosed task" title="Task detail" description="This view is bound to one public task version in the selected release. Undisclosed and unknown task identifiers use the same not-found response." />
    <ResourceState resource={releaseResource}>
      {(context) => <>
        <div className="page-toolbar"><Link className="back-link" href={`/tasks${asUrlQuery({ release: context.releaseId })}`}>← Back to task explorer</Link><span className="scope-facts"><b>Release</b> {context.releaseId}</span></div>
        <ReleaseNotice release={context.summary} />
        <ResourceState resource={taskResource}>
          {(task) => <>
            <section className="section-card task-detail-intro">
              <p className="eyebrow">{task.language_id} · {task.family} · {task.difficulty}</p>
              <h2>{task.task_id} · version {task.version}</h2>
              <p className="public-statement">{task.statement_summary}</p>
              <dl className="identity-list"><dt>Task identity</dt><dd><code>{task.task_id}@{task.version}</code></dd><dt>Public source versions</dt><dd>{task.source_version_count}</dd><dt>Submitted patches</dt><dd>{task.patch_count}</dd><dt>Tool findings</dt><dd>{task.finding_count}</dd></dl>
            </section>
            <LazyTaskContent taskId={task.task_id} releaseId={context.releaseId} autoOpen={autoOpen} />
            <p className="privacy-note">Only content explicitly released with this task version is shown. No upload is executed, and private or held-out candidate references are not included.</p>
          </>}
        </ResourceState>
      </>}
    </ResourceState>
  </>;
}
