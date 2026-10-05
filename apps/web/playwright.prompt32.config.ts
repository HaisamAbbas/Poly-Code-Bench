import { mkdirSync } from "node:fs";
import { defineConfig, devices } from "@playwright/test";
import { resolve } from "node:path";

const webRoot = __dirname;
const repoRoot = resolve(webRoot, "../..");
const artifacts = resolve(repoRoot, "docs/implementation/evidence/prompt-32");
const releaseStore = resolve(repoRoot, `.cache/prompt32-e2e-${process.pid}.sqlite3`);
const apiOrigin = "http://127.0.0.1:8132";
const webOrigin = "http://127.0.0.1:3123";
const oidcOrigin = "http://127.0.0.1:8140";
const oidcClientId = "prompt32-e2e-client";
const webAuthSigningKey = "synthetic-prompt32-signing-key-not-a-secret-0001";

mkdirSync(artifacts, { recursive: true });
mkdirSync(resolve(repoRoot, ".cache"), { recursive: true });
process.env.PCB_TEST_RELEASE_STORE_PATH = releaseStore;
process.env.PCB_ENVIRONMENT = "development";

export default defineConfig({
  testDir: "./tests/e2e",
  testMatch: "prompt32.spec.ts",
  globalSetup: "./tests/e2e/global-setup.ts",
  globalTeardown: "./tests/e2e/prompt32-global-teardown.ts",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  timeout: 60_000,
  retries: 0,
  workers: 1,
  expect: { timeout: 15_000 },
  reporter: [["list"], ["json", { outputFile: resolve(artifacts, "browser-results.json") }]],
  outputDir: resolve(repoRoot, ".cache/playwright/prompt32-results"),
  use: {
    baseURL: webOrigin,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    ...devices["Desktop Chrome"],
  },
  webServer: [
    {
      command: "node ./tests/e2e/fake-oidc-provider.mjs",
      cwd: webRoot,
      url: `${oidcOrigin}/.well-known/openid-configuration`,
      reuseExistingServer: false,
      timeout: 30_000,
      env: { PCB_FAKE_OIDC_PORT: "8140" },
    },
    {
      command: "uv run --locked --group dev python apps/web/tests/e2e/launch-prompt32-api.py",
      cwd: repoRoot,
      url: `${apiOrigin}/v1/releases`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        PCB_RELEASE_STORE_PATH: releaseStore,
        PCB_ENVIRONMENT: "development",
        PCB_WEB_AUTH_SIGNING_KEY: webAuthSigningKey,
      },
    },
    {
      command: "node ./node_modules/next/dist/bin/next dev --hostname 127.0.0.1 --port 3123",
      cwd: webRoot,
      url: `${webOrigin}/leaderboard`,
      reuseExistingServer: false,
      timeout: 180_000,
      env: {
        PCB_PUBLIC_API_URL: `${apiOrigin}/v1`,
        PCB_ENVIRONMENT: "development",
        PCB_OIDC_ISSUER: oidcOrigin,
        PCB_OIDC_CLIENT_ID: oidcClientId,
        PCB_OIDC_REDIRECT_URI: `${webOrigin}/auth/callback`,
        PCB_WEB_ORIGIN: webOrigin,
        PCB_WEB_AUTH_SIGNING_KEY: webAuthSigningKey,
      },
    },
  ],
});
