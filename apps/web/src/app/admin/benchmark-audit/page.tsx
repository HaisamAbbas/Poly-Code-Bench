import type { Metadata } from "next";
import { AuditAdminAccess } from "@/components/audit-admin-access";
import { BenchmarkAuditWorkspace } from "@/components/benchmark-audit-workspace";
import { getAuditAdminAccessState } from "@/lib/audit-admin-access";

export const metadata: Metadata = {
  title: "Admin benchmark audit",
  description: "Private benchmark scope and containment preflight controls.",
};

export const dynamic = "force-dynamic";

export default async function AdminBenchmarkAuditPage() {
  const access = await getAuditAdminAccessState();
  if (access !== "allowed") return <AuditAdminAccess state={access} returnTo="/admin/benchmark-audit" />;
  return <BenchmarkAuditWorkspace />;
}
