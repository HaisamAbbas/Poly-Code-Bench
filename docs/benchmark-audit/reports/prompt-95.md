# Prompt 95 / BWP-13 - Firewall admission and independently validated replacements

## Implemented functionality and changed files

- Added versioned firewall policy, scope, replacement-source metadata, bounded replacement plan, task validation and derived benchmark manifest contracts in `packages/core/src/polycodebench_core/benchmark_audit_documents.py`. The pure reducer in `benchmark_firewall.py` admits only complete finite scope plus accepted validity/rights, validated risk, required temporal status and an independent reviewer. Prohibited overlap, invalid validity and denied rights reject; unresolved evidence remains review. A no-hit scope or novelty claim cannot bypass those gates.
- Added replacement reviewer authorization checks in `packages/services/src/polycodebench_services/benchmark_firewall.py`. Persistence in `packages/persistence/src/polycodebench_persistence/benchmark_audit.py` validates exact query coverage and unresolved candidates, approved task source IDs, per-plan total/per-source draft quotas under a locked plan row, preregistration, trusted worker report/runtime/package/task digests, private test/oracle artifacts, task-specific exposure-scope binding, source-family ancestry, task-bound risk/temporal records and recomputed decision successors. Derived manifests preserve official imports, account for every official item, compute family/split/competency/difficulty distributions from entries, bind a private sampling policy, and prevent a family crossing splits across manifests for the same registry/version.
- Extended `packages/persistence/src/polycodebench_persistence/models.py` and added guarded migration `d4f7b2a196c3_firewall_admission_and_replacements.py`. Added canonical vectors and tests in `tests/fixtures/contracts/benchmark-audit-vectors.json`, `tests/test_benchmark_firewall.py`, `tests/test_benchmark_audit_documents.py` and `tests/test_benchmark_audit_controls.py`. Updated `TASKS.md`, acceptance and implementation ledgers, decisions, commands and BA4 reporting.

## Tests/commands actually run and results

- Focused firewall, audit-document and persistence-control tests: **34 passed** using synthetic fixtures.
- Ruff check passed; Ruff format check passed (9 files already formatted). Strict Mypy passed with no issues in 8 source files.
- Combined Prompt85-95 regression passed: **173 passed**. `git diff --check` passed.
- `corepack pnpm --filter @polycodebench/contracts test:contracts` passed: TypeScript canonical v1/v2 envelopes and 256 property cases agree with the shared vectors.
- `uv lock --check --offline` passed (138 packages); `uv run alembic -c packages/persistence/alembic.ini heads` reported `d4f7b2a196c3` as the sole head. Offline PostgreSQL migration upgrade from `a194d6c3e781` and guarded downgrade back to it both rendered successfully using a placeholder URL; neither connected to or modified a database.
- No live source, model, worker or database operation occurred.

## Acceptance gates satisfied, pending and blocked

- **Partial - BX-34 / BAT-13-A:** finite-scope decisions enforce validity, rights, risk, temporal and independent-review gates; live authorized corpus and reviewer evidence is absent.
- **Partial - BX-35 / BAT-13-B:** source-family approval, total/per-source draft quotas, frozen round/cost/time caps, private oracle artifacts and distinct author/checker/reviewer identities are represented and validated; production worker and authorized human identity evidence are absent.
- **Partial - BX-36 / BAT-13-C:** transformed ancestry remains in its family; independent prospective tasks require accepted template review; cross-split family reuse is rejected. No approved generation/exposure pipeline or reviewed live lineage exists.
- **Partial - BX-37 / BAT-13-D:** official snapshots remain immutable; derived manifests map dispositions, validation/oracle evidence, competency/difficulty and computed distributions, membership digest and separate non-comparable metric labels. Production distribution review, imported benchmark bytes and derived publication projection are unavailable.

## Decisions or specification discrepancies recorded

- No zero-hit or AI novelty result can admit a task. Incomplete, unresolved, failed, truncated or unsupported scope stays in review; prohibited overlap, invalid validity and denied rights reject.
- Replacement plans freeze approved source metadata, private configs, difficulty/exposure policy, quotas and maximum drafts/rounds/cost/wall time. The author, checker and final reviewer must be distinct. Prospective independence requires actual template/lineage review.
- Original benchmark membership is never rewritten. Derived metrics use distinct labels and carry `separate_labels_no_automatic_comparison`; source-family variants cannot cross official splits.
- Implementation remains partial because production admission evidence, authorization/role verification, source rights review, trusted worker execution, live PostgreSQL and an approved source corpus are not configured. Fixture contracts do not establish a live finding.

## Exact next command or numbered prompt

Proceed in order to **Prompt 96 / BWP-14 - Continuous monitoring, risk changes and owner alerts**.
