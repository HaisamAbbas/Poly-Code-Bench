import type { Candidate, Observation, RunConfig, Scorecard, TaskVersion } from "./generated.js";

export type SharedClientContract =
  | TaskVersion
  | RunConfig
  | Candidate
  | Observation
  | Scorecard;

export const runConfigShapeCheck: RunConfig = {
  schema_version: 1,
  kind: "run_config",
  run_id: "11111111-1111-4111-8111-111111111111",
  task_set_digest: `sha256:${"a".repeat(64)}`,
  model_config_digest: `sha256:${"b".repeat(64)}`,
  harness_digest: `sha256:${"a".repeat(64)}`,
  protocol_id: "standard-agent-v1",
  sampling: {
    schema_version: 1,
    kind: "sampling_config",
    samples_per_task: 3,
    master_seed: "18446744073709551615",
    temperature: "0.000000",
    provider_seed_policy: "pass_if_supported",
  },
  budget_profile: "agent-small-v1",
  evaluation_policy_digest: `sha256:${"b".repeat(64)}`,
  hardware_class: "cpu-perf-x86-v1",
  judge_panel_digest: null,
  split: "public_scored",
};
