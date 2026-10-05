# Prompt 31 — DONE

## Blocked on me

None. The model-submission page and approval flow remain Prompt 32 scope as requested.

## Changed

- Built release-pinned comparison, task explorer, task detail, scorecard evidence and frozen
  methodology routes. Shared navigation links these views; filters, model IDs and release identity
  stay in the URL.
- Comparison accepts two to four distinct configurations and uses only exact task ID/version
  intersections with public scorecards. Missing or differing protocol/budget identities return
  typed incompatibilities and no score rows. Paired values, source scorecard IDs and interval
  endpoint differences come from the API. The UI does not calculate or reweight a score.
- Added 50-row task pagination and a same-origin route that validates release/task identities,
  bounds the response size and fetches content only when opened. Source and diff strings render as
  inert text. A curated JSON export contains only task content released in that release.
- Added scorecard metric states and item drilldowns with exact decimal strings, effective weights,
  recorded arithmetic and formula links. Evidence references are allowlisted against the same
  disclosed task's released source/patch/finding IDs; private references are counted but not named.
- Pinned methodology lookups to the requested release/version and exposed frozen methods,
  formulas, applicability, native/adapted labels, limitations, corrections, withdrawal notices
  and successor links. Historical pages retain their original release identity.
- Extended the local fixture with compatible and incompatible configurations, public sources,
  patches/findings, missingness states, a withdrawn predecessor and 64 task-list rows. The fixture
  is labeled `synthetic_internal`; its numbers/intervals are display fixtures, not live results.

Main files: `apps/web/src/app/{compare,tasks,scorecards,methodology}/`,
`apps/web/src/app/api/public/tasks/[taskId]/content/route.ts`,
`apps/web/src/components/public-evidence.tsx`,
`packages/publication/src/polycodebench_publication/{projections.py,projections_query.py,releases.py}`,
`packages/api/src/polycodebench_api/{public_routes.py,dev_fixture.py}`, and
`tests/test_public_api_prompt31.py`.

## Found

- `npm exec --yes --package=node@24 --package=pnpm@12.5.1 -- pnpm --filter @polycodebench/web test:e2e:prompt31` — PASS, 3 browser cases. Covers E2E-39 navigation and API/UI scalar equality, E2E-26 public task/scorecard privacy and JSON export probes, and E2E-40 keyboard, lazy payload, viewport and 50 + 14 row pagination cases. Artifacts: `docs/implementation/evidence/prompt-31/browser-results.json` and six screenshots beside it.
- Prompt 30 browser regression — PASS, 4/4 cases. Covers release/filter state, existing language/model pages, keyboard selection and 375px/1440px layouts.
- Focused API/projection/release tests — PASS, 27 tests: `tests/test_public_api_prompt30.py`, `tests/test_public_api_prompt31.py`, `tests/test_public_api_projections.py`, and `tests/test_publication_releases.py`.
- Next.js production build — PASS; route output includes comparison, tasks, task detail, scorecards, methodology and the bounded content handler. Typecheck and lint — PASS.
- Scoped Ruff check and format check — PASS. `docs/implementation/progress.json` parses; `git diff --check` — PASS.
- E2E-39's public analysis journey passes. E2E-26 remains PARTIAL because binary artifact-download routes and production IAM/bucket-policy denial are outside these public task exports. E2E-40 remains PARTIAL until Prompt 32's model-submission page variants are implemented. No live benchmark result is claimed.
- D-31-01: protocol and budget must be explicitly present and identical; shared coverage means matching disclosed task versions and source scorecards, never a minimum coverage count.
- D-31-02: comparison intervals are labeled as differences of published interval endpoints; they are not called paired bootstrap confidence intervals. Release aggregate metrics remain source values and are not recalculated for task filters.
- D-31-03: public task exports and evidence links resolve only to curated release content; source and diff payloads stay inert and are fetched on demand.

Next: **Prompt 32 — Implement reviewed model submissions and close the public product phase.**

## Audit correction (2026-10-05)

`GET /compare` now returns `release_metric_scope`, `task_pair_scope`, and the exact
`applied_filters`. Entry metrics and aggregate deltas are explicitly labeled as full-release
aggregates; common task references and per-task deltas are labeled as the filtered common-task
intersection. The comparison page renders those API scope values and filter selections.

Verification: 23 focused API/projection tests passed, Ruff check/format passed, and the web TypeScript
check passed using the installed `tsc`. The module-level mypy check still reports eight pre-existing
errors elsewhere in these projection modules (one untyped-call and seven optional-value/Literal
errors); none points to the added scope fields.
