import type { Metadata } from "next";
import Link from "next/link";
import { AuditAdminAccess } from "@/components/audit-admin-access";
import { PageIntro } from "@/components/public-ui";
import { getAuditAdminAccessState } from "@/lib/audit-admin-access";

export const metadata: Metadata = {
  title: "Admin testing console",
  description: "Private controls and status for PolyCodeBench internal evaluation workflows.",
};

export const dynamic = "force-dynamic";

export default async function AdminPage() {
  const access = await getAuditAdminAccessState();
  if (access !== "allowed") return <AuditAdminAccess state={access} returnTo="/admin" />;

  return (
    <>
      <PageIntro eyebrow="Private operations" title="Admin testing console" description="Internal evaluation controls stay behind your tenant-scoped operator account. Only reviewed, published results appear on the public site." />
      <section className="audit-workspace-panel admin-testing-console" aria-labelledby="admin-testing-title">
        <div className="audit-workspace-section-heading">
          <div>
            <p className="audit-workspace-kicker">Internal testing</p>
            <h2 id="admin-testing-title">Testing workspaces</h2>
            <p>Current backend capabilities are shown as they exist today; blocked stages are not presented as runnable tests.</p>
          </div>
          <span className="audit-workspace-lifecycle-state">Private to authorized operators</span>
        </div>
        <div className="audit-workspace-lifecycle-grid">
          <article className="audit-workspace-lifecycle-step is-blocked">
            <span className="audit-workspace-lifecycle-number">01</span>
            <div>
              <div className="audit-workspace-lifecycle-heading"><h3>Code-strength evaluation</h3><span>Web workflow not connected</span></div>
              <p>The backend can create a code-evaluation run during MFA-protected model approval, but this console has no review, approval, or run-status controls yet.</p>
            </div>
          </article>
          <article className="audit-workspace-lifecycle-step is-blocked">
            <span className="audit-workspace-lifecycle-number">02</span>
            <div>
              <div className="audit-workspace-lifecycle-heading"><h3>Repository testing</h3><span>Run control unavailable</span></div>
              <p>No private repository-test launch or status workflow is wired into this web console yet.</p>
            </div>
          </article>
          <article className="audit-workspace-lifecycle-step is-available">
            <span className="audit-workspace-lifecycle-number">03</span>
            <div>
              <div className="audit-workspace-lifecycle-heading"><h3>Benchmark containment</h3><span>Catalog and preflight</span></div>
              <p>Inspect declared benchmark scope and calculate a bounded no-dispatch resource estimate. Live scans cannot be launched.</p>
              <Link className="audit-workspace-inline-link" href="/admin/benchmark-audit">Open benchmark workspace <span aria-hidden="true">→</span></Link>
            </div>
          </article>
        </div>
        <p className="audit-workspace-legal-note">The public explorer remains read-only. Results reach it only through the existing reviewed release and publication flow.</p>
      </section>
    </>
  );
}
