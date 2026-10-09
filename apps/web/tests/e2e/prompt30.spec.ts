import { expect, test, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { browserArtifacts } from "./browser-artifacts";

const artifactDirectory = browserArtifacts(30);

async function openBoard(page: Page): Promise<string> {
  await page.goto("/leaderboard");
  await expect(page.getByRole("heading", { name: "Leaderboard" })).toBeVisible();
  return page.locator("#release-select").inputValue();
}

test("E2E-39 segment: release-backed leaderboard values link to their source scorecards", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1050 });
  const releaseId = await openBoard(page);

  await expect(page.getByText("Synthetic internal test data", { exact: true })).toBeVisible();
  await expect(page.getByText(/not live benchmark results/i)).toBeVisible();
  await expect(page.getByRole("table", { name: /published configuration metrics/i })).toBeVisible();
  await expect(page.getByRole("link", { name: /Code score: Measured, 89.750000 score/i }).first()).toBeVisible();
  await expect(page.getByText("Interval 81.000000–96.000000")).toBeVisible();
  await expect(page.getByText("Gated zero", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("N/A", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Missing", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Pending review", { exact: true }).first()).toBeVisible();
  await expect(page.locator(".scope-facts")).toContainText("exploratory");

  const scoreLink = page.getByRole("link", { name: /Code score: Measured, 89.750000 score/i }).first();
  const href = await scoreLink.getAttribute("href");
  expect(href).toContain("/v1/scorecards/synthetic-scorecard-a");
  expect(href).toContain(`release=${releaseId}`);
  const source = await page.request.get(new URL(href!, page.url()).toString());
  expect(source.ok()).toBeTruthy();
  const evidence = await source.json();
  expect(evidence.data.release_id).toBe(releaseId);
  expect(evidence.data.metrics.some((metric: { metric_id: string }) => metric.metric_id === "code_score")).toBeTruthy();

  mkdirSync(artifactDirectory, { recursive: true });
  await page.screenshot({ path: resolve(artifactDirectory, "leaderboard-desktop.png"), fullPage: true });
});

test("E2E-39 segment: URL filters, metric sorting, release selection, and language diagnostics stay in scope", async ({ page }) => {
  const releaseId = await openBoard(page);
  await page.locator("#language-select").selectOption("javascript");
  await page.getByRole("button", { name: "Apply filter" }).click();
  await expect(page).toHaveURL(/language=javascript/);
  await expect(page.getByText(/Metrics and coverage remain release-wide/)).toBeVisible();
  await expect(page.getByRole("row", { name: /Fixture Code System A/ })).toBeVisible();
  await expect(page.getByRole("row", { name: /Fixture Code System B/ })).toHaveCount(0);

  await page.locator("thead").getByRole("link", { name: /Code score/ }).click();
  await expect(page).toHaveURL(/sort_metric=code_score/);
  await expect(page).toHaveURL(/direction=desc/);
  await expect(page.locator("th[aria-sort='descending']")).toContainText("Code score");

  const options = await page.locator("#release-select option").evaluateAll((nodes) =>
    nodes.map((node) => ({ value: (node as HTMLOptionElement).value, label: node.textContent ?? "" })),
  );
  const otherRelease = options.find((option) => option.value !== releaseId);
  expect(otherRelease).toBeDefined();
  await page.locator("#release-select").selectOption(otherRelease!.value);
  await page.getByRole("button", { name: "Load release" }).click();
  await expect(page).toHaveURL(new RegExp(`release=${otherRelease!.value}`));
  await expect(page).toHaveURL(/language=javascript/);

  const selectedRelease = await page.locator("#release-select").inputValue();
  await page.goto(`/languages/javascript?release=${selectedRelease}`);
  await expect(page.getByRole("heading", { name: "javascript", level: 1 })).toBeVisible();
  const languageMeasurements = page.getByRole("region", { name: "javascript language-specific measurements" });
  await expect(languageMeasurements).toContainText("74.00");
  await expect(languageMeasurements).not.toContainText("89.750000");
  const javascriptScore = languageMeasurements.getByRole("link", { name: /Correctness: Measured, 74.000000 score/i }).first();
  await expect(javascriptScore).toHaveAttribute("href", new RegExp("synthetic-scorecard-a-javascript"));
  await languageMeasurements.getByRole("link", { name: /Correctness/ }).first().click();
  await expect(page).toHaveURL(/sort_metric=dimension_correctness/);
  await expect(languageMeasurements.getByRole("row").nth(1)).toContainText("Fixture Code System C");
  await expect(page.getByRole("heading", { name: "Diagnostic profiles" })).toBeVisible();
  await expect(page.locator(".language-tool-coverage")).toHaveCount(2);
  await expect(page.locator(".language-tool-coverage").nth(0)).toContainText("eslint");
  await expect(page.locator(".language-tool-coverage").nth(1)).toContainText("eslint");
  await expect(page.getByText("TypeScript", { exact: false })).toHaveCount(0);
  await expect(page.getByText("No language-specific code dimensions were published for this configuration.")).toHaveCount(0);
  await page.screenshot({ path: resolve(artifactDirectory, "javascript-profile.png"), fullPage: true });
});

test("E2E-40 subcase: code profiles expose measured dimensions and answer-only profiles omit code charts", async ({ page }) => {
  const releaseId = await openBoard(page);
  await page.goto(`/models/synthetic-code-a?release=${releaseId}`);
  await expect(page.getByRole("heading", { name: "Fixture Code System A" })).toBeVisible();
  await expect(page.getByRole("img", { name: "Code-only dimension radar" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Language × dimension coverage" })).toBeVisible();
  await expect(page.getByRole("rowheader", { name: "javascript" })).toBeVisible();
  const languageHeatmap = page.getByRole("region", { name: "Language by dimension coverage" });
  const javascriptHeatmapMetric = languageHeatmap.getByRole("row", { name: /javascript/ })
    .getByRole("link", { name: /Correctness: Measured, 74.000000 score/i });
  await expect(javascriptHeatmapMetric).toHaveAttribute("href", /synthetic-scorecard-a-javascript/);
  await expect(page.locator(".profile-stats")).toContainText("$0.123456");
  await expect(page.getByText("Highest measured code dimension", { exact: true })).toBeVisible();
  await page.screenshot({ path: resolve(artifactDirectory, "model-code-profile.png"), fullPage: true });

  await page.goto(`/models/synthetic-answer-only?release=${releaseId}`);
  await expect(page.getByRole("heading", { name: "Fixture Answer Only System" })).toBeVisible();
  await expect(page.getByText("No generated-code dimensions were published for this configuration.")).toBeVisible();
  await expect(page.getByRole("img", { name: "Code-only dimension radar" })).toHaveCount(0);
  await expect(page.getByText("Answer accuracy", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "No measured code dimensions" })).toBeVisible();
  await page.screenshot({ path: resolve(artifactDirectory, "model-answer-only.png"), fullPage: true });
});

test("E2E-40 subcase: keyboard access, narrow and wide viewports, empty and API error states", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  const releaseId = await openBoard(page);
  const width = await page.evaluate(() => ({ viewport: window.innerWidth, document: document.documentElement.scrollWidth }));
  expect(width.document).toBeLessThanOrEqual(width.viewport + 1);
  await page.screenshot({ path: resolve(artifactDirectory, "leaderboard-mobile.png"), fullPage: true });

  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "PolyCodeBench home" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Menu", exact: true })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.locator("#release-select")).toBeFocused();
  const releaseOptions = await page.locator("#release-select option").evaluateAll((nodes) =>
    nodes.map((node) => (node as HTMLOptionElement).value),
  );
  expect(releaseOptions.length).toBeGreaterThan(1);
  await page.locator("#release-select").selectOption(releaseOptions[0]);
  await page.keyboard.press("ArrowDown");
  expect(await page.locator("#release-select").inputValue()).toBe(releaseOptions[1]);
  const scrollableTable = page.getByRole("region", { name: "Scrollable published configuration metrics" });
  await scrollableTable.focus();
  await expect(scrollableTable).toBeFocused();

  await page.setViewportSize({ width: 1440, height: 1000 });
  const wideWidth = await page.evaluate(() => ({ viewport: window.innerWidth, document: document.documentElement.scrollWidth }));
  expect(wideWidth.document).toBeLessThanOrEqual(wideWidth.viewport + 1);
  await page.goto(`/leaderboard?release=${releaseId}&language=uncovered-language`);
  await expect(page.getByRole("heading", { name: "No configurations cover uncovered-language" })).toBeVisible();
  await page.goto(`/models/not-in-this-release?release=${releaseId}`);
  await expect(page.locator(".state-error[role='alert']")).toContainText("resource is not available");
  await expect(page.locator(".state-error[role='alert']")).not.toContainText("not-in-this-release");
  await page.screenshot({ path: resolve(artifactDirectory, "model-not-found.png"), fullPage: true });
});
