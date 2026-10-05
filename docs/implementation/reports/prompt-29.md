# Prompt 29 - Public API and projections

Date: 2026-10-05  |  Phase 6  |  WP-21  |  E2E-25 / E2E-26 / E2E-28 / E2E-39

## Prompt 29 - PARTIAL

The release-backed public API and projection surface is implemented and exercised by the Prompt
30-32 public workflows. The public routes read published release projections; the public pages do
not query worker or held-out tables. The UI currently uses a maintained typed client. Generated
OpenAPI/TypeScript client parity and the full private-artifact/production IAM cases remain
unverified, so WP-21 and E2E-26 are not closed.

### Implemented surface

- Release listing/current-release metadata, leaderboard, model/language views, comparison,
  task browsing/detail, scorecards, evidence references and frozen methodology are backed by
  published release projections.
- Pagination, sorting, release-bound filters, same-release scorecards, common-task comparison
  scope, stable decimal serialization, cache/ETag behavior and typed incompatibility/missingness
  states are covered by API and projection tests.
- Public task content is bounded and lazy-loaded; source and diff content is rendered inertly.
  Evidence references are checked against released content and private references are redacted.
- Public and privileged route authorization, submission ownership, reviewer capabilities and
  release/privacy states have additional coverage in the later Prompt 30-32 API tests.
- Implementation paths include `packages/api/src/polycodebench_api/public_routes.py`,
  `packages/publication/src/polycodebench_publication/{projections.py,projections_query.py,releases.py}`,
  and `apps/web/src/lib/public-api.ts`.

### Verification and evidence

- Prompt 30 recorded 24 focused API/projection/release tests passing, four browser cases passing,
  and production build, typecheck and lint passing.
- Prompt 31 recorded 27 focused API/projection/release tests passing, three browser cases passing,
  and production build, typecheck and lint passing. Its later comparison-scope correction recorded
  23 focused API/projection tests and typecheck passing.
- Prompt 32 recorded 32 focused public API/projection tests, two submission/OIDC browser cases,
  and additional 18 OIDC/API tests passing. Its report explicitly notes that PostgreSQL integration
  was not run because `PCB_TEST_DATABASE_URL` was unset.
- Evidence and scope limits are in `docs/implementation/reports/prompt-30.md`,
  `prompt-31.md`, `prompt-32.md`, and their `docs/implementation/evidence/prompt-30/` and
  `prompt-31/` artifacts. Those are saved run records, not fresh reruns in this report.

### Acceptance and remaining gates

- **PCB-29-1:** Partial-to-implemented public route coverage through the current release
  projection layer; the implementation is exercised by downstream page and route tests.
- **PCB-29-2:** Partial. Public pagination/filter/comparison contracts are exercised, but a
  complete standalone E2E-28 cohort matrix is not evidenced as a fresh Prompt 29 run.
- **PCB-29-3:** Partial. Authorization, privacy and ownership have local API coverage; deployed
  artifact-bucket/IAM denial and PostgreSQL-backed integrations remain unverified.
- **PCB-29-4:** Partial. A typed browser client and API schemas exist, but generated-client/server
  parity is not independently verified. Release fixtures are explicitly synthetic internal data.
- E2E-25/39 public flows have later local evidence. E2E-26 remains partial for binary artifacts
  and production IAM policy. E2E-28 full matrix and generated-client parity remain open. No live
  benchmark result is claimed.

### Next

Generate and validate API clients against the current OpenAPI schema, complete the public privacy
and artifact IAM variants, and rerun the affected aggregate API/browser gates with the required
PostgreSQL and deployment inputs.
