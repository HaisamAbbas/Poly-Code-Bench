import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

export default function globalSetup(): void {
  const repoRoot = resolve(__dirname, "../../..");
  const store = process.env.PCB_TEST_RELEASE_STORE_PATH;
  if (!store) throw new Error("Playwright release fixture store path was not configured.");
  mkdirSync(resolve(repoRoot, ".cache"), { recursive: true });
  execFileSync(
    "uv",
    [
      "run",
      "--locked",
      "--group",
      "dev",
      "python",
      "-m",
      "polycodebench_api.dev_fixture",
      "--store",
      store,
      "--count",
      "2",
    ],
    { cwd: repoRoot, stdio: "inherit" },
  );
}
