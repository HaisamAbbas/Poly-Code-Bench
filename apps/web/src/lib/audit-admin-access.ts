import "server-only";

import { cookies } from "next/headers";
import {
  oidcConfigured,
  readSessionToken,
  sessionCookieName,
  webAuditOperatorTenant,
} from "@/lib/oidc-auth";

export type AuditAdminAccessState = "not_configured" | "sign_in_required" | "not_authorized" | "allowed";

export async function getAuditAdminAccessState(): Promise<AuditAdminAccessState> {
  if (!oidcConfigured()) return "not_configured";

  const cookieStore = await cookies();
  const session = readSessionToken(cookieStore.get(sessionCookieName())?.value);
  if (!session) return "sign_in_required";
  return webAuditOperatorTenant(session.subject) ? "allowed" : "not_authorized";
}
