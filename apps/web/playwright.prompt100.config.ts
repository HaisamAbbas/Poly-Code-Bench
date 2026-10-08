import { mkdirSync } from "node:fs";
import { defineConfig, devices } from "@playwright/test";
import { resolve } from "node:path";

const webRoot = __dirname;
const repoRoot = resolve(webRoot, "../..");
const releaseStore = resolve(repoRoot, `.cache/prompt100-e2e-${process.pid}.sqlite3`);
const apiOrigin = "http://127.0.0.1:8140";
const webOrigin = "http://127.0.0.1:3140";

mkdirSync(resolve(repoRoot, ".cache"), { recursive: true });

export default defineConfig({
  testDir: "./tests/e2e",
  testMatch: "prompt100.spec.ts",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  timeout: 60_000,
  retries: 0,
  workers: 1,
  expect: { timeout: 15_000 },
  reporter: "list",
  outputDir: resolve(repoRoot, ".cache/playwright/prompt100-results"),
  use: {
    baseURL: webOrigin,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    ...devices["Desktop Chrome"],
  },
  webServer: [
    {
      command: "uv run --locked --group dev python apps/web/tests/e2e/launch-prompt100-api.py",
      cwd: repoRoot,
      url: `${apiOrigin}/healthz`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        PCB_RELEASE_STORE_PATH: releaseStore,
        PCB_ENVIRONMENT: "development",
      },
    },
    {
      command: "node ./node_modules/next/dist/bin/next dev --hostname 127.0.0.1 --port 3140",
      cwd: webRoot,
      url: `${webOrigin}/audit-attestations`,
      reuseExistingServer: false,
      timeout: 180_000,
      env: {
        PCB_PUBLIC_API_URL: `${apiOrigin}/v1`,
        PCB_ENVIRONMENT: "development",
        PCB_NEXT_DIST_DIR: ".next/prompt100-e2e",
      },
    },
  ],
});
