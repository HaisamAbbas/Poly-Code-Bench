import assert from "node:assert/strict";
import { createHmac, randomUUID } from "node:crypto";
import { readFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../../../..");
const env = readEnvironment(resolve(repoRoot, ".env"));
const webOrigin = env.PCB_WEB_ORIGIN;
const apiOrigin = env.PCB_PUBLIC_API_URL.replace(/\/v1\/?$/, "");
const artifactDirectory = resolve(repoRoot, ".cache/local-stack-browser");
mkdirSync(artifactDirectory, { recursive: true });

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const unexpectedEndpointRequests = [];
  await page.route("https://api.example.com/**", async (route) => {
    unexpectedEndpointRequests.push(route.request().url());
    await route.abort("blockedbyclient");
  });
  page.on("request", (request) => {
    if (request.url().startsWith("https://api.example.com/")) {
      unexpectedEndpointRequests.push(request.url());
    }
  });

  await page.goto(`${webOrigin}/leaderboard`);
  await page.getByText("Synthetic internal test data", { exact: true }).waitFor();
  await page.getByText("These are not live benchmark results.", { exact: false }).waitFor();
  await page.screenshot({ path: resolve(artifactDirectory, "leaderboard-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: resolve(artifactDirectory, "leaderboard-mobile.png"), fullPage: true });
  const metricsRegion = page.getByRole("region", { name: "Scrollable published configuration metrics" });
  assert.match(await metricsRegion.innerText(), /scroll this region horizontally/i);
  const tableHasHorizontalOverflow = await metricsRegion.evaluate((node) => node.scrollWidth > node.clientWidth);
  assert.ok(tableHasHorizontalOverflow, "the mobile metrics region should expose its overflowing columns");
  await metricsRegion.focus();
  await page.keyboard.press("ArrowRight");
  const keyboardScrolled = await metricsRegion.evaluate((node) => node.scrollLeft > 0);
  assert.ok(keyboardScrolled, "the mobile metrics region should scroll with the keyboard");
  await metricsRegion.evaluate((node) => { node.scrollLeft = 0; });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`${webOrigin}/model-submissions`);
  await page.getByRole("link", { name: "Sign in with your account" }).click();
  await page.waitForURL("http://127.0.0.1:8080/**");
  await page.locator("#username").fill(env.LOCAL_SUBMITTER_USERNAME);
  await page.locator("#password").fill(env.LOCAL_SUBMITTER_PASSWORD);
  await page.locator("#kc-login").press("Enter");
  await page.waitForURL(
    (url) => url.origin === webOrigin || url.pathname.includes("/login-actions/required-action"),
    { timeout: 30_000 },
  );
  if (new URL(page.url()).pathname.includes("/login-actions/required-action")) {
    await page.locator("#firstName").fill("Local");
    await page.locator("#lastName").fill("Submitter");
    await page.getByRole("button", { name: "Submit" }).click();
  }
  try {
    await page.waitForURL(`${webOrigin}/model-submissions`, { timeout: 30_000 });
  } catch (error) {
    await page.screenshot({ path: resolve(artifactDirectory, "oidc-sign-in-failure.png"), fullPage: true });
    const errorText = await page.locator("#input-error").textContent().catch(() => null);
    console.error(`Local identity sign-in stopped at ${new URL(page.url()).origin}${new URL(page.url()).pathname}; provider message: ${errorText ?? "none"}`);
    throw error;
  }
  await page.getByText("Signed in with verified account", { exact: false }).waitFor();
  await page.screenshot({ path: resolve(artifactDirectory, "submission-form-desktop.png"), fullPage: true });

  const modelName = `Local OIDC smoke ${new Date().toISOString()}`;
  await page.getByLabel("Model name").fill(modelName);
  await page.getByLabel("Provider", { exact: true }).fill("Local Smoke Provider");
  await page.getByLabel("Provider endpoint URL").fill("https://api.example.com/v1");
  await page.getByLabel("Model/source documentation URL").fill("https://models.example.com/local-smoke");
  await page.getByLabel("Source license or permission basis").fill("Local test permission");
  await page.getByRole("checkbox").check();

  await page.getByLabel("Model name").focus();
  await page.keyboard.press("Tab");
  assert.equal(await page.getByLabel("Provider", { exact: true }).evaluate((input) => input === document.activeElement), true);

  await page.setViewportSize({ width: 390, height: 844 });
  const hasHorizontalOverflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  assert.equal(hasHorizontalOverflow, false, "the submission page should fit a small viewport");
  await page.screenshot({ path: resolve(artifactDirectory, "submission-form-mobile.png"), fullPage: true });

  await page.getByRole("button", { name: "Send for review" }).click();
  const submissionFeedback = page.locator(".submission-status, .submission-error").first();
  await submissionFeedback.waitFor();
  const submissionError = page.locator(".submission-error");
  assert.equal(unexpectedEndpointRequests.length, 0, "submitting metadata must not contact the provider endpoint");

  let requestId = await page.locator("dd code").first().textContent();
  let expectedModelName = modelName;
  if (await submissionError.count()) {
    const errorText = await submissionError.innerText();
    assert.match(errorText, /Too many new requests/, `unexpected submission error: ${errorText}`);
    assert.equal(await submissionError.getAttribute("role"), "alert", "the rate limit error should be announced accessibly");

    // The local API deliberately limits each owner to five new requests per hour. Reuse one of
    // this user's earlier synthetic smoke submissions when that window is full, without deleting
    // local data or relaxing the application rate limit.
    const reviewerResponse = await fetch(`${apiOrigin}/v1/admin/model-submissions?status=pending&limit=200`, {
      headers: { authorization: `Bearer ${env.LOCAL_REVIEWER_TOKEN}` },
    });
    assert.equal(reviewerResponse.status, 200, "the local reviewer identity should access the review queue");
    const reviewerEnvelope = await reviewerResponse.json();
    const smokeRows = reviewerEnvelope.data.filter((row) => row.model_name.startsWith("Local OIDC smoke "));
    for (const row of smokeRows) {
      const ownerStatus = await page.evaluate(async (submissionId) => {
        const response = await fetch(`/api/model-submissions/${encodeURIComponent(submissionId)}`, {
          cache: "no-store",
        });
        return response.ok ? response.json() : null;
      }, row.submission_id);
      if (ownerStatus?.data?.submission_id === row.submission_id) {
        requestId = row.submission_id;
        expectedModelName = row.model_name;
        break;
      }
    }
    assert.ok(requestId, "a prior owner-visible synthetic smoke request is required after rate limiting");
    await page.getByLabel("Request ID").fill(requestId);
  } else {
    assert.match(
      await page.locator(".submission-status").innerText(),
      /Pending review\. No endpoint has been contacted and no run or charge exists\./,
    );
  }

  await page.getByRole("button", { name: "Check status" }).click();
  await page.waitForFunction(() => Array.from(document.querySelectorAll("button")).some(
    (button) => button.textContent?.trim() === "Check status" && !button.disabled,
  ));
  assert.equal(await page.locator(".submission-status").count(), 1, "the owner status lookup should load from PostgreSQL");
  assert.match(await page.locator(".submission-status").innerText(), /pending review/i);
  await page.screenshot({ path: resolve(artifactDirectory, "submission-pending-mobile.png"), fullPage: true });

  assert.ok(requestId, "the accepted request should have a visible request ID");

  const reviewerResponse = await fetch(`${apiOrigin}/v1/admin/model-submissions?status=pending&limit=200`, {
    headers: { authorization: `Bearer ${env.LOCAL_REVIEWER_TOKEN}` },
  });
  assert.equal(reviewerResponse.status, 200, "the local reviewer identity should access the review queue");
  const reviewerEnvelope = await reviewerResponse.json();
  const reviewerRows = reviewerEnvelope.data;
  assert.ok(Array.isArray(reviewerRows), "the reviewer queue should return rows");
  assert.ok(reviewerRows.some((row) => row.submission_id === requestId && row.model_name === expectedModelName));

  const otherOwnerToken = ownerToken(env.PCB_WEB_AUTH_SIGNING_KEY, "different-local-submitter");
  const otherOwnerResponse = await fetch(`${apiOrigin}/v1/model-submissions/${requestId}`, {
    headers: { authorization: `Bearer ${otherOwnerToken}` },
  });
  assert.equal(otherOwnerResponse.status, 404, "another submitter must not read the request");

  const anonymousReviewerResponse = await fetch(`${apiOrigin}/v1/admin/model-submissions?status=pending`);
  assert.equal(anonymousReviewerResponse.status, 401, "the review queue must reject anonymous requests");

  console.log("Local Keycloak login, PostgreSQL submission, review authorization, owner isolation, keyboard access, and mobile layout passed.");
  console.log(`Screenshots: ${artifactDirectory}`);
} finally {
  await browser.close();
}

function readEnvironment(path) {
  const values = {};
  for (const line of readFileSync(path, "utf8").split(/\r?\n/)) {
    const entry = line.trim();
    if (!entry || entry.startsWith("#")) continue;
    const separator = entry.indexOf("=");
    if (separator < 1) throw new Error("invalid local environment file");
    values[entry.slice(0, separator)] = entry.slice(separator + 1);
  }
  return values;
}

function ownerToken(signingKey, subject) {
  const encode = (value) => Buffer.from(JSON.stringify(value)).toString("base64url");
  const header = encode({ alg: "HS256", typ: "JWT" });
  const now = Math.floor(Date.now() / 1000);
  const claims = encode({
    iss: "polycodebench-web",
    aud: "polycodebench-api",
    sub: subject,
    roles: ["submitter"],
    email: "other@example.test",
    email_verified: true,
    iat: now,
    exp: now + 180,
    jti: randomUUID(),
  });
  const payload = `${header}.${claims}`;
  const signature = createHmac("sha256", signingKey).update(payload).digest("base64url");
  return `${payload}.${signature}`;
}
