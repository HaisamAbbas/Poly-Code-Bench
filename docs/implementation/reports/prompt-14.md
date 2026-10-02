# Prompt 14 — Implement judging, review and calibration

Date: 2026-10-02 · Phase 2 · WP-14 · Read T §15, A §8.8 · E2E-21, E2E-22

## Prompt 14 / Phase 2 — PARTIAL

### 1. Implemented functionality and changed files

Anonymized evidence packets and a frozen rubric/panel pair; three logical votes through the real
model gateway with schema validation, bounded schema-repair recovery and exact-decimal averaging;
disagreement triggers; permission-checked reviewer access with immutable supersession; the disjoint
calibration workflow with a blocked-when-absent report; and the evaluation handoff that turns real
evidence into a bounded packet input.

New:

- `packages/core/src/polycodebench_core/judge_contracts.py` — rubric, panel, packet, packet input,
  span, item, vote, delivery, item outcome, result, adjudication, cohort and reviewer-queue
  contracts; rejection reasons; exact-decimal mean; identity-leak assertion; content-derived
  `derived_judge_id` / `adjudication_identity` / `judge_record_bytes`.
- `packages/core/src/polycodebench_core/judge_calibration.py` — labels, calibration packets, the
  frozen policy, agreement/confusion/bias reporting, the blocked-report constructor.
- `packages/core/src/polycodebench_core/judge_prompts.py` — system prompt, packet rendering with
  escaped untrusted text, the packet-derived response JSON Schema, the instruction-attempt
  detector, the frozen repair instruction and response validation.
- `packages/services/src/polycodebench_services/judging.py` — packet assembly, vote parsing,
  per-item aggregation and triggers, `audit_selected`, reviewer decisions and supersession.
- `packages/services/src/polycodebench_services/judging_calibration.py` — seeded disjoint
  stratified selection, label import with qualification/conflict checks, agreement, confusion,
  bias and audit selection, or `blocked` with named missing inputs.
- `packages/persistence/src/polycodebench_persistence/judging.py` and
  `.../migrations/versions/b9e04c7a1f38_judge_execution_records.py` — `judge_cohort`,
  `judge_packet`, `judge_delivery`, `judge_vote`, `judge_result`, `judge_item_result`,
  `calibration_label`, all append-only (the migration refuses to run if `judge_packet` has rows).
- `packages/orchestration/src/polycodebench_orchestration/judge/{__init__,protocol,runner,cli}.py`
  — `pcb-judge` (`rubric`, `panel`, `packet`, `validate-vote`, `run`, `result`, `review-queue`,
  `show`, `adjudicate`, `calibration`), a tool-free judge protocol, and the runner that dispatches
  three votes through the same `ModelGateway` the solve path uses.
- `packages/evaluation/src/polycodebench_evaluation/judge_inputs.py` — `EvaluationEvidence` →
  `JudgePacketInput`, excluding the gate result and candidate digest, extracting candidate
  comments as untrusted data.
- `config/judging/{rubric-v1,panel-v1,calibration-v1}.yaml` — nine residual items with declared
  anchors; `judge-panel-v1` (three votes, distinct seeds, ≤2 replacements, no tools, blinded
  fields, 10% audit sample, `effective_for_scoring: false`); the 30-per-language / 80% gate.
- `tests/judging_support.py`, `tests/test_judging_core.py`, `tests/test_judging_postgres.py`,
  `tests/test_judge_cli.py`, `tests/test_judge_inputs.py`, `tests/fixtures/judging/cases.json`
  (16 adversarial responses), `scripts/check_judge_boundaries.py`.
- Evidence: `docs/implementation/evidence/prompt-14-{integration,e2e-21,e2e-22,calibration}.json`.

Changed in existing files: judge tables in `packages/persistence/src/polycodebench_persistence/models.py`,
a `load()` in `.../model_configs.py`, `pyproject.toml` (the `pcb-judge` entry point), and the
ledgers `tickets.md`, `e2e-matrix.md`, `requirements-matrix.md`, `decisions.md`, `commands.md`,
`progress.json`.

### 2. Tests/commands actually run and their results

Environment: Windows dev sandbox, `.venv` Python 3.12; local PostgreSQL 17.6 on 55432 and SeaweedFS
4.48 on 8333 via `docker compose`; `PYTHONPATH=packages/plugins-api/src` (see section 4's shared-tree
note). Judge responses in every command below are FIXTURES replayed through the real gateway.

- `createdb pcb_prompt14_test`; `provision_roles.sql`; `grant_permissions.sql` — PASS.
- `alembic -c packages/persistence/alembic.ini upgrade head` then `check` — PASS: 12 revisions
  ending at `b9e04c7a1f38`; "No new upgrade operations detected" (no model/schema drift).
- `pytest tests/test_judging_core.py` — PASS: 56 offline tests (frozen rubric/panel, packet
  blinding, 16 adversarial responses, three-vote averaging, recovery bound, disagreement triggers,
  adjudication and supersession, cohort versioning, calibration selection/metrics/blocked paths, and
  11 regressions from the independent review).
- `pytest tests/test_judging_postgres.py` — PASS: 10 tests on real PostgreSQL/SeaweedFS through the
  real `ModelGateway` (endpoint approval, capability validation, cost reservation, settlement,
  three-turn ledger). E2E-21 mean `0.833333`; E2E-22 six retained deliveries, `infra_blocked` with
  two valid votes; reviewer override appends a result and preserves all votes; judge rows reject
  UPDATE/DELETE.
- `PCB_TEST_EVIDENCE_DIR=docs/implementation/evidence pytest tests/test_judging_postgres.py` — PASS:
  wrote `prompt-14-e2e-21.json` and `prompt-14-e2e-22.json` (secret-free; judge response text is
  never recorded).
- `pytest tests/test_judge_cli.py` — PASS: 10 tests, including adjudication permission enforced and
  `result` refused without restricted-evidence read.
- `pytest tests/test_judge_inputs.py` — PASS: 7 tests built from a real `EvaluationEvidence` manifest.
- All four suites together — **PASS: 83 tests**.
- `python -m polycodebench_orchestration.judge.cli calibration --packets <placeholder> --report
  docs/implementation/evidence/prompt-14-calibration.json --notes ...` — PASS with exit code 4
  (blocked): `status: blocked`, `exact_agreement_bp: null`, `promotion_target_met: null`,
  `disjointness: not_demonstrated`, missing inputs named.
- `ruff check` / `ruff format --check` on the 19 Prompt 14 files — PASS. Strict `mypy` on the 12
  new/changed source files — PASS (source-scoped, as every other prompt's command in this ledger is;
  including the test files surfaces environment errors of the same kind in the concurrently
  modified test files).
- `python scripts/check_judge_boundaries.py` — PASS. The repository-wide
  `scripts/check_boundaries.py` cannot run while the parallel session's plugin members lack
  boundary-map entries (`KeyError: 'lang_c'`).
- `python docs/implementation/verify_prompt00.py` — PASS: 14 REQ, 24 WP, 43 E2E, Prompts 00–34,
  142 PCB tickets with owners and evidence.

Not run, with reason: live judge-model evaluation, human calibration labelling/agreement and the
10% human audit, hosted CI, and any paid or remote action — no judge endpoint, credentials, price
snapshot, reviewer roster or labels exist in this workspace, and none were fabricated. Whole-suite
collection is blocked by the concurrent session's untracked
`tests/test_analyzer_contracts.py`; an earlier scoped run reported 666 passed / 50 failed with
every failure in concurrently modified Prompt 13 files.

E2E-21 evidence (`prompt-14-e2e-21.json`): votes scoring 1, 0.5 and 1 give exactly `0.833333`;
every delivery and vote retained; three distinct recorded seeds; no judge request contains a tool;
the ledger settles three turns and balances.

E2E-22 evidence (`prompt-14-e2e-22.json`): six deliveries retained with named reasons
(`not_json`, `missing_item`, `anchor_not_in_set`, `extra_instruction_action`); replacements carry
only the frozen `schema-repair-v1` instruction and name no score; `infra_blocked` with
`mean_score: null`; a comment-following vote retained and flagged `untrusted_comment_only`.

### 3. Acceptance gates

- **Satisfied:** PCB-14-1, PCB-14-2, PCB-14-3; E2E-21; E2E-22. Evidence: the three ticket entries
  in `tickets.md`, the two E2E rows in `e2e-matrix.md`, and the four files under
  `docs/implementation/evidence/`.
- **Pending:** none locally implementable. The judge's own behaviour beyond fixtures (live model
  output validity across the residual items) cannot be observed until an endpoint exists; the
  packet/vote/review/calibration contracts it will be measured against are implemented and tested.
- **Blocked:** PCB-14-4's human-evidence gate and T §15.3 calibration. Cause: no approved judge
  endpoint, credentials, price snapshot or judge model configuration; no registered
  `polycodebench_reviewer_v1` roster; no labels. Required input: (1) a judge endpoint whose model
  configuration is distinct from both pilot candidates; (2) a qualified reviewer roster; (3) at
  least 30 disjoint labelled packets per pilot language including adversarial comments and
  stylistic alternatives; (4) the seeded 10% human audit. Until then `judge-panel-v1` stays
  `effective_for_scoring: false`, `pcb-judge run` exits 4 with `JUDGE_PANEL_UNAVAILABLE`, and the
  calibration report says `blocked` with `null` metrics.

### 4. Decisions or specification discrepancies recorded

No specification discrepancy was found that required an amendment. D-14-01 … D-14-08 in
`decisions.md`: structural identity withholding plus a runtime proof; comment citations treated as
review evidence rather than schema errors; anchor *steps* (not distinct values) for disagreement;
replacements re-ask the same question; judge output capped by the resolved configuration; stored
records keep timestamps while digests stay content-only; content-derived local identities rather
than a shared helper; calibration `blocked` rather than `measured` without labels.

Before hand-off an independent adversarial reviewer attacked the new modules and reproduced its
findings by running the code (R-01 … R-09). Five were release blockers and four were material
defects; all nine are fixed, each with a regression test:

- **R-01** every stored result failed its own digest check on read-back, because `report_digest`
  took part in the canonical bytes its digest was computed over — `pcb-judge show`/`result` could
  not read a stored result. `report_digest` is now a canonical-excluded field, as `packet_id` is.
- **R-02** a reviewer's decision could rewrite another candidate's item score; decisions are now
  scoped to their own packet id, packet digest, rubric digest and panel digest.
- **R-03** supersession ran backwards, so the effective decision could be the replaced one;
  `supersedes_id` names the replaced decision and the lookup excludes that identity.
- **R-04** the response validator did not enforce the contract's maxima, so an over-long rationale
  or too many uncertainty flags raised an uncaught model error and would have lost the delivery;
  every bound is now a named rejection and the runner records an unanticipated violation as an
  invalid delivery with its reason.
- **R-05** replaying a panel half-wrote its ledger; a repeated logical delivery now records the
  same row so a resumed run completes.
- **R-06** the panel's seeded 10% audit sample was computed and discarded; it now decides review,
  deterministically from the panel seed and packet digest.
- **R-07** calibration disjointness was asserted and comparisons ignored packet identity;
  disjointness is checked against the scored digests (`not_demonstrated` without them,
  `blocked`/`violated` on overlap) and a comparison requires the result's own packet id and digest.
- **R-08** `*_bp` fields held percentages; they now hold integer basis points like every other
  `*_bp` field in the repository, and the promotion comparison no longer scales twice.
- **R-09** adjudication bound to the packet's rubric and panel, off-grid labels refused at import,
  repeated vote indexes counted once, mean range enforced, review queue limited to each packet's
  newest result, delivery artefact links preserved, a missing vote document is an error, and
  `run`/`result` require their own permissions.

**Residual limitation, not fixed:** candidate-controlled text is escaped before it reaches the
prompt, so a candidate can no longer close an `<evidence>` element or forge an item section, but
semantic prompt injection inside a comment remains possible in principle. The defences are the
escaping, the `untrusted_comment_only` trigger and the retained delivery; the claim is that a
hostile comment carries no authority and leaves evidence, not that it is harmless.

**Shared working tree:** a concurrent Prompt 13 session was active throughout. Its untracked
`tests/test_analyzer_contracts.py` imports a helper that no longer exists in its own `identity.py`,
blocking whole-suite collection until that session finishes; the repository boundary checker fails
on its new plugin members; its re-sync of the shared venv dropped the
`polycodebench_plugins_api` editable path, which is why the suites were run with
`PYTHONPATH=packages/plugins-api/src`. One repository-wide `ruff format` was run while scoping
arguments and reformatted several of those in-flight files (formatting only, idempotent) — this is
disclosed rather than hidden. Prompt 14's records were added to `progress.json` and `tickets.md`
without altering that session's later-prompt entries.

### 5. Exact next command or numbered prompt

Next: **Prompt 15 — Implement deterministic scoring and replay.** Scoring may be implemented
independently; the calibration dependency above remains recorded and unmet, and no judge-derived
item may be treated as scored until the T §15.3 gate passes. The owner authorises proceeding on
that basis. Re-entry for the blocked part: with an approved judge endpoint, judge model
configuration distinct from both pilot candidates, a qualified reviewer roster and 30+ disjoint
labelled packets per pilot language, run `pcb-judge calibration --labels <file> --results <file>
--scored-packets <file> --report <file>`.