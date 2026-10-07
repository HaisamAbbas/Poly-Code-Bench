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

async function showApprovedProgressFixture(page: Page, submissionId: string) {
  const progress = [
    { run_status: "queued", attempt_states: { queued: 2 }, solve_job_states: { queued: 2 } },
    { run_status: "running", attempt_states: { queued: 1, running: 1 }, solve_job_states: { leased: 1, queued: 1 } },
    { run_status: "completed", attempt_states: { completed: 2 }, solve_job_states: { succeeded: 2 } },
  ] as const;
  let requestIndex = 0;
  await page.route(`**/api/model-submissions/${submissionId}`, (route) => {
    const current = progress[Math.min(requestIndex++, progress.length - 1)];
    return route.fulfill({
      json: {
        data: {
          kind: "model_submission",
          schema_version: 1,
          submission_id: submissionId,
          status: "approved",
          model_name: "Synthetic Browser Candidate",
          provider: "Synthetic Test Provider",
          organization: null,
          contact_email: "browser@example.org",
          endpoint_url: "https://models.example.org/v1",
          source_url: "https://models.example.org/model/card",
          source_license: "Synthetic test-only permission",
          permission_attested: true,
          submitted_at: "2026-10-06T08:00:00Z",
          row_version: 1,
          rejection_reason: null,
          resulting_run_id: "b8e3eacb-47e5-4623-ae6b-8a58fb58f583",
          run_status: current.run_status,
          run_progress: {
            schema_version: 1,
            attempt_states: current.attempt_states,
            solve_job_states: current.solve_job_states,
          },
        },
        meta: { release_digest: `sha256:${"a".repeat(64)}` },
      },
    });
  });
  await page.getByLabel("Request ID").fill(submissionId);
  await page.getByRole("button", { name: "Check status" }).click();
  const approvedStatus = page.locator(".submission-status.status-approved").last();
  await expect(approvedStatus).toContainText("Solve work is queued");
  await expect(approvedStatus).toContainText("No evaluation result is available yet");
  await expect(approvedStatus).toContainText("Solve job states");
  await expect(approvedStatus.getByText("queued: 2", { exact: true }).last()).toBeVisible();

  await page.getByRole("button", { name: "Check status" }).click();
  await expect(approvedStatus).toContainText("Solve work is running");
  await expect(approvedStatus).toContainText("No evaluation result is available yet");
  await expect(approvedStatus.getByText("queued: 1, running: 1", { exact: true }).last()).toBeVisible();
  await expect(approvedStatus.getByText("leased: 1, queued: 1", { exact: true }).last()).toBeVisible();

  await page.getByRole("button", { name: "Check status" }).click();
  await expect(approvedStatus).toContainText("Solve work completed");
  await expect(approvedStatus).toContainText("not been evaluated, scored, or published");
  await expect(approvedStatus.getByText("completed: 2", { exact: true }).last()).toBeVisible();
  await expect(approvedStatus.getByText("succeeded: 2", { exact: true }).last()).toBeVisible();
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
  await showApprovedProgressFixture(page, requestId!);
  await page.screenshot({ path: resolve(artifactDirectory, "submission-approved-progress-desktop.png"), fullPage: true });
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
  const requestId = await page.locator(".submission-status.status-pending dd code").first().textContent();
  expect(requestId).toMatch(/^[0-9a-f-]{36}$/i);
  const narrow = await page.evaluate(() => ({ viewport: window.innerWidth, document: document.documentElement.scrollWidth }));
  expect(narrow.document).toBeLessThanOrEqual(narrow.viewport + 1);
  await page.screenshot({ path: resolve(artifactDirectory, "submission-pending-mobile.png"), fullPage: true });
  await showApprovedProgressFixture(page, requestId!);
  const progressNarrow = await page.evaluate(() => ({ viewport: window.innerWidth, document: document.documentElement.scrollWidth }));
  expect(progressNarrow.document).toBeLessThanOrEqual(progressNarrow.viewport + 1);
  await page.screenshot({ path: resolve(artifactDirectory, "submission-approved-progress-mobile.png"), fullPage: true });
});

test.beforeAll(() => mkdirSync(artifactDirectory, { recursive: true }));
