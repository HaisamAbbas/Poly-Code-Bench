"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

const REPORT_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function AuditReportLookupForm({ invalidQuery = false }: { invalidQuery?: boolean }) {
  const router = useRouter();
  const [hasInvalidId, setHasInvalidId] = useState(false);
  const [isOpening, setIsOpening] = useState(false);

  function openReport(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = new FormData(event.currentTarget).get("report_id");
    if (typeof value !== "string" || !REPORT_ID.test(value.trim())) {
      setHasInvalidId(true);
      return;
    }
    setHasInvalidId(false);
    setIsOpening(true);
    router.push(`/audit-reports/${encodeURIComponent(value.trim())}`);
  }

  return (
    <form action="/audit-reports" method="get" className="audit-lookup-form" onSubmit={openReport}>
      <label htmlFor="audit-report-id">Public report ID</label>
      <input
        id="audit-report-id"
        name="report_id"
        type="text"
        inputMode="text"
        autoComplete="off"
        maxLength={36}
        pattern="[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
        aria-describedby="audit-report-id-help"
        required
        onChange={() => setHasInvalidId(false)}
      />
      <p id="audit-report-id-help">Enter a complete UUID. The report page shows only the public allowlisted projection.</p>
      {invalidQuery || hasInvalidId ? (
        <p className="audit-form-error" role="alert">Enter one valid public report ID in UUID format.</p>
      ) : null}
      {isOpening ? <p role="status" aria-live="polite" aria-busy="true">Loading public report; retrieving the reviewed aggregate.</p> : null}
      <button type="submit" disabled={isOpening}>{isOpening ? "Opening report…" : "Open report"}</button>
    </form>
  );
}
