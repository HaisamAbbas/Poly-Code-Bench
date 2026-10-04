import { readdirSync, unlinkSync } from "node:fs";
import { resolve } from "node:path";

export default function globalTeardown(): void {
  const repoRoot = resolve(__dirname, "../../../..");
  const cacheDirectory = resolve(repoRoot, ".cache");
  for (const entry of readdirSync(cacheDirectory)) {
    if (entry === "prompt32-e2e-client.json") {
      unlinkSync(resolve(cacheDirectory, entry));
    }
  }
}
