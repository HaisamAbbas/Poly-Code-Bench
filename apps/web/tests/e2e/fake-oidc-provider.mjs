import { createServer } from "node:http";
import { createHash, generateKeyPairSync, randomBytes, sign } from "node:crypto";

const host = "127.0.0.1";
const port = Number(process.env.PCB_FAKE_OIDC_PORT ?? "8140");
const issuer = `http://${host}:${port}`;
const clientId = "prompt32-e2e-client";
const { privateKey, publicKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
const jwk = { ...publicKey.export({ format: "jwk" }), kid: "prompt32-test-key", use: "sig", alg: "RS256" };
const codes = new Map();

function sendJson(response, status, value) {
  const body = JSON.stringify(value);
  response.writeHead(status, {
    "cache-control": "no-store",
    "content-type": "application/json; charset=utf-8",
    "content-length": Buffer.byteLength(body),
  });
  response.end(body);
}

function makeIdToken({ email, nonce }) {
  const now = Math.floor(Date.now() / 1000);
  const header = Buffer.from(JSON.stringify({ alg: "RS256", typ: "JWT", kid: jwk.kid })).toString("base64url");
  const claims = Buffer.from(JSON.stringify({
    iss: issuer,
    aud: clientId,
    sub: `subject:${email}`,
    email,
    email_verified: email !== "unverified@example.org",
    nonce,
    iat: now,
    exp: now + 300,
  })).toString("base64url");
  const signingInput = `${header}.${claims}`;
  const signature = sign("RSA-SHA256", Buffer.from(signingInput), privateKey).toString("base64url");
  return `${signingInput}.${signature}`;
}

const server = createServer(async (request, response) => {
  const url = new URL(request.url ?? "/", issuer);
  if (request.method === "GET" && url.pathname === "/.well-known/openid-configuration") {
    return sendJson(response, 200, {
      issuer,
      authorization_endpoint: `${issuer}/authorize`,
      token_endpoint: `${issuer}/token`,
      jwks_uri: `${issuer}/jwks`,
      response_types_supported: ["code"],
      subject_types_supported: ["public"],
      id_token_signing_alg_values_supported: ["RS256"],
      token_endpoint_auth_methods_supported: ["none"],
    });
  }
  if (request.method === "GET" && url.pathname === "/jwks") return sendJson(response, 200, { keys: [jwk] });
  if (request.method === "GET" && url.pathname === "/authorize") {
    const state = url.searchParams.get("state");
    const nonce = url.searchParams.get("nonce");
    const callback = url.searchParams.get("redirect_uri");
    const challenge = url.searchParams.get("code_challenge");
    const email = url.searchParams.get("login_hint") ?? "browser@example.org";
    if (
      url.searchParams.get("client_id") !== clientId ||
      url.searchParams.get("response_type") !== "code" ||
      !state || !nonce || !callback || !challenge ||
      !callback.startsWith("http://127.0.0.1:3123/auth/callback")
    ) return sendJson(response, 400, { error: "invalid_request" });
    const code = randomBytes(24).toString("base64url");
    codes.set(code, { state, nonce, callback, challenge, email });
    response.writeHead(302, { location: `${callback}?code=${encodeURIComponent(code)}&state=${encodeURIComponent(state)}`, "cache-control": "no-store" });
    return response.end();
  }
  if (request.method === "POST" && url.pathname === "/token") {
    let body = "";
    for await (const chunk of request) body += chunk;
    const params = new URLSearchParams(body);
    const code = params.get("code") ?? "";
    const record = codes.get(code);
    codes.delete(code);
    const verifier = params.get("code_verifier") ?? "";
    const challenge = createHash("sha256").update(verifier).digest("base64url");
    if (
      params.get("grant_type") !== "authorization_code" ||
      params.get("client_id") !== clientId ||
      !record ||
      params.get("redirect_uri") !== record.callback ||
      challenge !== record.challenge
    ) return sendJson(response, 400, { error: "invalid_grant" });
    return sendJson(response, 200, {
      access_token: randomBytes(24).toString("base64url"),
      token_type: "Bearer",
      expires_in: 300,
      id_token: makeIdToken(record),
    });
  }
  return sendJson(response, 404, { error: "not_found" });
});

server.listen(port, host, () => process.stdout.write(`Synthetic OIDC provider ready at ${issuer}\n`));
