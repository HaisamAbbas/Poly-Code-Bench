# Repository audit fixes

Started 2026-10-05 after the cross-prompt audit. Fix items in this order, verify each focused change, preserve unrelated worktree changes, and report external blockers separately.

## Status

- [x] Track A reviewer attribution: untrusted accepted edges and adjudications now fail closed; exact duplicate suppression is derived from the submitted findings; review approvals bind to an immutable evaluation context. A trusted orchestration verifier is required and the CLI deliberately has none. Focused tests and source checks pass.
- [x] Public projection scope and evidence links: language pages now use only declared language-profile dimensions; a profile is exposed only when its model/language-matched scorecard URL validates at release load. Legacy profiles without that link are suppressed. The separate leaderboard language filter remains a configuration filter whose metrics/coverage are explicitly documented as release-wide.
- [ ] Release withdrawal cache policy: mutable withdrawal/successor fields are served with a one-year immutable cache lifetime.
- [ ] PostgreSQL submission rate limit: concurrent requests can race between count and insert.
- [ ] Publisher typed state mapping: non-ready states collapse to `infrastructure_missing`.
- [ ] Prompt 28 admission closure: Q&A and prediction checks treat path strings as evidence without validating the files or admission records.
- [ ] Comparison query scope: filtered task pairs coexist with release-wide summary/deltas; document or separate these API values so consumers cannot confuse cohorts.
- [ ] Strict publication validation: inspect the dirty change to `PublicationModel` and restore strict scalar validation while retaining JSON collection compatibility.
- [ ] Prompt 09 budget consistency: compare the run budget profile with the embedded protocol budget.
- [ ] Submission identity: the public request page still relies on a pasted bearer token rather than an established sign-in flow.
- [ ] Implementation ledgers: reconcile phase/prompt statuses, missing Prompt 19/20/29 reports, contradictory language coverage, stale tickets/E2E references, and claims unsupported by fresh runs.
- [ ] Fresh aggregate verification: rerun the affected E2E, database, build and deployment gates after code fixes; current audit verification was static/focused only.

## External blockers to retain

- Live benchmark authorization and reviewer/human calibration are absent; synthetic fixtures must not be represented as benchmark results.
- Phase 7 cloud account, spend authorization and PostgreSQL deployment inputs are absent. Do not provision resources or run live spending without them.
- The Prompt 05 source artifact `Pasted markdown(5).md` is missing from the workspace.
- Phase 8 final integrated deployment gate has no recorded completion.
