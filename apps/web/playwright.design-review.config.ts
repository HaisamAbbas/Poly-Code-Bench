import { defineConfig } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

const repoRoot = resolve(__dirname, "../..");
const store = resolve(repoRoot, `.cache/design-review-${process.pid}.sqlite3`);
mkdirSync(resolve(repoRoot, ".cache/frontend-design-review"), { recursive: true });
process.env.PCB_TEST_RELEASE_STORE_PATH = store;

export default defineConfig({
  testDir: "./tests/e2e",
  testMatch: "design-review.spec.ts",
  globalSetup: "./tests/e2e/global-setup.ts",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  timeout: 420_000,
  expect: { timeout: 20_000 },
  retries: 0,
  workers: 1,
  reporter: "list",
  outputDir: resolve(repoRoot, ".cache/playwright/design-review"),
  use: { baseURL: "http://127.0.0.1:3151", screenshot: "only-on-failure" },
  webServer: [
    {
      command: "uv run --locked --group dev python apps/web/tests/e2e/launch-design-review-api.py",
      cwd: repoRoot,
      url: "http://127.0.0.1:8151/healthz",
      timeout: 120_000,
      env: { PCB_RELEASE_STORE_PATH: store, PCB_ENVIRONMENT: "development" },
    },
    {
      command: "node ./node_modules/next/dist/bin/next dev --hostname 127.0.0.1 --port 3151",
      cwd: __dirname,
      url: "http://127.0.0.1:3151/leaderboard",
      timeout: 180_000,
      env: {
        PCB_PUBLIC_API_URL: "http://127.0.0.1:8151/v1",
        PCB_ENVIRONMENT: "development",
        PCB_NEXT_DIST_DIR: ".next/design-review",
      },
    },
  ],
});
