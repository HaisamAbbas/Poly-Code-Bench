"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { PageIntro } from "@/components/public-ui";
import {
  loadBenchmarkAuditWorkspace,
  requestAuditResourcePlan,
  type AuditResourcePlanResult,
  type AuditResourceRequest,
  type BenchmarkAuditWorkspaceData,
  type BenchmarkAuditWorkspaceResult,
  type BenchmarkScopeRow,
} from "@/lib/benchmark-audit-workspace";
import type { Resource } from "@/lib/public-api";

type Tab = "catalog" | "preflight";
type LiveState = BenchmarkScopeRow["live_state"];

const liveStateLabels: Record<LiveState, string> = {
  live_verified: "Live verified",
  pending: "Pending evidence",
  blocked: "Blocked",
};

export function BenchmarkAuditWorkspace() {
  const [resource, setResource] = useState<Resource<BenchmarkAuditWorkspaceResult>>({ state: "loading" });
  useEffect(() => {
    let active = true;
    void loadBenchmarkAuditWorkspace().then((result) => {
      if (active) setResource(result);
    });
    return () => { active = false; };
  }, []);

  if (resource.state !== "ready") {
    const title = resource.state === "error"
      ? resource.title
      : resource.state === "empty"
        ? resource.title
        : "Loading benchmark catalog";
    const message = resource.state === "error" || resource.state === "empty"
      ? resource.message
      : "Reading the versioned benchmark scope and source policy.";
    return (
      <>
        <PageIntro eyebrow="Benchmark containment" title="Audit workspace" description="Review benchmark scope readiness and size a no-dispatch resource preflight." />
        <section className="audit-workspace-state" role={resource.state === "error" ? "alert" : "status"}>
          <span className="audit-workspace-kicker">Catalog status</span>
          <h2>{title}</h2>
          <p>{message}</p>
          <p><Link href="/audit-reports">Open public audit reports</Link></p>
        </section>
      </>
    );
  }

  return <Workspace data={resource.value.data} digest={resource.value.meta.release_digest} />;
}

function Workspace({ data, digest }: { data: BenchmarkAuditWorkspaceData; digest: string }) {
  const [tab, setTab] = useState<Tab>("catalog");
  const [search, setSearch] = useState("");
  const [familyFilter, setFamilyFilter] = useState("all");
  const [liveFilter, setLiveFilter] = useState<LiveState | "all">("all");
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [sources, setSources] = useState<string[]>([]);
  const [stageCount, setStageCount] = useState(1);
  const [averageBytes, setAverageBytes] = useState("");
  const [planResult, setPlanResult] = useState<Resource<AuditResourcePlanResult> | null>(null);
  const [isPlanning, setIsPlanning] = useState(false);

  const families = useMemo(
    () => [...new Set(data.benchmarks.map((row) => row.family))].sort(),
    [data.benchmarks],
  );
  const statusCounts = useMemo(() => ({
    live_verified: data.benchmarks.filter((row) => row.live_state === "live_verified").length,
    pending: data.benchmarks.filter((row) => row.live_state === "pending").length,
    blocked: data.benchmarks.filter((row) => row.live_state === "blocked").length,
  }), [data.benchmarks]);
  const fixtureCount = data.benchmarks.filter((row) => row.fixture_evidence_state === "synthetic_source_fixture").length;
  const filteredRows = useMemo(() => {
    const query = search.trim().toLowerCase();
    return data.benchmarks
      .filter((row) => familyFilter === "all" || row.family === familyFilter)
      .filter((row) => liveFilter === "all" || row.live_state === liveFilter)
      .filter((row) => !query || `${row.name} ${row.benchmark_slug} ${row.family}`.toLowerCase().includes(query))
      .sort((left, right) => left.name.localeCompare(right.name));
  }, [data.benchmarks, familyFilter, liveFilter, search]);
  const selectedTaskTotal = Object.values(counts).reduce((total, count) => total + count, 0);
  const exceedsStorageCap = Boolean(averageBytes)
    && selectedTaskTotal * Number(averageBytes) > data.limits.max_storage_bytes_per_plan;
  const exceedsQueryCap = selectedTaskTotal * sources.length * stageCount > data.limits.max_query_units_per_plan;
  const planCanRun = selectedTaskTotal > 0
    && selectedTaskTotal <= data.limits.max_tasks_per_plan
    && sources.length > 0
    && sources.length <= data.limits.max_source_groups_per_plan
    && stageCount > 0
    && stageCount <= data.limits.max_stages_per_task
    && !exceedsQueryCap
    && !exceedsStorageCap
    && (!averageBytes || (Number.isSafeInteger(Number(averageBytes)) && Number(averageBytes) > 0 && Number(averageBytes) <= 1_099_511_627_776));

  function updateTaskCount(slug: string, value: string) {
    const parsed = value === "" ? 0 : Number(value);
    if (!Number.isSafeInteger(parsed) || parsed < 0) return;
    setCounts((current) => {
      const next = { ...current };
      if (parsed === 0) delete next[slug];
      else next[slug] = parsed;
      return next;
    });
    setPlanResult(null);
  }

  function toggleSource(slug: string) {
    setSources((current) => current.includes(slug)
      ? current.filter((source) => source !== slug)
      : [...current, slug]);
    setPlanResult(null);
  }

  async function buildResourcePlan() {
    if (!planCanRun) return;
    const request: AuditResourceRequest = {
      task_counts: counts,
      source_groups: sources,
      stages: Array.from({ length: stageCount }, (_, index) => `preflight-stage-${index + 1}`),
      average_item_bytes: averageBytes ? Number(averageBytes) : null,
    };
    setIsPlanning(true);
    setPlanResult(null);
    const result = await requestAuditResourcePlan(request);
    setPlanResult(result);
    setIsPlanning(false);
  }

  const assignedWidths = (count: number) => `${Math.max(count > 0 ? 3 : 0, Math.round((count / Math.max(1, data.benchmarks.length)) * 100))}%`;

  return (
    <>
      <PageIntro
        eyebrow="Benchmark containment"
        title="Audit workspace"
        description="Inspect the registered benchmark scope, evidence readiness, and source limits. Build a bounded resource preflight from the actual catalog."
      />

      <section className="audit-workspace-hero" aria-label="Benchmark audit status">
        <div className="audit-workspace-hero-copy">
          <span className="audit-workspace-kicker">Catalog {data.catalog_version} · observed {data.observed_on}</span>
          <h2>Scope readiness at a glance</h2>
          <p>Catalog and preflight outputs are metadata-only. No source is queried, no task content is inspected, and no containment result is produced here.</p>
          <div className="audit-workspace-hero-links">
            <Link href="/audit-reports">View reviewed reports <span aria-hidden="true">↗</span></Link>
            <Link href="/audit-attestations">Verify an attestation <span aria-hidden="true">↗</span></Link>
          </div>
        </div>
        <div className="audit-workspace-live-mark" aria-label={`${statusCounts.live_verified} benchmark families live verified`}>
          <span className="audit-workspace-live-number">{statusCounts.live_verified}</span>
          <span>live verified</span>
          <small>of {data.benchmarks.length} families</small>
        </div>
      </section>

      <section className="audit-workspace-metrics" aria-label="Catalog summary">
        <Metric label="Benchmark families" value={data.benchmarks.length.toLocaleString("en")} note="in versioned scope" icon="⌘" />
        <Metric label="Pending evidence" value={statusCounts.pending.toLocaleString("en")} note="not live verified" icon="◷" />
        <Metric label="Blocked" value={statusCounts.blocked.toLocaleString("en")} note="explicit catalog gates" icon="⊘" />
        <Metric label="Synthetic fixtures" value={fixtureCount.toLocaleString("en")} note="parser fixtures only" icon="◇" />
      </section>

      <section className="audit-workspace-panel audit-workspace-lifecycle" aria-labelledby="audit-lifecycle-title">
        <div className="audit-workspace-section-heading">
          <div><p className="audit-workspace-kicker">Operator path</p><h2 id="audit-lifecycle-title">What this workspace can do today</h2><p>Each stage is labeled from the existing backend contracts and deployment handoff.</p></div>
          <span className="audit-workspace-lifecycle-state">Current status: partial</span>
        </div>
        <div className="audit-workspace-lifecycle-grid">
          <LifecycleStep number="01" title="Catalog & scope checks" state="available" detail="Versioned benchmark families, declared scope, rights state, and conformance blockers." />
          <LifecycleStep number="02" title="Resource preflight" state="available" detail="Bounded query, candidate, and storage ceilings. It never dispatches a query." />
          <LifecycleStep number="03" title="Source scan & calibration" state="blocked" detail="No approved source snapshots, live connectors, independent labels, or scan dispatch route." />
          <LifecycleStep number="04" title="Review & enforcement" state="blocked" detail="Private evidence requires tenant curator claims; shared review and transition writers are not configured." />
          <LifecycleStep number="05" title="Monitoring & signing" state="blocked" detail="Monitor writers, scheduler, production key custody, and approved publication are unavailable." />
          <LifecycleStep number="06" title="Public verification" state="read-only" detail="Reviewed aggregate reports and signed attestations can be looked up and verified." />
        </div>
      </section>

      <section className="audit-workspace-panel audit-workspace-status-panel" aria-labelledby="audit-status-chart-title">
        <div className="audit-workspace-section-heading">
          <div>
            <p className="audit-workspace-kicker">Live conformance</p>
            <h2 id="audit-status-chart-title">Evidence status by benchmark family</h2>
          </div>
          <button className="audit-workspace-clear" type="button" onClick={() => setLiveFilter("all")} aria-pressed={liveFilter === "all"}>
            {liveFilter === "all" ? "All families" : "Clear filter"}
          </button>
        </div>
        <div className="audit-workspace-status-chart" role="group" aria-label="Filter benchmarks by live evidence status">
          {(["live_verified", "pending", "blocked"] as const).map((state) => (
            <button
              className={`audit-workspace-status-row audit-state-${state.replaceAll("_", "-")}${liveFilter === state ? " is-selected" : ""}`}
              key={state}
              type="button"
              aria-pressed={liveFilter === state}
              onClick={() => setLiveFilter(liveFilter === state ? "all" : state)}
            >
              <span className="audit-workspace-status-name">{liveStateLabels[state]}</span>
              <span className="audit-workspace-bar-track"><span className="audit-workspace-bar-fill" style={{ width: assignedWidths(statusCounts[state]) }} /></span>
              <strong>{statusCounts[state]}</strong>
              <span className="audit-workspace-status-hint">Filter table</span>
            </button>
          ))}
        </div>
        <p className="audit-workspace-chart-note">Counts come from the backend catalog scope report. Fixture-only parser checks do not count as live verification.</p>
      </section>

      <div className="audit-workspace-tabs" role="tablist" aria-label="Benchmark audit workspace sections">
        <button id="catalog-tab" role="tab" aria-selected={tab === "catalog"} type="button" onClick={() => setTab("catalog")}>Benchmark catalog <span>{data.benchmarks.length}</span></button>
        <button id="preflight-tab" role="tab" aria-selected={tab === "preflight"} type="button" onClick={() => setTab("preflight")}>Resource preflight <span>{selectedTaskTotal || "Set up"}</span></button>
      </div>

      {tab === "catalog" ? (
        <section id="catalog-panel" className="audit-workspace-panel" role="tabpanel" aria-labelledby="catalog-tab">
          <div className="audit-workspace-section-heading audit-workspace-catalog-heading">
            <div>
              <p className="audit-workspace-kicker">Registry explorer</p>
              <h2>Benchmark scope</h2>
              <p>Expand a row to inspect the declared components, modalities, source groups, and outstanding criteria.</p>
            </div>
            <button type="button" className="audit-workspace-primary" onClick={() => setTab("preflight")}>Plan resources <span aria-hidden="true">→</span></button>
          </div>
          <div className="audit-workspace-filters">
            <label className="audit-workspace-search">
              <span className="sr-only">Search benchmark families</span>
              <span aria-hidden="true">⌕</span>
              <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search benchmark or family" />
            </label>
            <label>
              <span className="sr-only">Filter by benchmark family</span>
              <select value={familyFilter} onChange={(event) => setFamilyFilter(event.target.value)}>
                <option value="all">All families</option>
                {families.map((family) => <option key={family} value={family}>{label(family)}</option>)}
              </select>
            </label>
            <span className="audit-workspace-result-count">{filteredRows.length} of {data.benchmarks.length} families</span>
          </div>

          <div className="audit-workspace-table-wrap">
            <table className="audit-workspace-table">
              <thead>
                <tr><th scope="col">Benchmark</th><th scope="col">Family</th><th scope="col">Version & split</th><th scope="col">Rights</th><th scope="col">Importer</th><th scope="col">Evidence status</th><th scope="col"><span className="sr-only">Resource preflight task count</span>Plan</th></tr>
              </thead>
              <tbody>
                {filteredRows.map((row) => {
                  const expanded = selectedSlug === row.benchmark_slug;
                  const count = counts[row.benchmark_slug] ?? 0;
                  return (
                    <AuditBenchmarkRows
                      key={row.benchmark_slug}
                      row={row}
                      expanded={expanded}
                      count={count}
                      maxTasks={data.limits.max_tasks_per_plan}
                      onExpand={() => setSelectedSlug(expanded ? null : row.benchmark_slug)}
                      onCount={(value) => updateTaskCount(row.benchmark_slug, value)}
                      onAdd={() => { if (!count) updateTaskCount(row.benchmark_slug, "1"); setTab("preflight"); }}
                    />
                  );
                })}
                {filteredRows.length === 0 ? <tr><td colSpan={7} className="audit-workspace-empty-row">No benchmark families match these filters.</td></tr> : null}
              </tbody>
            </table>
          </div>
          <div className="audit-workspace-table-footer">
            <span>Source digest <code>{digest.slice(0, 18)}…</code></span>
            <span>Registry is versioned metadata; it is not source authorization.</span>
          </div>
        </section>
      ) : (
        <section id="preflight-panel" className="audit-workspace-panel" role="tabpanel" aria-labelledby="preflight-tab">
          <div className="audit-workspace-section-heading">
            <div>
              <p className="audit-workspace-kicker">Bounded planning</p>
              <h2>Resource preflight</h2>
              <p>Estimate request, candidate, and storage ceilings for selected benchmark counts and source groups.</p>
            </div>
            <span className="audit-workspace-no-dispatch">No dispatch · no model calls</span>
          </div>

          <div className="audit-workspace-planner-grid">
            <section className="audit-workspace-planner-section" aria-labelledby="planner-benchmarks-title">
              <div className="audit-workspace-mini-heading"><span className="audit-workspace-step">01</span><div><h3 id="planner-benchmarks-title">Benchmarks and task counts</h3><p>{selectedTaskTotal} selected · cap {data.limits.max_tasks_per_plan.toLocaleString("en")}</p></div></div>
              <div className="audit-workspace-selected-list">
                {Object.entries(counts).length === 0 ? <p className="audit-workspace-empty-hint">No benchmarks selected. Add one or more from the catalog tab.</p> : Object.entries(counts).map(([slug, count]) => {
                  const benchmark = data.benchmarks.find((row) => row.benchmark_slug === slug);
                  if (!benchmark) return null;
                  return <div className="audit-workspace-selected-benchmark" key={slug}>
                    <div><strong>{benchmark.name}</strong><small>{label(benchmark.family)} · {label(benchmark.live_state)}</small></div>
                    <label><span className="sr-only">Task count for {benchmark.name}</span><input type="number" min={1} max={data.limits.max_tasks_per_plan} value={count} onChange={(event) => updateTaskCount(slug, event.target.value)} /></label>
                    <button type="button" aria-label={`Remove ${benchmark.name} from preflight`} onClick={() => updateTaskCount(slug, "0")}>×</button>
                  </div>;
                })}
              </div>
              <Link className="audit-workspace-inline-link" href="#catalog-tab" onClick={() => setTab("catalog")}>Browse catalog <span aria-hidden="true">→</span></Link>
            </section>

            <section className="audit-workspace-planner-section" aria-labelledby="planner-sources-title">
              <div className="audit-workspace-mini-heading"><span className="audit-workspace-step">02</span><div><h3 id="planner-sources-title">Source groups</h3><p>{sources.length} selected · cap {data.limits.max_source_groups_per_plan}</p></div></div>
              <div className="audit-workspace-source-list">
                {data.sources.map((source) => <label className="audit-workspace-source-choice" key={source.slug}>
                  <input type="checkbox" checked={sources.includes(source.slug)} onChange={() => toggleSource(source.slug)} />
                  <span className="audit-workspace-checkmark" aria-hidden="true">✓</span>
                  <span className="audit-workspace-source-copy"><strong>{source.name}</strong><small>{label(source.authorization_state)} · {label(source.connector_state)}</small></span>
                  <span className={`audit-workspace-pill ${source.authorization_state === "approved_scoped" ? "is-ready" : "is-pending"}`}>{source.authorization_state === "approved_scoped" ? "Approved" : "Not approved"}</span>
                </label>)}
              </div>
            </section>

            <section className="audit-workspace-planner-section audit-workspace-sizing-section" aria-labelledby="planner-sizing-title">
              <div className="audit-workspace-mini-heading"><span className="audit-workspace-step">03</span><div><h3 id="planner-sizing-title">Sizing assumptions</h3><p>Inputs are used only for bounded estimates.</p></div></div>
              <div className="audit-workspace-sizing-controls">
                <label><span>Stage count</span><input type="number" min={1} max={data.limits.max_stages_per_task} value={stageCount} onChange={(event) => { setStageCount(Number(event.target.value)); setPlanResult(null); }} /><small>Counted as request units only; no retrieval stage is dispatched.</small></label>
                <label><span>Average item size <em>Optional</em></span><div className="audit-workspace-number-unit"><input inputMode="numeric" type="number" min={1} max={1_099_511_627_776} value={averageBytes} onChange={(event) => { setAverageBytes(event.target.value); setPlanResult(null); }} placeholder="Not provided" /><span>bytes</span></div><small>Storage is unknown until you provide an estimate.</small></label>
              </div>
              <div className="audit-workspace-cap-strip">
                <span><strong>{data.limits.max_query_units_per_plan.toLocaleString("en")}</strong> max query units</span>
                <span><strong>{formatBytes(data.limits.max_storage_bytes_per_plan)}</strong> max storage</span>
                <span><strong>Unknown</strong> source price</span>
              </div>
            </section>
          </div>

          <div className="audit-workspace-plan-actions">
            <div><strong>{selectedTaskTotal.toLocaleString("en")} tasks</strong><span> · {sources.length} source groups · {stageCount} sizing stage{stageCount === 1 ? "" : "s"}</span></div>
            <button type="button" className="audit-workspace-primary" disabled={!planCanRun || isPlanning} onClick={() => void buildResourcePlan()}>{isPlanning ? "Calculating…" : "Calculate resource preflight"}<span aria-hidden="true">→</span></button>
          </div>
          {selectedTaskTotal > data.limits.max_tasks_per_plan ? <p className="audit-workspace-form-warning" role="alert">Selected tasks exceed the catalog cap of {data.limits.max_tasks_per_plan}.</p> : null}
          {sources.length > data.limits.max_source_groups_per_plan ? <p className="audit-workspace-form-warning" role="alert">Selected source groups exceed the cap of {data.limits.max_source_groups_per_plan}.</p> : null}
          {stageCount > data.limits.max_stages_per_task ? <p className="audit-workspace-form-warning" role="alert">Stage count exceeds the cap of {data.limits.max_stages_per_task}.</p> : null}
          {exceedsQueryCap ? <p className="audit-workspace-form-warning" role="alert">This selection would exceed the {data.limits.max_query_units_per_plan.toLocaleString("en")} query unit cap.</p> : null}
          {exceedsStorageCap ? <p className="audit-workspace-form-warning" role="alert">This estimate exceeds the {formatBytes(data.limits.max_storage_bytes_per_plan)} storage cap.</p> : null}
          {planResult ? <PlanResult resource={planResult} /> : null}
          <p className="audit-workspace-legal-note">This estimate does not authorize source access, imports, model calls, query dispatch, persistence, or publication. Live scans remain unavailable until source rights, connectors, independent review, and an authorized transition service are in place.</p>
        </section>
      )}
    </>
  );
}

function AuditBenchmarkRows({
  row,
  expanded,
  count,
  maxTasks,
  onExpand,
  onCount,
  onAdd,
}: {
  row: BenchmarkScopeRow;
  expanded: boolean;
  count: number;
  maxTasks: number;
  onExpand: () => void;
  onCount: (value: string) => void;
  onAdd: () => void;
}) {
  return <>
    <tr className={expanded ? "is-expanded" : ""}>
      <td><button type="button" className="audit-workspace-benchmark-name" aria-expanded={expanded} onClick={onExpand}><span className="audit-workspace-expand-icon" aria-hidden="true">{expanded ? "−" : "+"}</span><span><strong>{row.name}</strong><small>{row.benchmark_slug}</small></span></button></td>
      <td><span className="audit-workspace-family-chip">{label(row.family)}</span></td>
      <td><span className="audit-workspace-cell-primary">{label(row.version_state)}</span><small className="audit-workspace-cell-muted">{label(row.split_state)}</small></td>
      <td><span className="audit-workspace-cell-primary">{label(row.rights_state)}</span></td>
      <td><span className={`audit-workspace-status-pill importer-${row.importer_state.replaceAll("_", "-")}`}>{label(row.importer_state)}</span></td>
      <td><span className={`audit-workspace-live-pill live-${row.live_state.replaceAll("_", "-")}`}><i aria-hidden="true" />{liveStateLabels[row.live_state]}</span><small className="audit-workspace-cell-muted">{row.live_blockers.length ? `${row.live_blockers.length} criteria open` : "All criteria evidenced"}</small></td>
      <td><label className="audit-workspace-plan-count"><span className="sr-only">Task count for {row.name}</span><input type="number" min={0} max={maxTasks} value={count || ""} placeholder="Add" onChange={(event) => onCount(event.target.value)} /></label></td>
    </tr>
    {expanded ? <tr className="audit-workspace-detail-row"><td colSpan={7}><BenchmarkDetails row={row} onAdd={onAdd} /></td></tr> : null}
  </>;
}

function BenchmarkDetails({ row, onAdd }: { row: BenchmarkScopeRow; onAdd: () => void }) {
  const criteria = [
    ["Exact dataset revision", row.version_state === "dataset_version_pinned"],
    ["Official source revision pinned", row.source_pins.length > 0],
    ["Pinned split", row.split_state === "pinned"],
    ["Access available for declared scope", row.access_state !== "gated" && row.access_state !== "owner_supplied"],
    ["Rights approved for scope", row.rights_state === "approved_for_declared_scope"],
    ["Audit importer conformant", row.importer_state === "audit_conformant"],
    ["Declared components supported", row.unsupported_components.length === 0],
    ["Required modalities supported", row.unsupported_modalities.length === 0],
    ["Runtime conformant", row.runtime_state === "conformant"],
    ["Live conformance evidence", row.conformance_state === "live_verified"],
  ] as const;
  return <div className="audit-workspace-detail-card">
    <div className="audit-workspace-detail-top">
      <div><p className="audit-workspace-kicker">Scope criteria</p><h3>{row.name}</h3><p>{row.live_state === "live_verified" ? "Catalog reports live conformance for the declared scope." : "Catalog evidence is incomplete. These are import and conformance readiness gates, not overlap or leakage detector findings."}</p></div>
      <button type="button" className="audit-workspace-primary audit-workspace-small-primary" onClick={onAdd}>Add to preflight <span aria-hidden="true">→</span></button>
    </div>
    <div className="audit-workspace-detail-grid">
      <div className="audit-workspace-criteria-list">
        <h4>Readiness criteria</h4>
        {criteria.map(([title, passed]) => <div className={`audit-workspace-criterion ${passed ? "is-passed" : "is-open"}`} key={title}><span aria-hidden="true">{passed ? "✓" : "·"}</span><span>{title}</span><strong>{passed ? "Met" : "Open"}</strong></div>)}
      </div>
      <div className="audit-workspace-scope-list">
        <h4>Declared scope</h4>
        <p><strong>Components</strong><span>{row.component_scope.map(label).join(", ")}</span></p>
        <p><strong>Unsupported components</strong><span>{row.unsupported_components.length ? row.unsupported_components.map(label).join(", ") : "None listed"}</span></p>
        <p><strong>Modalities</strong><span>{row.required_modalities.map(label).join(", ")}</span></p>
        <p><strong>Source groups</strong><span>{row.source_groups.map(label).join(", ")}</span></p>
        <p><strong>Dataset version</strong><span>{row.version ?? "Not pinned"}</span></p>
        <p><strong>Split policy</strong><span>{row.split_policy}</span></p>
        <p><strong>Evidence</strong><span>{row.fixture_evidence_state === "synthetic_source_fixture" ? "Synthetic parser fixture; not live source evidence" : "Catalog contract only"}</span></p>
        <a href={row.official_url} target="_blank" rel="noreferrer">Open official benchmark source <span aria-hidden="true">↗</span></a>
      </div>
    </div>
    {row.live_blockers.length ? <div className="audit-workspace-open-items"><strong>Open criteria from backend</strong><ul>{row.live_blockers.map((item) => <li key={item}>{label(item)}</li>)}</ul></div> : null}
  </div>;
}

function PlanResult({ resource }: { resource: Resource<AuditResourcePlanResult> }) {
  if (resource.state !== "ready") {
    const message = resource.state === "error" || resource.state === "empty" ? resource.message : "The estimate is being calculated.";
    return <div className="audit-workspace-plan-error" role="alert"><strong>{resource.state === "error" ? resource.title : "Preflight unavailable"}</strong><p>{message}</p></div>;
  }
  const plan = resource.value.data;
  return <section className="audit-workspace-plan-result" aria-labelledby="plan-result-title" aria-live="polite">
    <div className="audit-workspace-plan-result-heading">
      <div><p className="audit-workspace-kicker">Backend resource estimate · {plan.plan_version}</p><h3 id="plan-result-title">Preflight {label(plan.state)}</h3><p>This result is not a containment scan or an authorization to run one.</p></div>
      <span className="audit-workspace-no-dispatch">Dispatch allowed: no</span>
    </div>
    <div className="audit-workspace-plan-stats">
      <Metric label="Tasks" value={plan.total_tasks.toLocaleString("en")} note="selected in request" icon="▦" />
      <Metric label="Query ceiling" value={plan.query_ceiling.toLocaleString("en")} note="planned units" icon="⌁" />
      <Metric label="Candidate ceiling" value={plan.candidate_ceiling.toLocaleString("en")} note="maximum candidates" icon="◎" />
      <Metric label="Estimated storage" value={plan.estimated_storage_bytes === null ? "Unknown" : formatBytes(plan.estimated_storage_bytes)} note={plan.estimated_storage_bytes === null ? "supply average item size" : "under configured cap"} icon="▤" />
    </div>
    <div className="audit-workspace-plan-columns">
      <div><h4>Benchmark readiness</h4>{plan.benchmark_plans.map((item) => <article className="audit-workspace-plan-line" key={item.benchmark_slug}><div><strong>{label(item.benchmark_slug)}</strong><span>{item.selected_tasks} tasks · {label(item.state)}</span></div>{item.blockers.length ? <ul>{item.blockers.map((blocker) => <li key={blocker}>{blocker}</li>)}</ul> : <p>No catalog blockers listed.</p>}</article>)}</div>
      <div><h4>Source readiness</h4>{plan.source_plans.map((item) => <article className="audit-workspace-plan-line" key={item.source_group}><div><strong>{label(item.source_group)}</strong><span>{item.planned_query_units} / {item.request_cap} request units · {label(item.state)}</span></div>{item.blockers.length ? <ul>{item.blockers.map((blocker) => <li key={blocker}>{blocker}</li>)}</ul> : <p>No catalog blockers listed.</p>}</article>)}</div>
    </div>
    <div className="audit-workspace-plan-blockers"><h4>Plan-wide blockers</h4>{plan.blockers.length ? <ul>{plan.blockers.map((blocker) => <li key={blocker}>{blocker}</li>)}</ul> : <p>No plan-wide blockers listed. This still does not authorize dispatch.</p>}</div>
    <div className="audit-workspace-plan-foot"><span>Price: {label(plan.cost_state)} · model call ceiling: {plan.model_call_ceiling}</span><code>Policy {plan.policy_version}</code></div>
  </section>;
}

function Metric({ label: title, value, note, icon }: { label: string; value: string; note: string; icon: string }) {
  return <article className="audit-workspace-metric"><span className="audit-workspace-metric-icon" aria-hidden="true">{icon}</span><div><span className="audit-workspace-metric-label">{title}</span><strong>{value}</strong><small>{note}</small></div></article>;
}

function LifecycleStep({ number, title, state, detail }: { number: string; title: string; state: "available" | "blocked" | "read-only"; detail: string }) {
  const stateLabel = state === "read-only" ? "Read only" : label(state);
  return <article className={`audit-workspace-lifecycle-step is-${state}`}>
    <span className="audit-workspace-lifecycle-number">{number}</span>
    <div><div className="audit-workspace-lifecycle-heading"><h3>{title}</h3><span>{stateLabel}</span></div><p>{detail}</p></div>
  </article>;
}

function label(value: string): string {
  return value.replaceAll("_", " ").replaceAll("-", " ");
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toLocaleString("en", { maximumFractionDigits: 1 })} ${units[index]}`;
}
