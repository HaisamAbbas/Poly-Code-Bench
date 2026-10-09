import { expect, test } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { inspectPage } from "./design-inspection";

const artifacts = resolve(__dirname, "../../../../.cache/frontend-design-review");
const healthId = "50000000-0000-4000-8000-000000000099";
const attestationId = "50000000-0000-4000-8000-000000000100";


for (const width of [360, 768, 1440]) {
  test(`all module pages at ${width}px`, async ({ page }) => {
    mkdirSync(artifacts, { recursive: true });
    await page.setViewportSize({ width, height: 1000 });
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/");
    await expect(page).toHaveURL(/\/leaderboard$/);
    await expect(page.locator("#release-select")).toBeVisible();
    const release = await page.locator("#release-select").inputValue();
    const query = `?release=${encodeURIComponent(release)}`;
    const routes = [
      ["leaderboard", `/leaderboard${query}`],
      ["language", `/languages/javascript${query}`],
      ["model-code", `/models/synthetic-code-a${query}`],
      ["model-answer", `/models/synthetic-answer-only${query}`],
      ["compare-selection", `/compare${query}`],
      ["compare-compatible", `/compare${query}&models=synthetic-code-a&models=synthetic-code-c`],
      ["compare-incompatible", `/compare${query}&models=synthetic-code-a&models=synthetic-code-b`],
      ["tasks", `/tasks${query}`],
      ["task-detail", `/tasks/synthetic-task-example${query}`],
      ["scorecard", `/scorecards/synthetic-scorecard-a${query}`],
      ["methodology", `/methodology/synthetic-ui-fixture-v1${query}`],
      ["model-missing", `/models/not-in-this-release${query}`],
      ["submission", "/model-submissions"],
      ["audit-lookup", "/audit-reports"],
      ["audit-report", `/audit-reports/${healthId}`],
      ["audit-missing", "/audit-reports/00000000-0000-4000-8000-000000000099"],
      ["curator", "/benchmark-audit"],
      ["attestation-lookup", "/audit-attestations"],
      ["attestation-invalid", "/audit-attestations?attestation_id=not-a-uuid"],
      ["attestation-valid", `/audit-attestations/${attestationId}`],
      ["attestation-revoked", "/audit-attestations/80000000-0000-4000-8000-000000000100"],
    ];
    const results = [];
    for (const [name, route] of routes) {
      await page.goto(route);
      await expect(page.locator("main h1")).toBeVisible();
      if (name === "task-detail") {
        await page.getByRole("button", { name: "Load public details" }).click();
        await expect(page.locator(".payload-card pre").first()).toBeVisible();
      }
      if (name === "attestation-invalid") {
        await expect(page.getByRole("textbox", { name: "Public attestation ID" })).toHaveAttribute("aria-invalid", "true");
        await expect(page.locator("main").getByRole("alert")).toContainText("Enter one valid attestation ID");
      }
      for (const table of await page.locator(".table-wrap").all()) {
        await expect(table).toHaveAttribute("data-layout", /table|cards/);
        const tableScroll = await table.evaluate((element) => ({
          overflows: element.scrollWidth > element.clientWidth + 1,
          overflowX: getComputedStyle(element).overflowX,
          tabIndex: element.getAttribute("tabindex"),
        }));
        if (tableScroll.overflows) {
          expect(tableScroll.overflowX).toMatch(/auto|scroll/);
          expect(tableScroll.tabIndex).toBe("0");
        }
      }
      const review = await inspectPage(page);
      for (const chart of await page.locator(".radar-scroll").all()) {
        expect(await chart.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
      }
      await page.screenshot({ path: resolve(artifacts, `${name}-${width}.png`), fullPage: true, caret: "initial", style: "nextjs-portal { visibility: hidden; }" });
      results.push({ name, route, ...review });
      writeFileSync(resolve(artifacts, `review-${width}.json`), JSON.stringify(results, null, 2));
      expect.soft(review.documentWidth, `${name} page overflow`).toBeLessThanOrEqual(width + 1);
      expect.soft(review.contrastFailures, `${name} text contrast`).toEqual([]);
      expect.soft(review.targetFailures, `${name} control targets`).toEqual([]);
    }
    writeFileSync(resolve(artifacts, `review-${width}.json`), JSON.stringify(results, null, 2));
    expect(errors).toEqual([]);
  });
}

test("leaderboard stays readable at 390px and 1024px", async ({ page }) => {
  mkdirSync(artifacts, { recursive: true });
  for (const width of [390, 1024]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/leaderboard");
    await expect(page.getByRole("heading", { name: "Coding capability, by language." })).toBeVisible();
    const review = await inspectPage(page);
    expect(review.documentWidth).toBeLessThanOrEqual(width + 1);
    expect(review.contrastFailures).toEqual([]);
    expect(review.targetFailures).toEqual([]);
    await expect(page.locator(".results-section .table-wrap")).toHaveAttribute("data-layout", /table|cards/);
    await page.screenshot({
      path: resolve(artifacts, `leaderboard-${width}.png`),
      fullPage: true,
      caret: "initial",
      style: "nextjs-portal { visibility: hidden; }",
    });
  }
});

test("leaderboard search, language filter, sorting, and browser history stay in sync", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/leaderboard");
  await expect(page.getByRole("heading", { name: "Coding capability, by language." })).toBeVisible();

  const release = await page.locator("#release-select").inputValue();
  const firstModelId = (await page.locator(".results-section .model-id").first().textContent())?.trim();
  expect(firstModelId).toBeTruthy();
  const search = page.getByRole("searchbox", { name: "Search configurations" });
  await search.fill(firstModelId!);
  await page.locator(".leaderboard-filters").getByRole("button", { name: "Apply filters" }).click();
  await expect(page).toHaveURL(new RegExp(`release=${encodeURIComponent(release)}.*q=${encodeURIComponent(firstModelId!)}`));
  await expect(page.locator(".results-section .data-table tbody tr")).toHaveCount(1);

  const firstLanguage = (await page.locator(".results-section .language-links a").first().textContent())?.trim();
  expect(firstLanguage).toBeTruthy();
  await page.getByLabel("Declared language", { exact: true }).selectOption(firstLanguage!);
  await page.locator(".leaderboard-filters").getByRole("button", { name: "Apply filters" }).click();
  let filteredSearch = new URL(page.url()).searchParams;
  expect(filteredSearch.get("language")).toBe(firstLanguage);
  expect(filteredSearch.get("q")).toBe(firstModelId);
  await expect(page.locator(".results-section .data-table tbody tr")).toHaveCount(1);
  const filteredUrl = page.url();

  await page.locator(".results-section .sort-link").first().click();
  await expect(page).toHaveURL(/sort_metric=/);
  filteredSearch = new URL(page.url()).searchParams;
  expect(filteredSearch.get("language")).toBe(firstLanguage);
  expect(filteredSearch.get("q")).toBe(firstModelId);
  await page.goBack();
  await expect(page).toHaveURL(filteredUrl);
  await expect(page.locator(".results-section .data-table tbody tr")).toHaveCount(1);
});

test("Languages navigation opens the release-backed directory and language details", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 900 });
  await page.goto("/leaderboard");
  const release = await page.locator("#release-select").inputValue();

  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "Languages" }).click();
  await expect(page).toHaveURL("/languages");
  await expect(page.getByRole("heading", { name: "Explore languages." })).toBeVisible();
  await expect(page.locator(".language-directory-link").first()).toBeVisible();

  const language = (await page.locator(".language-directory-name").first().textContent())?.trim();
  expect(language).toBeTruthy();
  await page.locator(".language-directory-link").first().click();
  await expect(page).toHaveURL(new RegExp(`/languages/${encodeURIComponent(language!)}\\?release=${encodeURIComponent(release)}`));
  await expect(page.getByRole("heading", { name: language!, level: 1 })).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/leaderboard");
  await page.getByRole("button", { name: "Menu", exact: true }).click();
  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "Languages" }).click();
  await expect(page).toHaveURL("/languages");
  await expect(page.getByRole("heading", { name: "Explore languages." })).toBeVisible();
});

test("mobile navigation, skip link, focus and reduced-motion loading", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  await page.goto("/audit-reports");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#main-content")).toBeFocused();
  const menu = page.getByRole("button", { name: "Menu", exact: true });
  await menu.focus();
  await page.keyboard.press("Enter");
  await expect(menu).toHaveAttribute("aria-expanded", "true");
  const current = page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "Audit reports", exact: true });
  await expect(current).toHaveAttribute("aria-current", "page");
  await current.focus();
  const focus = await current.evaluate((element) => getComputedStyle(element).outlineStyle);
  expect(focus).toBe("solid");
  await page.keyboard.press("Escape");
  await expect(menu).toHaveAttribute("aria-expanded", "false");
  await expect(menu).toBeFocused();
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/audit-reports/70000000-0000-4000-8000-000000000099", { waitUntil: "commit" });
  await expect(page.locator(".content-skeleton")).toBeVisible();
  expect(await page.locator(".skeleton-line").first().evaluate((element) => getComputedStyle(element).animationName)).toBe("none");
  await expect(page.getByRole("heading", { name: "Synthetic HumanEval" })).toBeVisible();
});
