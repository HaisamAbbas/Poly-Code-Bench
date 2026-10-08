import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { AuditReportLookupForm } from "@/components/audit-report-lookup-form";
import { PageIntro } from "@/components/public-ui";
import { isPublicReportId } from "@/lib/benchmark-audit";

export const metadata: Metadata = {
  title: "Public benchmark audit reports",
  description: "Open a reviewed aggregate benchmark health report by its public report ID.",
};

type AuditReportSearchParams = Promise<{
  report_id?: string | string[];
}>;

export default async function AuditReportsPage({
  searchParams,
}: {
  searchParams: AuditReportSearchParams;
}) {
  const reportIdParam = (await searchParams).report_id;
  const reportId = typeof reportIdParam === "string" ? reportIdParam.trim() : "";
  const hasInvalidQuery = reportIdParam !== undefined && !isPublicReportId(reportId);

  if (reportId && isPublicReportId(reportId)) {
    redirect(`/audit-reports/${encodeURIComponent(reportId)}`);
  }

  return (
    <>
      <PageIntro
        eyebrow="Public benchmark audit"
        title="Open an audit report"
        description="Public reports show reviewed aggregate scope and evidence limits. They do not expose individual task content or model-specific claims without reviewed evidence."
      />
      <section className="section-card audit-lookup-card" aria-labelledby="audit-lookup-heading">
        <h2 id="audit-lookup-heading">Report lookup</h2>
        <p>The public report catalog is not available yet. Open a report using its public ID from an approved report link.</p>
        <AuditReportLookupForm invalidQuery={hasInvalidQuery} />
      </section>
    </>
  );
}
