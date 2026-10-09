import { resolve } from "node:path";

export function browserArtifacts(prompt: number): string {
  const repoRoot = resolve(__dirname, "../../../..");
  const root = process.env.PCB_BROWSER_ARTIFACT_ROOT ?? resolve(repoRoot, "docs/implementation/evidence");
  return resolve(root, `prompt-${prompt}`);
}
