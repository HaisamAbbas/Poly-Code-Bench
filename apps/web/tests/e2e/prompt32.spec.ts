import { expect, test } from "@playwright/test";

test("E2E: public pages show published results and no operator controls", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  const requests: string[] = [];
  page.on("request", (request) => requests.push(new URL(request.url()).pathname));

  await page.goto("/leaderboard");
  await expect(page.getByRole("heading", { name: "Coding capability, by language." })).toBeVisible();
  const navigation = page.getByRole("navigation", { name: "Main navigation" });
  await expect(navigation.getByRole("link", { name: "Leaderboard" })).toBeVisible();
  await expect(navigation.getByRole("link", { name: "Audit reports" })).toBeVisible();
  await expect(navigation.getByRole("link", { name: "Verify attestation" })).toBeVisible();
  await expect(navigation.getByRole("link", { name: "Submit model" })).toHaveCount(0);

  for (const route of [
    "/admin",
    "/admin/benchmark-audit",
    "/benchmark-audit",
    "/model-submissions",
    "/auth/sign-in",
    "/auth/callback",
    "/auth/sign-out",
    "/api/model-submissions",
    "/api/benchmark-audit/scope-preview",
    "/api/benchmark-audit/resource-plan",
  ]) {
    const response = await page.request.get(route);
    expect(response.status(), route).toBe(404);
  }

  expect(requests).not.toContain("/v1/benchmark-audit/scope-preview");
  expect(requests).not.toContain("/v1/model-submissions");
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(1281);
});

test("E2E: public navigation remains keyboard accessible on a narrow screen", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/leaderboard");
  const menu = page.getByRole("button", { name: "Menu", exact: true });
  await menu.focus();
  await page.keyboard.press("Enter");
  await expect(menu).toHaveAttribute("aria-expanded", "true");
  const navigation = page.getByRole("navigation", { name: "Main navigation" });
  await navigation.getByRole("link", { name: "Languages" }).focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL("/languages");
  await expect(page.getByRole("heading", { name: "Explore languages." })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(376);
});
