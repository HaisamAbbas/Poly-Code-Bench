import { NextResponse } from "next/server";
import {
  cookieIsSecure,
  flowCookieName,
  isSameOriginMutation,
  oidcSettings,
  sessionCookieName,
  signedCookieOptions,
} from "@/lib/oidc-auth";

export const runtime = "nodejs";

export async function POST(request: Request): Promise<Response> {
  if (!isSameOriginMutation(request)) {
    return Response.json(
      { error: { code: "FORBIDDEN", message: "request origin is not allowed" } },
      { status: 403, headers: { "cache-control": "no-store" } },
    );
  }
  try {
    const settings = oidcSettings();
    const response = NextResponse.redirect(new URL("/model-submissions", settings.webOrigin), 303);
    response.headers.set("cache-control", "no-store");
    response.cookies.set(sessionCookieName(), "", signedCookieOptions(cookieIsSecure(), 0));
    response.cookies.set(flowCookieName(), "", signedCookieOptions(cookieIsSecure(), 0));
    return response;
  } catch {
    return Response.json(
      { error: { code: "SIGN_OUT_UNAVAILABLE", message: "account sign-out is unavailable" } },
      { status: 503, headers: { "cache-control": "no-store" } },
    );
  }
}
