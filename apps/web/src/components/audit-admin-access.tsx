import Link from "next/link";
import type { AuditAdminAccessState } from "@/lib/audit-admin-access";

export function AuditAdminAccess({ state, returnTo }: { state: Exclude<AuditAdminAccessState, "allowed">; returnTo: string }) {
  const title = state === "not_configured"
    ? "Admin sign-in is not configured"
    : state === "sign_in_required"
      ? "Sign in to the admin area"
      : "This account has no admin grant";
  const message = state === "not_configured"
    ? "This deployment has no OIDC sign-in configuration. An operator must configure the identity provider and map your exact issuer and subject to a tenant before private tools are available."
    : state === "sign_in_required"
      ? "Sign in with your verified operator account. The server checks its tenant-scoped admin grant before loading internal tools."
      : "Your verified account is not listed in the server-side tenant-scoped audit access map. Ask the deployment owner to grant your exact OIDC subject access.";

  return (
    <section className="audit-workspace-state" role="status">
      <span className="audit-workspace-kicker">Private admin</span>
      <h2>{title}</h2>
      <p>{message}</p>
      {state === "sign_in_required" ? <p><Link href={`/auth/sign-in?return_to=${encodeURIComponent(returnTo)}`}>Sign in to continue</Link></p> : null}
      <p><Link href="/leaderboard">Return to public results</Link></p>
    </section>
  );
}
