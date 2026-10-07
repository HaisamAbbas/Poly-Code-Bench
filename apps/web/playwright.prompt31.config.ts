import { defineConfig, devices } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

const webRoot = __dirname;
const repoRoot = resolve(webRoot, "../..");
const artifacts = resolve(repoRoot, "docs/implementation/evidence/prompt-31");
const releaseStore = resolve(repoRoot, `.cache/prompt31-e2e-${process.pid}.sqlite3`);
const apiOrigin = "http://127.0.0.1:8130";
const webOrigin = "http://127.0.0.1:3121";

mkdirSync(artifacts, { recursive: true });
process.env.PCB_TEST_RELEASE_STORE_PATH = releaseStore;

export default defineConfig({
  testDir: "./tests/e2e",
  testMatch: "prompt31.spec.ts",
  globalSetup: "./tests/e2e/global-setup.ts",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  timeout: 60_000,
  retries: 0,
  workers: 1,
  expect: { timeout: 15_000 },
  reporter: [["list"], ["./tests/e2e/sanitized-json-reporter.mjs", {
    outputFile: resolve(artifacts, "browser-results.json"),
    suiteName: "Prompt 31 public analysis workflows",
  }]],
  outputDir: resolve(repoRoot, ".cache/playwright/prompt31-results"),
  use: {
    baseURL: webOrigin,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    ...devices["Desktop Chrome"],
  },
  webServer: [
    {
      command: "uv run --locked --group dev uvicorn polycodebench_api.app:app --host 127.0.0.1 --port 8130 --log-level warning",
      cwd: repoRoot,
      url: `${apiOrigin}/v1/releases`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: { PCB_RELEASE_STORE_PATH: releaseStore },
    },
    {
      command: "node ./node_modules/next/dist/bin/next dev --hostname 127.0.0.1 --port 3121",
      cwd: webRoot,
      url: `${webOrigin}/leaderboard`,
      reuseExistingServer: false,
      timeout: 180_000,
      env: {
        PCB_PUBLIC_API_URL: `${apiOrigin}/v1`,
        PCB_NEXT_DIST_DIR: ".next/prompt31-e2e",
      },
    },
  ],
});
