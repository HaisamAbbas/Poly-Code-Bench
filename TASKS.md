# Benchmark audit implementation goal

User-authorized sequence: implement Prompts 83–106 from the benchmark audit specification, in order; review each prompt against the production engineering constitution; commit and push each prompt separately as `Haisam Abbas <HaisamAbbas@outlook.com>`.

## Prompt checklist

- [x] Prompt 83 — baseline inventory and audit foundations. Commit `48adefb`.
- [x] Prompt 84 — benchmark catalog and planning contracts. Commit `b1c2134`.
- [x] Prompt 85 — immutable audit persistence and queue foundations. Commit `2f6b811`.
- [x] Prompt 86 — bounded benchmark import foundations. Commit `bcdf72f`.
- [x] Prompt 87 — task fingerprints. Commit `a32fbca`.
- [x] Prompt 88 — source connector contracts and coverage. Commit `dbe840b`.
- [x] Prompt 89 — bounded retrieval, coverage and replay. Commit `f6f752c`.
- [x] Prompt 90 — match verification, review and disputes. Commit `187d4e2`.
- [x] Prompt 91 — explainable risk index, eight signals and missingness gates. Commit `a9b599e`.
- [x] Prompt 92 — temporal holdouts, model contexts and timestamp commitments. Commit `483691e`.
- [x] Prompt 93 — sealed evaluations, encryption, access and canaries (partial foundations; production prerequisites recorded in the report). Commit `be7121a`.
- [x] Prompt 94 — optional behavioral diagnostic protocols and applicability (partial foundations; production prerequisites recorded in the report). Commit `6d8b4d6`.
- [x] Prompt 95 — firewall admission and independently validated replacements (partial foundations; production prerequisites recorded in the report).
- [x] Prompt 96 - continuous monitoring, risk changes and owner alerts (partial foundations; production prerequisites recorded in the report).
- [x] Prompt 97 - benchmark health aggregation and comparable trends (partial foundations; production prerequisites recorded in the report).
- [x] Prompt 98 — private API, CLI, SDK and permission contracts (partial foundations; transition adapters, shared ACL, private artifact-byte authorization and live service evidence remain).
- [x] Prompt 99 — benchmark health dashboard and evidence journeys (partial foundations; live reviewed projections, private curator ACL/transitions and full context/trend/revocation journeys remain unavailable).
- [x] Prompt 100 — signed audit attestations and public verification (partial foundations; production signer, reviewer, timestamp, trust publication and live PostgreSQL evidence remain blocked).
- [x] Prompt 101 — actual benchmark pilot and detector calibration (partial preflight and calibration foundations; approved source bytes/rights, live scans, independent labels and behavioral ground truth remain blocked).
- [x] Prompt 102 — live replacements, sealed workflow and monitoring evidence (partial reference-only campaign accounting; no authorized authors, production key/timestamp authority, live source rescan or resolver exists).
- [x] Prompt 103 — operations, malicious-input defenses and recovery/load (partial; current-schema restore/load prerequisites are missing).
- [x] Prompt 104 — broader benchmark adapters and scope conformance (partial; only GSM8K adds a synthetic-fixture parser, all other source/runtime rights and multimodal evidence remain pending or blocked).
- [ ] Prompt 105 — integrated end-to-end demonstration and reviewed projections.
- [ ] Prompt 106 — final traceability audit, fixes and operator handoff.

## Persistent blockers

- No approved benchmark/corpus snapshots, source bytes, rights scope or trusted source-artifact resolver are available. Never turn candidate or fixture evidence into accepted live findings.
- No independent match detector or observed-risk calibration set is available. Low/medium risk tiers require a policy with validated calibration and complete finite scope.
- No current-schema benchmark-audit database, reviewer session, approved model context or audit-specific exposure writer is configured. The designated local test database lacks its Alembic schema; do not migrate it as part of this prompt.
- The available ignored local recovery backup is synthetic but predates the benchmark-audit schema; the isolated restore correctly fails with `benchmark_audit_schema_missing`. No approved persisted retrieval-index configuration/rebuild adapter or representative corpus/monitor load environment is available.
- No benchmark payloads or native harnesses are approved for Prompt104. GAIA requires gated access approval; HellaSwag upstream is blocked under a recorded GitHub DMCA notice; image/OCR, agent-environment and tool-call audit adapters remain unsupported. Do not substitute mirrors.
- The implementation specification's §1 source bridge claims five historical source MDs but lists only three; the other two are absent from the workspace. Supply/enumerate them to close final historical-source traceability.
- Keep unrelated dirty work out of prompt commits; stage only the exact prompt-owned files or hunks.
