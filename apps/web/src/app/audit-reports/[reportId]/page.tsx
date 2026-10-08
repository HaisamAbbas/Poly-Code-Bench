import type { Metadata } from "next";
import { PublicAuditReportState } from "@/components/benchmark-audit";
import { loadPublicBenchmarkHealth, isPublicReportId } from "@/lib/benchmark-audit";

export const metadata: Metadata = {
  title: "Benchmark health report",
  description: "Reviewed aggregate benchmark audit scope, coverage, unknowns, and evidence limits.",
};

export default async function PublicAuditReportPage({
  params,
}: {
  params: Promise<{ reportId: string }>;
}) {
  const { reportId } = await params;
  const resource = isPublicReportId(reportId)
    ? await loadPublicBenchmarkHealth(reportId)
    : {
        state: "error" as const,
        title: "Report ID is invalid",
        message: "Enter one complete public report ID in UUID format.",
      };

  return <PublicAuditReportState resource={resource} />;
}
