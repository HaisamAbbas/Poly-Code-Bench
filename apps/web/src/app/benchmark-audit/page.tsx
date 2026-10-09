import type { Metadata } from "next";
import { BenchmarkAuditWorkspace } from "@/components/benchmark-audit-workspace";

export const metadata: Metadata = {
  title: "Benchmark audit workspace",
  description: "Explore benchmark scope readiness and build a bounded no-dispatch resource preflight.",
};

export default function BenchmarkAuditPage() {
  return <BenchmarkAuditWorkspace />;
}
