import { expect, test, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

const artifactDirectory = resolve(__dirname, "../../../../docs/implementation/evidence/prompt-31");
const apiOrigin = "http://127.0.0.1:8130";

async function openBoard(page: Page): Promise<string> {
  await page.goto("/leaderboard");
  await expect(page.getByRole("heading", { name: "Leaderboard" })).toBeVisible();
  return page.locator("#release-select").inputValue();
}

async function releaseRows(page: Page) {
  const response = await page.request.get(`${apiOrigin}/v1/releases?limit=200`);
  expect(response.ok()).toBeTruthy();
  return (await response.json()).data as { release_id: string; state: string; replacement_release_id: string | null }[];
}

test("E2E-39 navigation: leaderboard → compatible comparison → disclosed task → score evidence", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1050 });
  const releaseId = await openBoard(page);
  await expect(page.getByText("Synthetic internal test data", { exact: true })).toBeVisible();

  await page.getByRole("row", { name: /Fixture Code System A/ }).getByRole("link", { name: "Fixture Code System A" }).click();
  await expect(page.getByRole("heading", { name: "Fixture Code System A" })).toBeVisible();
  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "Leaderboard" }).click();
  await expect(page.locator("#release-select")).toHaveValue(releaseId);

  await page.locator("#compare-models-launcher").selectOption(["synthetic-code-a", "synthetic-code-c"]);
  await page.getByRole("button", { name: "Open comparison" }).click();
  await expect(page).toHaveURL(/models=synthetic-code-a/);
  await expect(page).toHaveURL(/models=synthetic-code-c/);
  await expect(page.getByRole("heading", { name: "Configuration identities" })).toBeVisible();
  await expect(page.getByText("3 common disclosed tasks", { exact: false })).toBeVisible();

  const apiUrl = new URL(`${apiOrigin}/v1/compare`);
  apiUrl.searchParams.set("release", releaseId);
  apiUrl.searchParams.append("models", "synthetic-code-a");
  apiUrl.searchParams.append("models", "synthetic-code-c");
  const apiComparisonResponse = await page.request.get(apiUrl.toString());
  expect(apiComparisonResponse.ok()).toBeTruthy();
  const apiComparison = (await apiComparisonResponse.json()).data;
  const apiPythonDelta = apiComparison.paired_task_deltas.find(
    (item: { task_id: string; metric_id: string }) => item.task_id === "synthetic-task-example" && item.metric_id === "code_score",
  );
  expect(apiPythonDelta).toMatchObject({
    baseline_value: "92.000000",
    candidate_value: "80.000000",
    delta_value: "-12.000000",
    baseline_scorecard_id: "synthetic-scorecard-a",
    candidate_scorecard_id: "synthetic-scorecard-c",
  });
  const pythonRow = page.getByRole("row").filter({ hasText: "synthetic-task-example" }).filter({ hasText: "Code score" });
  await expect(pythonRow).toContainText("92.000000");
  await expect(pythonRow).toContainText("80.000000");
  await expect(pythonRow).toContainText("-12.000000");
  await page.screenshot({ path: resolve(artifactDirectory, "comparison-desktop.png"), fullPage: true });

  await page.getByRole("region", { name: "Exact common task scorecards" }).getByRole("link", { name: "synthetic-task-example · v1" }).click();
  await expect(page.getByRole("heading", { name: "synthetic-task-example · version 1" })).toBeVisible();
  await expect(page.getByText("Public statement, sources, patches and findings")).toBeVisible();
  await page.getByRole("button", { name: "Load public details" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Hide details" })).toBeVisible();
  const releasedDiff = page.locator(".payload-card pre[aria-label='Submitted patch diff synthetic-patch-a-python']");
  await expect(releasedDiff).toContainText("<script>alert('synthetic')</script>");
  await expect(page.locator(".payload-card pre script")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Download this disclosed task record as JSON" })).toBeVisible();
  const exportedTask = await page.request.get(
    new URL(`/api/public/tasks/synthetic-task-example/content?release=${encodeURIComponent(releaseId)}&download=1`, page.url()).toString(),
  );
  expect(exportedTask.ok()).toBeTruthy();
  expect(exportedTask.headers()["content-disposition"]).toContain("attachment; filename=\"polycodebench-task-synthetic-task-example-v1.json\"");
  const exportedBody = await exportedTask.text();
  expect(exportedBody).toContain("synthetic-source-python-v1");
  expect(exportedBody).not.toContain("private/oracle-review");
  expect(exportedBody).not.toContain("private-heldout");
  await page.screenshot({ path: resolve(artifactDirectory, "task-detail-public-evidence.png"), fullPage: true });

  await page.locator("#patch-synthetic-patch-a-python").getByRole("link", { name: "Source scorecard synthetic-scorecard-a", exact: true }).click();
  await expect(page.getByRole("heading", { name: "synthetic-scorecard-a" })).toBeVisible();
  await expect(page.locator("#metric-code_score")).toContainText("92.000000");
  await expect(page.locator("#metric-gated_repair_score")).toContainText("gated zero");
  await expect(page.locator("#metric-not_applicable_example")).toContainText("not applicable");
  await expect(page.locator("#metric-missing_example")).toContainText("missing");
  await expect(page.locator("#metric-pending_review_example")).toContainText("needs review");
  await expect(page.getByText("1 evidence reference(s) were omitted")).toBeVisible();
  await expect(page.locator(".evidence-ref-list")).toContainText("synthetic-patch-a-python");
  await expect(page.locator(".evidence-ref-list")).not.toContainText("private/oracle-review");
  await page.screenshot({ path: resolve(artifactDirectory, "scorecard-contribution-chain.png"), fullPage: true });

  await page.getByRole("link", { name: "synthetic-source-python-v1" }).click();
  await expect(page).toHaveURL(/open=1/);
  await expect(page.locator("#source-synthetic-source-python-v1")).toBeVisible();
  await page.getByRole("link", { name: /Frozen methodology · synthetic-ui-fixture-v1/ }).first().click();
  await expect(page.getByRole("heading", { name: "Methodology and corrections" })).toBeVisible();
  await expect(page.getByText(releaseId, { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Native and adapted benchmark records" })).toBeVisible();
  await expect(page.getByText("Synthetic display label only; this release contains no native result.")).toBeVisible();
  await expect(page.getByText("Synthetic display label only; this release contains no adapted result.")).toBeVisible();
  await page.screenshot({ path: resolve(artifactDirectory, "frozen-methodology.png"), fullPage: true });
});

test("E2E-39 and E2E-26: incompatible cohorts, private probes and safe public export", async ({ page }) => {
  const releaseId = await openBoard(page);
  await page.goto(`/compare?release=${releaseId}&models=synthetic-code-a&models=synthetic-code-b`);
  await expect(page.getByRole("heading", { name: "Configurations are not comparable" })).toBeVisible();
  await expect(page.locator(".incompatibility-list")).toContainText("protocol mismatch");
  await expect(page.locator(".incompatibility-list")).toContainText("budget mismatch");
  await expect(page.getByRole("heading", { name: "Configuration identities" })).toHaveCount(0);
  await expect(page.locator(".paired-task-difference")).toHaveCount(0);

  const tasks = await page.request.get(`${apiOrigin}/v1/tasks?release=${encodeURIComponent(releaseId)}&limit=200`);
  expect(tasks.ok()).toBeTruthy();
  const taskListText = await tasks.text();
  expect(taskListText).not.toContain("private-heldout");
  expect(taskListText).not.toContain("private/oracle-review");

  const hiddenTask = await page.request.get(`${apiOrigin}/v1/tasks/private-heldout-candidate?release=${encodeURIComponent(releaseId)}`);
  const hiddenScorecard = await page.request.get(`${apiOrigin}/v1/scorecards/private-heldout-scorecard?release=${encodeURIComponent(releaseId)}`);
  expect(hiddenTask.status()).toBe(404);
  expect(hiddenScorecard.status()).toBe(404);
  expect(await hiddenTask.text()).not.toContain("private-heldout");
  expect(await hiddenScorecard.text()).not.toContain("private-heldout");

  const exportProbe = await page.request.get(
    new URL(`/api/public/tasks/private-heldout-candidate/content?release=${encodeURIComponent(releaseId)}&download=1`, page.url()).toString(),
  );
  expect(exportProbe.status()).toBe(404);
  expect(await exportProbe.text()).not.toContain("private-heldout");

  const releases = await releaseRows(page);
  const withdrawn = releases.find((row) => row.state === "withdrawn");
  expect(withdrawn).toBeDefined();
  expect(withdrawn!.replacement_release_id).toBe(releaseId);
  await page.goto(`/methodology/synthetic-ui-fixture-v1?release=${encodeURIComponent(withdrawn!.release_id)}`);
  await expect(page.getByRole("heading", { name: "Release withdrawn" })).toBeVisible();
  await expect(page.getByRole("complementary").getByRole("link", { name: releaseId, exact: true })).toBeVisible();
  await expect(page.getByText(withdrawn!.release_id, { exact: true })).toBeVisible();
  await page.screenshot({ path: resolve(artifactDirectory, "withdrawal-and-successor.png"), fullPage: true });
});

test("E2E-40 affected cases: bounded task pagination, lazy payloads, keyboard operation and responsive layouts", async ({ page }) => {
  const releaseId = await openBoard(page);
  let taskContentRequests = 0;
  page.on("request", (request) => {
    if (request.url().includes("/api/public/tasks/") && request.url().includes("/content")) taskContentRequests += 1;
  });
  await page.goto(`/tasks?release=${encodeURIComponent(releaseId)}`);
  await expect(page.locator(".task-row")).toHaveCount(50);
  await expect(page.getByRole("link", { name: "Next page" })).toBeVisible();
  expect(taskContentRequests).toBe(0);
  await page.getByRole("link", { name: "Next page" }).click();
  await expect(page.locator(".task-row")).toHaveCount(14);
  expect(taskContentRequests).toBe(0);

  await page.goto(`/tasks/synthetic-task-example?release=${encodeURIComponent(releaseId)}`);
  const loadDetails = page.getByRole("button", { name: "Load public details" });
  await loadDetails.focus();
  await expect(loadDetails).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator(".payload-card pre").first()).toBeVisible();
  expect(taskContentRequests).toBe(1);

  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto(`/compare?release=${encodeURIComponent(releaseId)}&models=synthetic-code-a&models=synthetic-code-c`);
  await expect(page.getByText("Synthetic internal test data", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Exact common task versions" })).toBeVisible();
  const narrow = await page.evaluate(() => ({ viewport: window.innerWidth, document: document.documentElement.scrollWidth }));
  expect(narrow.document).toBeLessThanOrEqual(narrow.viewport + 1);
  await page.screenshot({ path: resolve(artifactDirectory, "comparison-mobile.png"), fullPage: true });

  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.goto(`/tasks?release=${encodeURIComponent(releaseId)}`);
  const wide = await page.evaluate(() => ({ viewport: window.innerWidth, document: document.documentElement.scrollWidth }));
  expect(wide.document).toBeLessThanOrEqual(wide.viewport + 1);
  const taskRegion = page.getByRole("navigation", { name: "Task list pages" });
  await expect(taskRegion).toBeVisible();
});

test.beforeAll(() => mkdirSync(artifactDirectory, { recursive: true }));
