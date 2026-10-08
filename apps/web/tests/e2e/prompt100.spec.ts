import { expect, test } from "@playwright/test";

const apiOrigin = "http://127.0.0.1:8140";
const validId = "50000000-0000-4000-8000-000000000100";
const tamperedId = "60000000-0000-4000-8000-000000000100";
const privateFieldId = "70000000-0000-4000-8000-000000000100";
const revokedId = "80000000-0000-4000-8000-000000000100";
const supersededId = "90000000-0000-4000-8000-000000000100";
const privateSentinel = "PROMPT100_PRIVATE_FIXTURE_MARKER";

test("E2E: valid development signature shows finite scope and explicitly denies endorsement", async ({ page }) => {
  const response = await page.request.get(`${apiOrigin}/v1/public/audit-attestations/${validId}`);
  expect(response.status()).toBe(200);
  expect(response.headers()["cache-control"]).toContain("no-store");
  const body = await response.json();
  expect(body.data.verification.signature_valid).toBe(true);
  expect(body.data.verification.current_endorsement).toBe(false);
  expect(body.data.verification.result).toBe("development_key_not_endorsed");
  expect(JSON.stringify(body)).not.toContain(privateSentinel);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/audit-attestations");
  await page.getByRole("textbox", { name: "Public attestation ID" }).fill(validId);
  await page.getByRole("button", { name: "Verify attestation" }).click();
  await expect(page).toHaveURL(`/audit-attestations/${validId}`);
  await expect(page.getByRole("heading", { name: "Benchmark Audit Attestation" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Development signature valid; not an endorsement" })).toBeVisible();
  await expect(page.getByText(/current key or lifecycle state does not provide an endorsement/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Synthetic HumanEval" })).toBeVisible();
  await expect(page.getByText(/model-agnostic and makes no model-specific eligibility claim/)).toBeVisible();
  await expect(page.getByText("12", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/not an independent certification authority/)).toBeVisible();
  await expect(page.getByText(/not independently timestamp-proven/)).toBeVisible();
  await expect(page.locator("body")).not.toContainText(privateSentinel);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("E2E: tampered signature withholds every signed claim from the browser", async ({ page }) => {
  const response = await page.request.get(`${apiOrigin}/v1/public/audit-attestations/${tamperedId}`);
  expect(response.status()).toBe(200);
  expect((await response.json()).data.verification.result).toBe("invalid_signature");

  await page.goto(`/audit-attestations/${tamperedId}`);
  await expect(page.getByRole("heading", { name: "Signature is invalid" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Signed claims withheld" })).toBeVisible();
  await expect(page.locator("body")).not.toContainText("TAMPERED CLAIM");
  await expect(page.locator("body")).not.toContainText("Synthetic HumanEval");
});

test("E2E: current revocation is visible and untrusted projection fields stay private", async ({ page }) => {
  const revoked = await page.request.get(`${apiOrigin}/v1/public/audit-attestations/${revokedId}`);
  expect(revoked.status()).toBe(200);
  expect((await revoked.json()).data.verification.result).toBe("revoked");
  await page.goto(`/audit-attestations/${revokedId}`);
  await expect(page.getByRole("heading", { name: "Attestation revoked" })).toBeVisible();

  const invalid = await page.request.get(`${apiOrigin}/v1/public/audit-attestations/${privateFieldId}`);
  expect(invalid.status()).toBe(404);
  expect(await invalid.text()).not.toContain(privateSentinel);
  await page.goto(`/audit-attestations/${privateFieldId}`);
  await expect(page.locator('.audit-unavailable[role="alert"]')).toContainText(
    "Public attestation not found",
  );
  await expect(page.locator("body")).not.toContainText(privateSentinel);

  const superseded = await page.request.get(`${apiOrigin}/v1/public/audit-attestations/${supersededId}`);
  expect(superseded.status()).toBe(200);
  const supersededView = (await superseded.json()).data;
  expect(supersededView.verification.result).toBe("superseded");
  expect(supersededView.successor_id).toBe(validId);
  await page.goto(`/audit-attestations/${supersededId}`);
  await expect(page.getByText("Corrected by", { exact: false })).toBeVisible();
  await expect(page.getByRole("link", { name: validId })).toHaveAttribute(
    "href",
    `/audit-attestations/${validId}`,
  );
});
