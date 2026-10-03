"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { scoreEvidenceHref, type ApiEnvelope, type PublicTaskContent, type Resource } from "@/lib/public-api";

function errorMessage(body: unknown): string {
  if (typeof body === "object" && body !== null && "error" in body) {
    const error = (body as { error?: { message?: unknown } }).error;
    if (typeof error?.message === "string") return error.message;
  }
  return "Public task details could not be loaded. Try again.";
}

export function LazyTaskContent({
  taskId,
  releaseId,
  autoOpen = false,
}: {
  taskId: string;
  releaseId: string;
  autoOpen?: boolean;
}) {
  const [expanded, setExpanded] = useState(autoOpen);
  const [resource, setResource] = useState<Resource<PublicTaskContent>>({ state: "loading" });

  async function fetchResource(): Promise<Resource<PublicTaskContent>> {
    try {
      const response = await fetch(
        `/api/public/tasks/${encodeURIComponent(taskId)}/content?release=${encodeURIComponent(releaseId)}`,
        { cache: "no-store", headers: { accept: "application/json" } },
      );
      const body: unknown = await response.json();
      if (!response.ok) {
        return { state: "error", title: "Task detail unavailable", message: errorMessage(body) };
      }
      const envelope = body as ApiEnvelope<PublicTaskContent>;
      if (!envelope.data || envelope.data.task_id !== taskId) {
        return { state: "error", title: "Task detail unavailable", message: "The returned task identity did not match the requested task." };
      }
      return envelope.data.source_versions.length + envelope.data.submitted_patches.length + envelope.data.tool_findings.length === 0
        ? { state: "empty", title: "No additional public task artifacts", message: "This disclosed task has no source, patch, or tool-finding payload in this release." }
        : { state: "ready", value: envelope.data };
    } catch {
      return { state: "error", title: "Task detail unavailable", message: "Public task details could not be loaded. Try again." };
    }
  }

  async function load() {
    setResource({ state: "loading" });
    setResource(await fetchResource());
  }

  useEffect(() => {
    if (autoOpen) void fetchResource().then(setResource);
    // The task and release are immutable URL inputs for this mounted component.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoOpen, taskId, releaseId]);

  useEffect(() => {
    if (resource.state !== "ready" || !window.location.hash) return;
    let targetId = window.location.hash.slice(1);
    try { targetId = decodeURIComponent(targetId); } catch { /* Keep the literal fragment. */ }
    const target = document.getElementById(targetId);
    const section = target?.classList.contains("anchor-alias") ? target.parentElement : target;
    section?.scrollIntoView({
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
      block: "start",
    });
  }, [resource.state]);

  function toggle() {
    const next = !expanded;
    setExpanded(next);
    if (next && resource.state === "loading") void load();
  }

  return (
    <section className="lazy-evidence" aria-labelledby={`task-content-${taskId}`}>
      <div className="lazy-evidence-heading">
        <div>
          <h2 id={`task-content-${taskId}`}>Public statement, sources, patches and findings</h2>
          <p>Payloads are fetched only when opened. Source and diff text is displayed inertly as plain text.</p>
        </div>
        <button type="button" aria-expanded={expanded} onClick={toggle}>
          {expanded ? "Hide details" : "Load public details"}
        </button>
      </div>
      {expanded ? <TaskPayload resource={resource} releaseId={releaseId} /> : null}
    </section>
  );
}

function TaskPayload({ resource, releaseId }: { resource: Resource<PublicTaskContent>; releaseId: string }) {
  if (resource.state === "loading") {
    return <p className="payload-state" role="status" aria-live="polite">Loading bounded public task details…</p>;
  }
  if (resource.state === "error") {
    return <div className="payload-state payload-error" role="alert"><strong>{resource.title}</strong><p>{resource.message}</p></div>;
  }
  if (resource.state === "empty") {
    return <div className="payload-state"><strong>{resource.title}</strong><p>{resource.message}</p></div>;
  }
  const content = resource.value;
  return (
    <div className="task-payload">
      <section id="public-statement">
        <h3>Task statement · version {content.task_version}</h3>
        <p className="public-statement">{content.statement}</p>
        <a className="task-export-link" href={`/api/public/tasks/${encodeURIComponent(content.task_id)}/content?release=${encodeURIComponent(releaseId)}&download=1`}>
          Download this disclosed task record as JSON
        </a>
      </section>
      {content.source_versions.map((source) => (
        <section className="payload-card" id={`source-${source.source_id}`} key={source.source_id}>
          <span className="anchor-alias" id={`evidence-${source.source_id}`} />
          <div className="payload-heading"><h3>{source.path}</h3><span>{source.version_label} · {source.language_id}</span></div>
          <pre tabIndex={0} aria-label={`Public source text ${source.path}`}><code>{source.source_text}</code></pre>
          <p className="payload-id">Public source ID: {source.source_id}</p>
        </section>
      ))}
      {content.submitted_patches.map((patch) => (
        <section className="payload-card" id={`patch-${patch.patch_id}`} key={patch.patch_id}>
          <span className="anchor-alias" id={`evidence-${patch.patch_id}`} />
          <div className="payload-heading"><h3>{patch.summary}</h3><span>{patch.model_config_id}</span></div>
          <pre tabIndex={0} aria-label={`Submitted patch diff ${patch.patch_id}`}><code>{patch.diff_text}</code></pre>
          <Link href={`/scorecards/${encodeURIComponent(patch.scorecard_id)}?release=${encodeURIComponent(releaseId)}`}>
            Source scorecard {patch.scorecard_id}
          </Link>
          <p className="payload-id">Public patch ID: {patch.patch_id}</p>
        </section>
      ))}
      {content.tool_findings.map((finding) => (
        <article className="finding-card" id={`finding-${finding.finding_id}`} key={finding.finding_id}>
          <span className="anchor-alias" id={`evidence-${finding.finding_id}`} />
          <div className="payload-heading"><h3>{finding.tool_id} · {finding.rule_id}</h3><span>{finding.severity}{finding.line === null ? "" : ` · line ${finding.line}`}</span></div>
          <p>{finding.message}</p>
          <Link href={`/scorecards/${encodeURIComponent(finding.scorecard_id)}?release=${encodeURIComponent(releaseId)}`}>
            Source scorecard {finding.scorecard_id}
          </Link>
          <p className="payload-id">Public finding ID: {finding.finding_id}</p>
        </article>
      ))}
    </div>
  );
}

export function scorecardUrl(scorecardId: string, releaseId: string): string {
  return scoreEvidenceHref(`/v1/scorecards/${encodeURIComponent(scorecardId)}`, releaseId);
}
