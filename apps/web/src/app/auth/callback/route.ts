import { NextResponse } from "next/server";
import {
  cookieIsSecure,
  cookieValue,
  exchangeCodeForSession,
  flowCookieName,
  oidcSettings,
  readOidcFlow,
  sessionCookieName,
  sessionTtlSeconds,
  signedCookieOptions,
} from "@/lib/oidc-auth";

export const runtime = "nodejs";

export async function GET(request: Request): Promise<Response> {
  const requestUrl = new URL(request.url);
  const code = requestUrl.searchParams.get("code");
  const state = requestUrl.searchParams.get("state");
  const flowToken = cookieValue(request.headers.get("cookie"), safeFlowCookieName());
  const flow = state ? readOidcFlow(flowToken, state) : null;
  if (!flow || requestUrl.searchParams.has("error") || !code) return callbackFailure();

  try {
    const settings = oidcSettings();
    if (requestUrl.pathname !== "/auth/callback") return callbackFailure();
    const { sessionToken } = await exchangeCodeForSession(code, flow);
    const response = NextResponse.redirect(new URL(flow.returnTo, settings.webOrigin), 303);
    response.headers.set("cache-control", "no-store");
    response.headers.set("referrer-policy", "no-referrer");
    response.cookies.set(sessionCookieName(), sessionToken, signedCookieOptions(cookieIsSecure(), sessionTtlSeconds));
    response.cookies.set(flowCookieName(), "", signedCookieOptions(cookieIsSecure(), 0));
    return response;
  } catch {
    return callbackFailure();
  }
}

function safeFlowCookieName(): string {
  try { return flowCookieName(); } catch { return "pcb-oidc-flow-dev"; }
}

function callbackFailure(): Response {
  const response = new NextResponse(null, {
    status: 303,
    headers: {
      location: "/model-submissions?auth_error=sign_in_failed",
      "cache-control": "no-store",
      "referrer-policy": "no-referrer",
    },
  });
  try {
    response.cookies.set(flowCookieName(), "", signedCookieOptions(cookieIsSecure(), 0));
  } catch {
    // There is no flow cookie to clear when the OIDC deployment configuration is absent.
  }
  return response;
}
