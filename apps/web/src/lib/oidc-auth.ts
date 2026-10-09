import "server-only";

import {
  createHash,
  createHmac,
  createPublicKey,
  randomBytes,
  timingSafeEqual,
  verify as verifySignature,
} from "node:crypto";

const SESSION_ISSUER = "polycodebench-oidc-session";
const SESSION_AUDIENCE = "polycodebench-web";
const FLOW_ISSUER = "polycodebench-oidc-flow";
const FLOW_AUDIENCE = "polycodebench-web";
const API_ISSUER = "polycodebench-web";
const API_AUDIENCE = "polycodebench-api";
const SESSION_TTL_SECONDS = 8 * 60 * 60;
const FLOW_TTL_SECONDS = 10 * 60;
const API_TTL_SECONDS = 5 * 60;

export type WebSession = {
  readonly subject: string;
  readonly email: string;
  readonly issuer: string;
  readonly issuedAt: number;
  readonly expiresAt: number;
};

export type OidcFlow = {
  readonly state: string;
  readonly nonce: string;
  readonly verifier: string;
  readonly returnTo: string;
  readonly issuer: string;
  readonly clientId: string;
};

type OidcSettings = {
  readonly issuer: string;
  readonly clientId: string;
  readonly clientSecret?: string;
  readonly redirectUri: string;
  readonly webOrigin: string;
  readonly signingKey: Buffer;
  readonly secureCookies: boolean;
};

type JsonObject = Record<string, unknown>;

export const SESSION_COOKIE = "__Host-pcb-session";
export const SESSION_COOKIE_DEV = "pcb-session-dev";
export const FLOW_COOKIE = "__Host-pcb-oidc-flow";
export const FLOW_COOKIE_DEV = "pcb-oidc-flow-dev";

export function oidcConfigured(): boolean {
  try {
    oidcSettings();
    return true;
  } catch {
    return false;
  }
}

export function oidcSettings(): OidcSettings {
  const issuer = process.env.PCB_OIDC_ISSUER?.trim();
  const clientId = process.env.PCB_OIDC_CLIENT_ID?.trim();
  const clientSecret = process.env.PCB_OIDC_CLIENT_SECRET;
  const redirectUri = process.env.PCB_OIDC_REDIRECT_URI?.trim();
  const signingSecret = process.env.PCB_WEB_AUTH_SIGNING_KEY;
  if (!issuer || !clientId || !redirectUri || !signingSecret) {
    throw new Error("OIDC login is not configured");
  }
  const signingKey = Buffer.from(signingSecret, "utf8");
  if (signingKey.byteLength < 32) throw new Error("OIDC signing configuration is invalid");

  const production =
    process.env.PCB_ENVIRONMENT === "staging" ||
    process.env.PCB_ENVIRONMENT === "production" ||
    process.env.NODE_ENV === "production";
  trustedUrl(issuer, production);
  const redirect = trustedUrl(redirectUri, production);
  const configuredOrigin = process.env.PCB_WEB_ORIGIN?.trim();
  const configuredWebUrl = configuredOrigin ? trustedUrl(configuredOrigin, production) : null;
  const webOrigin = configuredWebUrl ? configuredWebUrl.origin : redirect.origin;
  if (
    redirect.origin !== webOrigin ||
    redirect.pathname !== "/auth/callback" ||
    redirect.search ||
    redirect.hash ||
    (configuredWebUrl !== null && (configuredWebUrl.pathname !== "/" || configuredWebUrl.search || configuredWebUrl.hash)) ||
    clientId.length > 512 ||
    /[\r\n]/.test(clientId) ||
    (clientSecret !== undefined && (clientSecret.length > 4096 || /[\r\n]/.test(clientSecret)))
  ) {
    throw new Error("OIDC configuration is invalid");
  }
  return {
    issuer,
    clientId,
    ...(clientSecret ? { clientSecret } : {}),
    redirectUri: redirect.toString(),
    webOrigin,
    signingKey,
    secureCookies: production || redirect.protocol === "https:",
  };
}

export function sessionCookieName(): string {
  return oidcSettings().secureCookies ? SESSION_COOKIE : SESSION_COOKIE_DEV;
}

export function flowCookieName(): string {
  return oidcSettings().secureCookies ? FLOW_COOKIE : FLOW_COOKIE_DEV;
}

export function cookieIsSecure(): boolean {
  return oidcSettings().secureCookies;
}

export function getExpectedWebOrigin(request: Request): string {
  if (process.env.PCB_WEB_ORIGIN?.trim()) {
    const configured = trustedUrl(process.env.PCB_WEB_ORIGIN.trim(), isProduction());
    if (configured.pathname !== "/") throw new Error("PCB_WEB_ORIGIN must not include a path");
    return configured.origin;
  }
  if (isProduction()) throw new Error("PCB_WEB_ORIGIN is required in production");
  return new URL(request.url).origin;
}

export function isSameOriginMutation(request: Request): boolean {
  const origin = request.headers.get("origin");
  if (!origin) return false;
  try {
    return new URL(origin).origin === getExpectedWebOrigin(request);
  } catch {
    return false;
  }
}

function newOidcFlowToken(returnTo: string): string {
  const settings = oidcSettings();
  const state = randomToken();
  const nonce = randomToken();
  const verifier = randomToken(48);
  const flowToken = signJwt(
    {
      iss: FLOW_ISSUER,
      aud: FLOW_AUDIENCE,
      state,
      nonce,
      verifier,
      return_to: returnTo,
      provider_issuer: settings.issuer,
      client_id: settings.clientId,
      iat: nowSeconds(),
      exp: nowSeconds() + FLOW_TTL_SECONDS,
    },
    settings.signingKey,
  );
  return flowToken;
}

export async function createAuthorizationUrl(
  returnTo: string,
  loginHint?: string,
): Promise<{ readonly authorizationUrl: URL; readonly flowToken: string }> {
  const settings = oidcSettings();
  const flowToken = newOidcFlowToken(returnTo);
  const metadata = await oidcMetadata(settings);
  const claims = verifyLocalJwt(flowToken, settings.signingKey, FLOW_ISSUER, FLOW_AUDIENCE);
  const authorizationUrl = new URL(metadata.authorization_endpoint as string);
  authorizationUrl.searchParams.set("client_id", settings.clientId);
  authorizationUrl.searchParams.set("response_type", "code");
  authorizationUrl.searchParams.set("redirect_uri", settings.redirectUri);
  authorizationUrl.searchParams.set("scope", "openid email profile");
  authorizationUrl.searchParams.set("state", claims.state as string);
  authorizationUrl.searchParams.set("nonce", claims.nonce as string);
  authorizationUrl.searchParams.set("code_challenge", b64url(createHash("sha256").update(claims.verifier as string).digest()));
  authorizationUrl.searchParams.set("code_challenge_method", "S256");
  if (loginHint) authorizationUrl.searchParams.set("login_hint", loginHint);
  return { authorizationUrl, flowToken };
}

export function readOidcFlow(token: string | undefined, state: string): OidcFlow | null {
  if (!token || !state) return null;
  try {
    const settings = oidcSettings();
    const claims = verifyLocalJwt(token, settings.signingKey, FLOW_ISSUER, FLOW_AUDIENCE);
    if (
      claims.state !== state ||
      claims.provider_issuer !== settings.issuer ||
      claims.client_id !== settings.clientId ||
      typeof claims.nonce !== "string" ||
      typeof claims.verifier !== "string" ||
      typeof claims.return_to !== "string" ||
      !safeReturnTo(claims.return_to, settings.webOrigin)
    ) return null;
    return {
      state,
      nonce: claims.nonce,
      verifier: claims.verifier,
      returnTo: claims.return_to,
      issuer: settings.issuer,
      clientId: settings.clientId,
    };
  } catch {
    return null;
  }
}

export async function exchangeCodeForSession(code: string, flow: OidcFlow): Promise<{
  readonly sessionToken: string;
  readonly session: WebSession;
}> {
  const settings = oidcSettings();
  if (!code || code.length > 4096 || /[\r\n]/.test(code)) throw new Error("OIDC callback is invalid");
  const metadata = await oidcMetadata(settings);
  const body = new URLSearchParams({
    grant_type: "authorization_code",
    code,
    redirect_uri: settings.redirectUri,
    client_id: settings.clientId,
    code_verifier: flow.verifier,
  });
  const headers = new Headers({ accept: "application/json", "content-type": "application/x-www-form-urlencoded" });
  if (settings.clientSecret) {
    const methods = Array.isArray(metadata.token_endpoint_auth_methods_supported)
      ? metadata.token_endpoint_auth_methods_supported
      : ["client_secret_basic"];
    if (methods.includes("client_secret_post")) {
      body.set("client_secret", settings.clientSecret);
    } else if (methods.includes("client_secret_basic")) {
      const basic = `${formEncode(settings.clientId)}:${formEncode(settings.clientSecret)}`;
      headers.set("authorization", `Basic ${Buffer.from(basic).toString("base64")}`);
      body.delete("client_id");
    } else {
      throw new Error("OIDC provider does not support the configured client authentication");
    }
  }
  const response = await fetch(metadata.token_endpoint as string, {
    method: "POST",
    headers,
    body,
    cache: "no-store",
    signal: AbortSignal.timeout(8000),
  });
  const tokenResponse = await responseJson(response, 64_000);
  if (!response.ok || typeof tokenResponse.id_token !== "string") throw new Error("OIDC token exchange failed");
  const claims = await verifyIdentityToken(tokenResponse.id_token, settings, flow.nonce, metadata.jwks_uri as string);
  const issuedAt = nowSeconds();
  const expiresAt = issuedAt + SESSION_TTL_SECONDS;
  const session: WebSession = {
    subject: `${settings.issuer}|${claims.sub as string}`,
    email: claims.email as string,
    issuer: settings.issuer,
    issuedAt,
    expiresAt,
  };
  const sessionToken = signJwt(
    {
      iss: SESSION_ISSUER,
      aud: SESSION_AUDIENCE,
      sub: session.subject,
      email: session.email,
      email_verified: true,
      oidc_issuer: session.issuer,
      iat: issuedAt,
      exp: expiresAt,
    },
    settings.signingKey,
  );
  return { sessionToken, session };
}

export function readSessionToken(token: string | undefined): WebSession | null {
  if (!token) return null;
  try {
    const settings = oidcSettings();
    const claims = verifyLocalJwt(token, settings.signingKey, SESSION_ISSUER, SESSION_AUDIENCE);
    if (
      typeof claims.sub !== "string" ||
      !claims.sub ||
      typeof claims.email !== "string" ||
      !validEmail(claims.email) ||
      claims.email_verified !== true ||
      claims.oidc_issuer !== settings.issuer ||
      typeof claims.iat !== "number" ||
      typeof claims.exp !== "number" ||
      claims.exp - claims.iat > SESSION_TTL_SECONDS
    ) return null;
    return {
      subject: claims.sub,
      email: claims.email,
      issuer: settings.issuer,
      issuedAt: claims.iat,
      expiresAt: claims.exp,
    };
  } catch {
    return null;
  }
}

export function createApiBearer(session: WebSession): string {
  const settings = oidcSettings();
  const issuedAt = nowSeconds();
  const auditTenantId = webAuditOperatorTenant(session.subject);
  return signJwt(
    {
      iss: API_ISSUER,
      aud: API_AUDIENCE,
      sub: session.subject,
      roles: auditTenantId ? ["submitter", "operator"] : ["submitter"],
      ...(auditTenantId ? { tenant_id: auditTenantId } : {}),
      email: session.email,
      email_verified: true,
      iat: issuedAt,
      exp: Math.min(issuedAt + API_TTL_SECONDS, session.expiresAt),
      jti: randomToken(),
    },
    settings.signingKey,
  );
}

/** Return a tenant only for an exact, server-configured OIDC subject grant. */
export function webAuditOperatorTenant(subject: string): string | null {
  const raw = process.env.PCB_WEB_AUDIT_OPERATOR_ACCESS_JSON;
  if (!raw || raw.length > 256_000) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!isJsonObject(parsed) || !hasExactKeys(parsed, ["schema_version", "principals"]) || parsed.schema_version !== 1) return null;
    if (!Array.isArray(parsed.principals) || parsed.principals.length > 1000) return null;
    const seen = new Set<string>();
    let matchedTenant: string | null = null;
    for (const row of parsed.principals) {
      if (!isJsonObject(row) || !hasExactKeys(row, ["subject", "tenant_id"])) return null;
      if (
        typeof row.subject !== "string" || !row.subject || row.subject.length > 512 || row.subject.trim() !== row.subject ||
        typeof row.tenant_id !== "string" || !isUuid(row.tenant_id) || seen.has(row.subject)
      ) return null;
      seen.add(row.subject);
      if (row.subject === subject) matchedTenant = row.tenant_id.toLowerCase();
    }
    return matchedTenant;
  } catch {
    return null;
  }
}

export async function getCurrentSession(cookieHeader: string | null): Promise<WebSession | null> {
  const token = cookieValue(cookieHeader, sessionCookieName());
  return readSessionToken(token);
}

export function safeReturnTo(value: string, origin: string): boolean {
  if (!value.startsWith("/") || value.startsWith("//") || value.includes("\\") || /[\u0000-\u001f]/.test(value)) {
    return false;
  }
  try {
    return new URL(value, origin).origin === origin;
  } catch {
    return false;
  }
}

export function cookieValue(header: string | null, name: string): string | undefined {
  if (!header) return undefined;
  for (const part of header.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) return rest.join("=") || undefined;
  }
  return undefined;
}

export function signedCookieOptions(secure: boolean, maxAge: number) {
  return {
    httpOnly: true,
    secure,
    sameSite: "lax" as const,
    path: "/",
    maxAge,
  };
}

export const sessionTtlSeconds = SESSION_TTL_SECONDS;
export const flowTtlSeconds = FLOW_TTL_SECONDS;

async function oidcMetadata(settings: OidcSettings): Promise<JsonObject> {
  const discoveryUrl = new URL(`${settings.issuer.replace(/\/$/, "")}/.well-known/openid-configuration`);
  const metadata = await fetchJson(discoveryUrl, 64_000);
  if (
    metadata.issuer !== settings.issuer ||
    typeof metadata.authorization_endpoint !== "string" ||
    typeof metadata.token_endpoint !== "string" ||
    typeof metadata.jwks_uri !== "string"
  ) throw new Error("OIDC discovery document is invalid");
  for (const endpoint of [metadata.authorization_endpoint, metadata.token_endpoint, metadata.jwks_uri]) {
    trustedUrl(endpoint, isProduction());
  }
  return metadata;
}

async function verifyIdentityToken(
  token: string,
  settings: OidcSettings,
  nonce: string,
  jwksUri: string,
): Promise<JsonObject> {
  const parts = token.split(".");
  if (parts.length !== 3 || token.length > 32_000) throw new Error("OIDC identity token is invalid");
  const header = decodeJwtPart(parts[0]);
  const claims = decodeJwtPart(parts[1]);
  if (header.alg !== "RS256" && header.alg !== "ES256") throw new Error("OIDC identity token algorithm is unsupported");
  if (header.typ !== undefined && header.typ !== "JWT") throw new Error("OIDC identity token type is invalid");
  if (Object.keys(header).some((name) => !["alg", "typ", "kid"].includes(name))) {
    throw new Error("OIDC identity token header is invalid");
  }
  if (typeof header.kid !== "string" || header.kid.length > 256) throw new Error("OIDC signing key is invalid");
  const jwks = await fetchJson(new URL(jwksUri), 64_000);
  if (!Array.isArray(jwks.keys) || jwks.keys.length > 100) throw new Error("OIDC signing keys are invalid");
  const matchingKeys = jwks.keys.filter((key): key is JsonObject =>
    isJsonObject(key) && key.kid === header.kid &&
    (key.use === undefined || key.use === "sig") &&
    (key.alg === undefined || key.alg === header.alg) &&
    (key.key_ops === undefined || (Array.isArray(key.key_ops) && key.key_ops.includes("verify"))),
  );
  if (matchingKeys.length !== 1) throw new Error("OIDC signing key is unavailable or ambiguous");
  const jwk = matchingKeys[0];
  const keyTypeOkay = header.alg === "RS256" ? jwk.kty === "RSA" : jwk.kty === "EC" && jwk.crv === "P-256";
  if (!keyTypeOkay) throw new Error("OIDC signing key type is invalid");
  const key = createPublicKey({ key: jwk, format: "jwk" });
  const signature = decodeBase64Url(parts[2]);
  const signingInput = Buffer.from(`${parts[0]}.${parts[1]}`, "ascii");
  const verified = header.alg === "RS256"
    ? verifySignature("RSA-SHA256", signingInput, key, signature)
    : verifySignature("sha256", signingInput, { key, dsaEncoding: "ieee-p1363" }, signature);
  if (!verified) throw new Error("OIDC identity token signature is invalid");
  if (
    (header.alg === "RS256" && (key.asymmetricKeyType !== "rsa" || (key.asymmetricKeyDetails?.modulusLength ?? 0) < 2048)) ||
    (header.alg === "ES256" && (key.asymmetricKeyType !== "ec" || key.asymmetricKeyDetails?.namedCurve !== "prime256v1"))
  ) throw new Error("OIDC signing key strength is insufficient");

  const now = nowSeconds();
  const audiences = typeof claims.aud === "string" ? [claims.aud] : Array.isArray(claims.aud) ? claims.aud : [];
  if (
    claims.iss !== settings.issuer ||
    audiences.some((audience) => typeof audience !== "string") ||
    !audiences.includes(settings.clientId) ||
    (claims.azp !== undefined && claims.azp !== settings.clientId) ||
    (audiences.length > 1 && claims.azp !== settings.clientId) ||
    typeof claims.exp !== "number" || claims.exp <= now ||
    typeof claims.iat !== "number" || claims.iat > now + 60 || claims.exp <= claims.iat || claims.exp - claims.iat > 3600 ||
    (typeof claims.nbf === "number" && claims.nbf > now + 60) ||
    claims.nonce !== nonce ||
    typeof claims.sub !== "string" || !claims.sub || claims.sub.length > 255 ||
    claims.email_verified !== true ||
    typeof claims.email !== "string" || !validEmail(claims.email)
  ) throw new Error("OIDC identity claims are invalid");
  return claims;
}

async function fetchJson(url: URL, maxBytes: number): Promise<JsonObject> {
  const response = await fetch(url, {
    headers: { accept: "application/json" },
    cache: "no-store",
    signal: AbortSignal.timeout(8000),
  });
  return responseJson(response, maxBytes);
}

async function responseJson(response: Response, maxBytes: number): Promise<JsonObject> {
  const declaredLength = Number(response.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > maxBytes) throw new Error("OIDC response is too large");
  if (!response.body) throw new Error("OIDC response is empty");
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let byteLength = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    byteLength += value.byteLength;
    if (byteLength > maxBytes) {
      await reader.cancel();
      throw new Error("OIDC response is too large");
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(byteLength);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  const body = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  let parsed: unknown;
  try { parsed = JSON.parse(body); } catch { throw new Error("OIDC response is invalid"); }
  if (!isJsonObject(parsed)) throw new Error("OIDC response is invalid");
  return parsed;
}

function signJwt(payload: JsonObject, key: Buffer): string {
  const header = b64url(Buffer.from(JSON.stringify({ alg: "HS256", typ: "JWT" })));
  const body = b64url(Buffer.from(JSON.stringify(payload)));
  const input = `${header}.${body}`;
  const signature = createHmac("sha256", key).update(input).digest();
  return `${input}.${b64url(signature)}`;
}

function verifyLocalJwt(token: string, key: Buffer, issuer: string, audience: string): JsonObject {
  if (token.length > 16_384) throw new Error("signed token is too large");
  const parts = token.split(".");
  if (parts.length !== 3) throw new Error("signed token is invalid");
  const header = decodeJwtPart(parts[0]);
  const claims = decodeJwtPart(parts[1]);
  if (header.alg !== "HS256" || header.typ !== "JWT") throw new Error("signed token algorithm is invalid");
  const signature = decodeBase64Url(parts[2]);
  const expected = createHmac("sha256", key).update(`${parts[0]}.${parts[1]}`).digest();
  if (signature.byteLength !== expected.byteLength || !timingSafeEqual(signature, expected)) {
    throw new Error("signed token signature is invalid");
  }
  const now = nowSeconds();
  if (
    claims.iss !== issuer || claims.aud !== audience ||
    typeof claims.iat !== "number" || claims.iat > now + 30 ||
    typeof claims.exp !== "number" || claims.exp <= now || claims.exp - claims.iat > SESSION_TTL_SECONDS
  ) throw new Error("signed token claims are invalid");
  return claims;
}

function decodeJwtPart(value: string): JsonObject {
  const decoded = decodeBase64Url(value).toString("utf8");
  const parsed: unknown = JSON.parse(decoded);
  if (!isJsonObject(parsed)) throw new Error("JWT object is invalid");
  return parsed;
}

function decodeBase64Url(value: string): Buffer {
  if (!/^[A-Za-z0-9_-]+$/.test(value)) throw new Error("JWT encoding is invalid");
  const decoded = Buffer.from(value, "base64url");
  if (decoded.toString("base64url") !== value) throw new Error("JWT encoding is invalid");
  return decoded;
}

function b64url(value: Buffer): string {
  return value.toString("base64url");
}

function randomToken(bytes = 32): string {
  return randomBytes(bytes).toString("base64url");
}

function nowSeconds(): number {
  return Math.floor(Date.now() / 1000);
}

function formEncode(value: string): string {
  return new URLSearchParams({ value }).toString().slice("value=".length);
}

function validEmail(value: string): boolean {
  return value.length <= 320 && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
}

function isJsonObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys(value: JsonObject, keys: readonly string[]): boolean {
  const actual = Object.keys(value);
  return actual.length === keys.length && keys.every((key) => Object.hasOwn(value, key));
}

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}

function trustedUrl(value: string, production: boolean): URL {
  const url = new URL(value);
  const localHttp = url.protocol === "http:" && ["127.0.0.1", "localhost", "[::1]"].includes(url.hostname);
  if (
    (!production && !localHttp && url.protocol !== "https:") ||
    (production && url.protocol !== "https:") ||
    url.username || url.password || url.hash || url.search
  ) throw new Error("OIDC URL configuration is invalid");
  return url;
}

function isProduction(): boolean {
  return process.env.PCB_ENVIRONMENT === "staging" ||
    process.env.PCB_ENVIRONMENT === "production" ||
    process.env.NODE_ENV === "production";
}
