# Repository audit fixes

Started 2026-10-05 after the cross-prompt audit. Fix items in this order, verify each focused change, preserve unrelated worktree changes, and report external blockers separately.

## Status

- [x] Track A reviewer attribution: untrusted accepted edges and adjudications now fail closed; exact duplicate suppression is derived from the submitted findings; review approvals bind to an immutable evaluation context. A trusted orchestration verifier is required and the CLI deliberately has none. Focused tests and source checks pass.
- [x] Public projection scope and evidence links: language pages now use only declared language-profile dimensions; a profile is exposed only when its model/language-matched scorecard URL validates at release load. Legacy profiles without that link are suppressed. The separate leaderboard language filter remains a configuration filter whose metrics/coverage are explicitly documented as release-wide.
- [x] Release withdrawal cache policy: release lists and per-release state/withdrawal notices now require revalidation; immutable methodology/content routes retain long caching. ETag changes and 304 revalidation are covered across withdrawal.
- [x] PostgreSQL submission rate limit: the count-and-insert section now holds a stable subject-scoped transaction advisory lock and uses the post-lock database timestamp; a concurrent PostgreSQL integration test is added.
- [x] Publisher typed state mapping: pending, evaluating, needs-review, infrastructure-blocked, quarantined, cancelled, and not-applicable rows retain distinct statuses/reasons and block score coverage; only finalized pass/failure attempts count as observed. Ready not-applicable gates do not become passes. Focused scorecard-to-aggregate tests pass.
- [x] Prompt 28 admission closure: the seven-family audit now resolves repository-contained pack/evidence paths, validates task-package family and fixture files, and checks family-specific evidence contents. `repo_qa` is bound to the actual Prompt 27 pack and E2E-38 artifact. The refreshed audit passes 7/7; SWE-bench suite 44 passed, 1 image test skipped (opt-in).
- [x] Comparison query scope: `GET /compare` now labels release aggregate metrics/deltas separately from the common-task intersection, returns the applied language/family/difficulty filters, and the page renders that API scope. Focused API/projection tests pass.
- [x] Strict publication validation: `PublicationModel` retains strict scalar validation and converts JSON arrays only for tuple-annotated fields, including nested tuple collections. A release-content JSON round trip and scalar rejection test pass; publication aggregation and public projection tests pass.
- [x] Prompt 09 budget consistency: the solve loader requires an installed run profile whose full budget exactly matches the protocol; all four configured protocols have named profiles. Missing/mismatched-profile regressions pass. Database-backed loader cases remain skipped without `PCB_TEST_DATABASE_URL`.
- [ ] Submission identity: the public request page still relies on a pasted bearer token rather than an established sign-in flow.
- [ ] Implementation ledgers: reconcile phase/prompt statuses, missing Prompt 19/20/29 reports, contradictory language coverage, stale tickets/E2E references, and claims unsupported by fresh runs.
- [ ] Fresh aggregate verification: rerun the affected E2E, database, build and deployment gates after code fixes; current audit verification was static/focused only.

## External blockers to retain

- Live benchmark authorization and reviewer/human calibration are absent; synthetic fixtures must not be represented as benchmark results.
- Phase 7 cloud account, spend authorization and PostgreSQL deployment inputs are absent. Do not provision resources or run live spending without them.
- The Prompt 05 source artifact `Pasted markdown(5).md` is missing from the workspace.
- `PCB_TEST_DATABASE_URL` is not configured, so the new PostgreSQL concurrency integration test is currently skipped.
- Phase 8 final integrated deployment gate has no recorded completion.
