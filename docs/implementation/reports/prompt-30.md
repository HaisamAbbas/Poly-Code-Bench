Prompt 30 / Phase 6 — DONE

1. Implemented functionality and changed files
   - Built the shared responsive shell and first three public pages: release-backed leaderboard,
     language leaderboard/diagnostics, and model profile. URL state retains release, language,
     metric sort and direction. Typed API states and accessible tables/chart labels preserve
     measured, gated-zero, N/A, missing, pending-review, empty and error states. Horizontally
     scrollable tables and charts are keyboard focusable.
   - The API release's metric registry supplies labels/domains/directions, and its entries supply
     available languages. Language diagnostics remain per configuration. The model radar plots
     published code dimensions only; the language heatmap labels absent measurements “Not tested.”
     Cost, latency, coverage, metric and opportunity values link to same-release scorecards.
   - Added canonical API app setup and development release fixture lifecycle, repaired the API's
     projection-registry imports, added public release listing and current-release metadata, and
     extended projections for missing/review states and page-specific release data.
   - Main paths: `apps/web/src/app/{leaderboard,languages,models}/`,
     `apps/web/src/components/`, `apps/web/src/lib/public-api.ts`, `packages/api/src/polycodebench_api/`,
     `packages/publication/src/polycodebench_publication/{projections.py,projections_query.py,releases.py}`,
     `tests/test_public_api_prompt30.py`, and Playwright coverage in `apps/web/tests/e2e/`.

2. Tests/commands actually run and their results
   - `npm exec --yes --package=node@24 --package=pnpm@12.5.1 -- pnpm --filter @polycodebench/web test:e2e`:
     PASS, 4 browser cases, 0 skipped; Node 24.21.0, Chromium. JSON and screenshots:
     `docs/implementation/evidence/prompt-30/`.
   - `npm exec --yes --package=node@24 --package=pnpm@12.5.1 -- pnpm --filter @polycodebench/web build`:
     PASS, optimized Next.js production build and route type validation.
   - Matching `typecheck` and `lint` commands: PASS.
   - `uv run --locked --group dev pytest -q tests/test_public_api_prompt30.py tests/test_public_api_projections.py tests/test_publication_releases.py -p no:cacheprovider`:
     PASS, 24 tests. Covers published release index/current pointer, leaderboard registry,
     same-release scorecard, JavaScript-only dimensions/tool coverage, answer-only omission and
     unknown-language denial.
   - Scoped Ruff check and format check for changed API/publication modules and the new route test:
     PASS. `git diff --check`: PASS.
   - Required but not run as a complete scenario: full E2E-39 comparison → task → contribution
     journey; full E2E-40 across all seven pages and large-task-list/load bounds. Those pages and
     cases are Prompt 31/32 scope and remain explicitly pending.

3. Acceptance gates
   - Satisfied: PCB-30-1 through PCB-30-4; Prompt 30 browser subcases of E2E-39/40. Evidence:
     `docs/implementation/evidence/prompt-30/browser-results.json` and the six screenshots beside
     it. The test release is `synthetic_internal` and the pages label it as synthetic, not live.
   - Pending: E2E-39 and E2E-40 remain PARTIAL overall until Prompt 31/32 add the remaining routes,
     complete navigation chain, and large-list/load cases.
   - Blocked: None for Prompt 30.

4. Decisions or specification discrepancies recorded
   - D-30-01: release projections remain the source of metric definitions, language choices and
     dimension applicability; no frontend scoring formula or language registry was introduced.
     No specification discrepancy was required.

5. Exact next command or numbered prompt
   - Next: Prompt 31 — Build comparison, task explorer and methodology pages.
