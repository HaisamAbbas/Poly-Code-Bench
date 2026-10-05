import { NextResponse } from "next/server";
import {
  cookieIsSecure,
  createAuthorizationUrl,
  flowCookieName,
  flowTtlSeconds,
  oidcSettings,
  safeReturnTo,
  signedCookieOptions,
} from "@/lib/oidc-auth";

export const runtime = "nodejs";

export async function GET(request: Request): Promise<Response> {
  try {
    const settings = oidcSettings();
    const url = new URL(request.url);
    const returnTo = url.searchParams.get("return_to") ?? "/model-submissions";
    const loginHint = url.searchParams.get("login_hint") ?? undefined;
    if (!safeReturnTo(returnTo, settings.webOrigin)) return errorResponse(400);
    if (loginHint && (loginHint.length > 320 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(loginHint))) {
      return errorResponse(400);
    }
    const { authorizationUrl, flowToken } = await createAuthorizationUrl(returnTo, loginHint);
    const response = NextResponse.redirect(authorizationUrl, 302);
    response.headers.set("cache-control", "no-store");
    response.headers.set("referrer-policy", "no-referrer");
    response.cookies.set(flowCookieName(), flowToken, signedCookieOptions(cookieIsSecure(), flowTtlSeconds));
    return response;
  } catch {
    return errorResponse(503);
  }
}

function errorResponse(status: number): Response {
  return Response.json(
    { error: { code: status === 400 ? "INVALID_SIGN_IN_REQUEST" : "SIGN_IN_UNAVAILABLE", message: "Account sign-in is unavailable." } },
    { status, headers: { "cache-control": "no-store", "referrer-policy": "no-referrer" } },
  );
}
