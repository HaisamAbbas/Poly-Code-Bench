# Prompt 99 / BWP-17 — Benchmark health dashboard and evidence journeys

## Implemented functionality and changed files

- Added a public report-ID lookup and server-rendered aggregate report view. It displays the benchmark/version, source window, selected and assessed counts, scope and missingness states, observed-risk tier counts, and recorded limitations. The report explicitly avoids a clean verdict, probability claim, or model-specific eligibility inference.
- Kept the public UI on the API's strict allowlisted `PublicHealthView` projection. It does not render raw audit documents, task text, answers, fingerprints, vectors, or sealed evidence. Public report requests remain `no-store`.
- Added client-side keyboard-accessible report lookup with an announced pending state. The report page also has a loading fallback, safe not-found/unavailable states, and mobile layouts.
- Added a curator route that explains the current authorization boundary and makes no private API call. The current web identity cannot provide tenant and curator claims; transition controls remain unavailable until the shared ACL and authorized service writers exist.
- Added a synthetic API fixture and isolated Playwright config/tests. The fixture includes one partial aggregate and one intentionally invalid projection carrying a private sentinel; the API rejects the invalid projection without returning the sentinel.
- Changed files: `apps/web/package.json`, `apps/web/src/app/globals.css`, `apps/web/src/app/audit-reports/page.tsx`, `apps/web/src/app/audit-reports/[reportId]/page.tsx`, `apps/web/src/app/audit-reports/[reportId]/loading.tsx`, `apps/web/src/app/benchmark-audit/page.tsx`, `apps/web/src/components/audit-report-lookup-form.tsx`, `apps/web/src/components/benchmark-audit.tsx`, `apps/web/src/components/public-ui.tsx`, `apps/web/src/lib/benchmark-audit.ts`, `apps/web/src/lib/public-api.ts`, `apps/web/playwright.prompt99.config.ts`, `apps/web/tests/e2e/prompt99.spec.ts`, and `apps/web/tests/e2e/launch-prompt99-api.py`.

## Tests/commands actually run and results

- `corepack pnpm --filter @polycodebench/web typecheck` — passed (`tsc --noEmit`).
- `corepack pnpm --filter @polycodebench/web lint` — passed (ESLint).
- `corepack pnpm --filter @polycodebench/web test:e2e:prompt99` — passed, 4 browser scenarios: keyboard report lookup and mobile fit; delayed-response loading announcement; invalid projection fails closed without private-field disclosure; curator blocker sends no private API request.
- `corepack pnpm --filter @polycodebench/web build` — passed; report lookup/detail and curator routes appear in the production route manifest.
- `uv run --locked ruff check apps/web/tests/e2e/launch-prompt99-api.py` and `uv run --locked ruff format --check apps/web/tests/e2e/launch-prompt99-api.py` — passed.
- All API and browser data in these checks is synthetic. No live database, source, reviewer, model, signer, or production authorization operation occurred.

## Acceptance gates satisfied, pending and blocked

- **Partial — BX-46 / BAT-17-B:** the public aggregate report renders version, source window, scope, missingness, risk distribution, and limitations. Live reviewed projection, model context, prior-exposure evidence, temporal qualification, trend breaks, corrections, and derived-set views are unavailable.
- **Partial — BX-47 / BAT-17-D:** synthetic browser checks cover keyboard, mobile, loading, partial report, not-found/privacy filtering, and blocked curator access. Production browser/access/log review and revoked-report behavior are not evidenced. The public projection contract has no revocation state; Prompt 100 owns attestation expiry/revocation.
- **Partial — BAT-17-A:** the curator page fails closed without requesting private data. Source/cost planning and firewall, review, sealing, replacement, and monitoring actions are not exposed because authorized transition services and shared reviewer ACL are absent.
- **Partial — BAT-17-C:** the page names unavailable trends, corrections, derived views, and attestations. Those evidence journeys are not backed by the current projection or lifecycle APIs.
- BA5 remains partial. Synthetic browser fixtures do not substitute for approved source snapshots, independent review, shared curator authorization, or live database evidence.

## Decisions or specification discrepancies recorded

- `ADDENDUM-DECISION-44` records that the public UI consumes only the no-store allowlisted aggregate and that curator controls remain absent until a reviewed shared ACL and authorized transitions are connected.
- No new specification discrepancy was found. The earlier §1 source-list discrepancy remains: the specification says five source Markdown files were read, but lists and hashes three.

## Exact next command or numbered prompt

Prompt 100 / BWP-18 — signed audit attestations and public verification. Implement canonical attestation payloads, signing/verification and expiry/revocation/correction lifecycle; keep signing and publication blocked until reviewed signer, key-custody, and approval adapters exist.
