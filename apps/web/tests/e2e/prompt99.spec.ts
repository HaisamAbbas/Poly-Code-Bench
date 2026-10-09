import { expect, test } from "@playwright/test";

const apiOrigin = "http://127.0.0.1:8139";
const reportId = "50000000-0000-4000-8000-000000000099";
const privateFieldReportId = "60000000-0000-4000-8000-000000000099";
const delayedReportId = "70000000-0000-4000-8000-000000000099";
const privateSentinel = "PROMPT99_PRIVATE_FIXTURE_MARKER";

test("E2E: public report lookup renders aggregate scope and remains usable on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const apiResponse = await page.request.get(`${apiOrigin}/v1/public/audit-reports/${reportId}`);
  expect(apiResponse.status()).toBe(200);
  expect(apiResponse.headers()["cache-control"]).toContain("no-store");
  const apiBody = await apiResponse.json();
  expect(Object.keys(apiBody.data).sort()).toEqual([
    "assessed_tasks", "benchmark_label", "benchmark_version", "blocked_tasks", "complete_tasks",
    "high_risk_tasks", "insufficient_risk_tasks", "kind", "limitations", "low_risk_tasks",
    "medium_risk_tasks", "partial_tasks", "report_id", "review_state", "schema_version",
    "selected_tasks", "source_window_end", "source_window_start", "unknown_tasks", "unscanned_tasks",
  ].sort());
  expect(JSON.stringify(apiBody)).not.toContain(privateSentinel);

  await page.goto("/audit-reports");
  await expect(page.getByRole("heading", { name: "Open an audit report" })).toBeVisible();
  const reportInput = page.getByRole("textbox", { name: "Public report ID" });
  await reportInput.focus();
  await page.keyboard.type(reportId);
  await page.keyboard.press("Enter");

  await expect(page).toHaveURL(`/audit-reports/${reportId}`);
  await expect(page.getByRole("heading", { name: "Benchmark health report" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Synthetic HumanEval" })).toBeVisible();
  await expect(page.getByText("Published aggregate report")).toBeVisible();
  await expect(page.getByText("12", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("3", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Unknown", { exact: true })).toBeVisible();
  await expect(page.getByText("Unscanned", { exact: true })).toBeVisible();
  await expect(page.getByText(/does not establish the absence of contamination/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Comparable trends" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Model eligibility" })).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText(privateSentinel);

  const viewportFits = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
  expect(viewportFits).toBe(true);
});

test("E2E: public report displays an accessible loading state while the API is delayed", async ({ page }) => {
  await page.goto("/audit-reports");
  await page.getByRole("textbox", { name: "Public report ID" }).fill(delayedReportId);
  await page.getByRole("button", { name: "Open report" }).click();
  await expect(page.getByRole("status")).toContainText("Loading public report");
  await expect(page.getByRole("heading", { name: "Synthetic HumanEval" })).toBeVisible();
});

test("E2E: invalid public projection fails closed without exposing a private field", async ({ page }) => {
  const response = await page.request.get(
    `${apiOrigin}/v1/public/audit-reports/${privateFieldReportId}`,
  );
  expect(response.status()).toBe(404);
  expect(await response.text()).not.toContain(privateSentinel);

  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(`/audit-reports/${privateFieldReportId}`);
  await expect(page.locator(".audit-unavailable[role='alert']")).toContainText("Public audit report not found");
  await expect(page.locator("body")).not.toContainText(privateSentinel);
});

test("E2E: curator page discloses the authorization block without requesting private data", async ({ page }) => {
  const privateApiRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/v1/benchmark-audit")) privateApiRequests.push(request.url());
  });

  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/benchmark-audit");
  await expect(page.getByRole("heading", { name: "Curator access is not configured" })).toBeVisible();
  await expect(page.getByRole("status")).toContainText("No private data loaded");
  await expect(page.getByRole("status")).toContainText("No private API request was sent");
  expect(privateApiRequests).toEqual([]);
  await expect(page.locator("body")).not.toContainText(privateSentinel);
});
