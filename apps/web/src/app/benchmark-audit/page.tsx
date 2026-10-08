import type { Metadata } from "next";
import { CuratorAccessBoundary } from "@/components/benchmark-audit";

export const metadata: Metadata = {
  title: "Curator access",
  description: "Private benchmark audit controls require a tenant-bound curator identity and reviewed transitions.",
};

export default function BenchmarkAuditPage() {
  return <CuratorAccessBoundary />;
}
