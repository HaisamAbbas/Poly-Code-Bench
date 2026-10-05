import { expect, test, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

const artifactDirectory = resolve(__dirname, "../../../../docs/implementation/evidence/prompt-32");
const webOrigin = "http://127.0.0.1:3123";

async function openSubmissionPage(page: Page) {
  await page.goto("/leaderboard");
  await expect(page.getByRole("heading", { name: "Leaderboard" })).toBeVisible();
  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "Submit model" }).click();
  await expect(page.getByRole("heading", { name: "Request a model evaluation" })).toBeVisible();
  await expect(page.getByText("Synthetic internal test data", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Sign in with your account" }).click();
  await expect(page).toHaveURL(`${webOrigin}/model-submissions`);
  await expect(page.getByLabel("Verified account email")).toHaveValue("browser@example.org");
  const cookies = await page.context().cookies();
  expect(cookies.find((cookie) => cookie.name === "pcb-session-dev")?.httpOnly).toBe(true);
  expect(cookies.some((cookie) => cookie.name === "pcb-oidc-flow-dev")).toBe(false);
}

async function completeRequestForm(page: Page) {
  await page.getByLabel("Model name").fill("Synthetic Browser Candidate");
  await page.getByLabel("Provider", { exact: true }).fill("Synthetic Test Provider");
  await expect(page.getByLabel("Verified account email")).toHaveAttribute("readonly", "");
  await page.getByLabel("Provider endpoint URL").fill("https://models.example.org/v1");
  await page.getByLabel("Model/source documentation URL").fill("https://models.example.org/model/card");
  await page.getByLabel("Source license or permission basis").fill("Synthetic test-only permission");
  await page.getByLabel(/I am authorized to request evaluation/).check();
}

test("E2E-41 and E2E-39: OIDC request is pending and only the owner can track it", async ({ page, browser }) => {
  await page.setViewportSize({ width: 1440, height: 1050 });
  const providerRequests: string[] = [];
  const browserSubmissionRequests: import("@playwright/test").Request[] = [];
  page.on("request", (request) => {
    if (request.url().startsWith("https://models.example.org")) providerRequests.push(request.url());
    if (request.url().includes("/api/model-submissions")) browserSubmissionRequests.push(request);
  });
  await openSubmissionPage(page);
  const crossOriginSubmission = await page.request.post("/api/model-submissions", {
    headers: {
      origin: "https://attacker.example.org",
      "content-type": "application/json",
      "idempotency-key": "cross-origin-attempt",
    },
    data: "{}",
  });
  expect(crossOriginSubmission.status()).toBe(403);
  await completeRequestForm(page);
  await page.getByRole("button", { name: "Send for review" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("status")).toContainText("pending");
  await expect(page.getByRole("status")).toContainText("No endpoint has been contacted");
  const requestId = await page.locator(".submission-status dd code").first().textContent();
  expect(requestId).toMatch(/^[0-9a-f-]{36}$/i);
  expect(providerRequests).toEqual([]);
  expect(page.url()).not.toContain("code=");
  expect(page.url()).not.toContain("state=");
  expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain("access_token");
  for (const browserRequest of browserSubmissionRequests) {
    expect((await browserRequest.allHeaders()).authorization).toBeUndefined();
  }
  await page.screenshot({ path: resolve(artifactDirectory, "submission-pending-desktop.png"), fullPage: true });

  await page.getByLabel("Request ID").fill(requestId!);
  await page.getByRole("button", { name: "Check status" }).click();
  await expect(page.locator(".submission-status.status-pending").last()).toBeVisible();
  await expect(page.getByText("Pending review. No endpoint has been contacted and no run or charge exists.")).toBeVisible();
  const crossOriginSignOut = await page.request.post("/auth/sign-out", {
    headers: { origin: "https://attacker.example.org" },
  });
  expect(crossOriginSignOut.status()).toBe(403);
  await expect(page.getByLabel("Verified account email")).toHaveValue("browser@example.org");
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("link", { name: "Sign in with your account" })).toBeVisible();

  const anonymousContext = await browser.newContext({ baseURL: webOrigin });
  const unauthenticated = await anonymousContext.request.get(`/api/model-submissions/${requestId}`);
  expect(unauthenticated.status()).toBe(401);
  expect(unauthenticated.headers()["cache-control"]).toContain("no-store");
  await anonymousContext.close();

  const otherContext = await browser.newContext({ baseURL: webOrigin });
  const otherPage = await otherContext.newPage();
  await otherPage.goto(`/auth/sign-in?return_to=%2Fmodel-submissions&login_hint=other%40example.org`);
  await expect(otherPage.getByLabel("Verified account email")).toHaveValue("other@example.org");
  const foreign = await otherContext.request.get(`/api/model-submissions/${requestId}`);
  expect(foreign.status()).toBe(404);
  expect(await foreign.text()).not.toContain(requestId!);
  await otherContext.close();
  await expect(page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "Leaderboard" })).toBeVisible();
});

test("E2E-25/26/40: unverified OIDC email is rejected and the sign-in flow works by keyboard on mobile", async ({ page }) => {
  const openRedirect = await page.request.get("/auth/sign-in?return_to=%2F%2Fattacker.example.org");
  expect(openRedirect.status()).toBe(400);
  await page.goto("/auth/sign-in?return_to=%2Fmodel-submissions&login_hint=unverified%40example.org");
  await expect(page.locator(".submission-error")).toContainText("Sign-in was not completed");
  await expect(page.getByRole("link", { name: "Sign in with your account" })).toBeVisible();

  await page.setViewportSize({ width: 375, height: 812 });
  await openSubmissionPage(page);
  await completeRequestForm(page);
  await page.getByRole("button", { name: "Send for review" }).focus();
  await expect(page.getByRole("button", { name: "Send for review" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("status")).toContainText("pending");
  const narrow = await page.evaluate(() => ({ viewport: window.innerWidth, document: document.documentElement.scrollWidth }));
  expect(narrow.document).toBeLessThanOrEqual(narrow.viewport + 1);
  await page.screenshot({ path: resolve(artifactDirectory, "submission-pending-mobile.png"), fullPage: true });
});

test.beforeAll(() => mkdirSync(artifactDirectory, { recursive: true }));
