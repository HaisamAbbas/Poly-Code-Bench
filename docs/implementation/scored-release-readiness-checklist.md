# Scored-release readiness checklist

Audit refresh: 2026-10-07.

## Local product and synthetic evidence

- [x] Release-backed API and public workflows run locally against PostgreSQL and serve explicitly labeled synthetic/internal release projections.
- [x] Generic OIDC browser sign-in, metadata-only submission creation, reviewer authorization, owner isolation and bounded approval mechanics have local evidence.
- [x] Comparison, task browsing, score/evidence drilldowns, methodology, correction/withdrawal and export paths are present and release scoped.
- [x] Local API readiness and the leaderboard returned HTTP 200 in the Prompt 34 refresh.
- [x] Local solve-worker assembly and exact job/run filters are verified; shared dispatch remains disabled.

## Scored exploratory pilot

- [ ] Actual 144-attempt pilot: 144 planned, 0 dispatched, 0 completed, 0 model failures. See `evidence/prompt-17-preflight.json`.
- [ ] Approved model configurations, provider endpoints, price snapshots and owner-authorized hard spend limit.
- [ ] Distinct configured judge endpoint and required qualified-human calibration labels.
- [ ] Task rights/provenance confirmation, quality admission and curator freeze for the pilot cohort.
- [ ] Production worker and isolated execution identity/target.
- [ ] Durable evaluation, judging, scoring and publication workers connected to the approved run lifecycle.
- [ ] Live-pilot replay and complete source-to-score provenance.

## Public ranked release

- [ ] Scoring remains inactive: `config/scoring/pilot-v1.yaml` has `effective_for_scoring: false` and `calibration_status: pending`.
- [ ] All required language/family paths and TypeScript task-backed execution are not complete.
- [ ] Official-source rights, native/adapted scope, held-out policy and public disclosure have not been cleared for a scored cohort.
- [ ] Production object-store IAM, key management, deployed identity denial, public DNS/HTTPS and staging deployment are unverified.
- [ ] Public release review and publication to an authorized target have not occurred.

## Release decision

The local site is ready for owner review as a synthetic/internal product preview. It is not ready for a scored exploratory pilot, public ranking, or publication. Website completeness, image availability, fixture tests and synthetic releases do not satisfy those gates.
