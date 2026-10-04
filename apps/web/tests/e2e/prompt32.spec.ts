import { expect, test, type Page } from "@playwright/test";
import { mkdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

const artifactDirectory = resolve(__dirname, "../../../../docs/implementation/evidence/prompt-32");
const apiOrigin = "http://127.0.0.1:8132";
const testTokens = JSON.parse(
  readFileSync(resolve(__dirname, "../../../../.cache/prompt32-e2e-client.json"), "utf-8"),
) as { accountToken: string; secondAccountToken: string };

async function openSubmissionPage(page: Page) {
  await page.goto("/leaderboard");
  await expect(page.getByRole("heading", { name: "Leaderboard" })).toBeVisible();
  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "Submit model" }).click();
  await expect(page.getByRole("heading", { name: "Request a model evaluation" })).toBeVisible();
  await expect(page.getByText("Synthetic internal test data", { exact: true })).toBeVisible();
}

async function completeRequestForm(page: Page, email = "browser@example.org") {
  await page.getByLabel("Verified account access token").first().fill(testTokens.accountToken);
  await page.getByLabel("Model name").fill("Synthetic Browser Candidate");
  await page.getByLabel("Provider", { exact: true }).fill("Synthetic Test Provider");
  await page.getByLabel("Verified account email").fill(email);
  await page.getByLabel("Provider endpoint URL").fill("https://models.example.org/v1");
  await page.getByLabel("Model/source documentation URL").fill("https://models.example.org/model/card");
  await page.getByLabel("Source license or permission basis").fill("Synthetic test-only permission");
  await page.getByLabel(/I am authorized to request evaluation/).check();
}

test("E2E-41 and E2E-39: public request is pending and owner can track its status", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1050 });
  const providerRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().startsWith("https://models.example.org")) providerRequests.push(request.url());
  });
  await openSubmissionPage(page);
  const identityProbe = await page.request.get(
    `${apiOrigin}/v1/model-submissions/00000000-0000-4000-8000-000000000001`,
    { headers: { authorization: `Bearer ${testTokens.accountToken}` } },
  );
  expect(identityProbe.status()).toBe(404);
  await completeRequestForm(page);
  const tokenInput = page.getByLabel("Verified account access token").first();
  await expect(tokenInput).toHaveAttribute("type", "password");
  await page.getByRole("button", { name: "Send for review" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("status")).toContainText("pending");
  await expect(page.getByRole("status")).toContainText("No endpoint has been contacted");
  const requestId = await page.locator(".submission-status dd code").first().textContent();
  expect(requestId).toMatch(/^[0-9a-f-]{36}$/i);
  expect(providerRequests).toEqual([]);
  expect(page.url()).not.toContain(testTokens.accountToken);
  expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain(
    testTokens.accountToken,
  );
  await page.screenshot({ path: resolve(artifactDirectory, "submission-pending-desktop.png"), fullPage: true });

  await page.getByLabel("Request ID").fill(requestId!);
  await page.getByRole("button", { name: "Check status" }).click();
  await expect(page.locator(".submission-status.status-pending").last()).toBeVisible();
  await expect(page.getByText("Pending review. No endpoint has been contacted and no run or charge exists.")).toBeVisible();

  const foreign = await page.request.get(
    `${apiOrigin}/v1/model-submissions/${requestId}`,
    { headers: { authorization: `Bearer ${testTokens.secondAccountToken}` } },
  );
  expect(foreign.status()).toBe(404);
  expect(await foreign.text()).not.toContain(requestId!);
  const unauthenticated = await page.request.get(`/api/model-submissions/${requestId}`);
  expect(unauthenticated.status()).toBe(401);
  expect(unauthenticated.headers()["cache-control"]).toContain("no-store");
  await expect(page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "Leaderboard" })).toBeVisible();
});

test("E2E-25/26/40: identity mismatch errors, keyboard flow and narrow viewport", async ({ page }) => {
  await openSubmissionPage(page);
  await completeRequestForm(page, "not-the-verified-email@example.org");
  await page.getByRole("button", { name: "Send for review" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.locator(".submission-error")).toContainText("Verified account could not be confirmed");

  await page.reload();
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
