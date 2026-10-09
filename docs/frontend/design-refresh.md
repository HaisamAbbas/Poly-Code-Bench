# Frontend design refresh — 2026-10-09

## Audit before editing

The application uses Next.js 16.3.7, React 19, Tailwind's CSS import, and a shared global stylesheet. `apps/web/src/app/globals.css` defines ten original palette variables plus many hardcoded shades. Arial/Helvetica is the existing font. Spacing, type, border, radius and motion tokens are not centralized. There is no dark-mode implementation, modal/toast library or external icon system.

Problems ranked by visual impact:

1. Small text throughout comparison, task/evidence, methodology and submission modules: many values below 12px, dense labels, inconsistent weights and line heights.
2. The header combines eight navigation links, a brand and a caption in one row, has no active-route indication, and becomes crowded at tablet widths. There is no skip-to-content link.
3. Similar cards, form controls, status labels and buttons use different spacing, colors, radii and borders. Some missing/review states use inconsistent semantic colors between components.
4. The layout feels flat and cramped; irregular padding and compact grids obscure the page/section hierarchy. Form controls and secondary actions need coherent emphasis.
5. Loading states are bare text, disabled/loading control styles are incomplete, and several navigation links, disclosure summaries and buttons have targets smaller than 40px. Three different amber focus colors compete.

Existing strengths to preserve: native labels and forms, named table scroll regions, inert public evidence, explicit privacy/missingness qualifications, immutable source links, release-aware URLs, clear loading/error/blocked states, and reduced-motion support.

## Scope and implementation plan

Apply the refresh to every existing module: leaderboard, model profiles, language profiles, comparison, task index/details, scorecards, methodology/corrections, submissions, audit lookup/reports, curator access, and attestation lookup/verification. Preserve routes, API calls, data flow, existing content, and public component props/events.

1. Introduce semantic palette, type, spacing, radius, shadow and motion tokens. Every color derives from an existing palette seed; retain Arial/Helvetica.
2. Refactor shared styles, navigation, controls, cards, tables, states and evidence surfaces. Add presentation-only active navigation, mobile disclosure, skip link and skeletons.
3. Review all route templates at 360px, 768px and 1440px with existing synthetic data. Validate contrast, keyboard/focus, contained table scrolling and touch targets.
4. Run the production build, lint, typecheck, existing browser suites and the new route/responsiveness review. Record actual results and remaining recommendations here.

## Implemented foundation and components

- `apps/web/src/app/tokens.css` centralizes the original 13 palette seeds, semantic primary/surface/border/text/success/warning/danger/info colors, derived tints and aliases for existing variable names. `globals.css` uses these tokens throughout; no hardcoded colors remain there. Arial/Helvetica and the existing Georgia brand mark are retained.
- Typography uses 12/14/16/18/20/24/30/36px sizes and 400/500/600 weights. Spacing follows a 4px scale, radii are 4px and 8px, shadows are two restrained surface/elevation options, and interaction transitions are 160ms ease-out. Reduced motion disables transitions and skeleton animation.
- The shared header now has active-route styling, a mobile navigation disclosure, keyboard Escape dismissal with focus restoration, and a skip-to-content link. Existing destinations and navigation labels are retained.
- Buttons have primary, secondary, ghost and destructive styles, consistent sizes, hover/active/focus/disabled/loading treatment. Forms use 44px controls, regular-weight input text, readable labels/helpers, readonly states and linked error descriptions.
- Cards, notices, status labels, profile panels, evidence blocks and page spacing share the foundation. Review is blue, missing/insufficient information amber, gated failure red, and measured/approved states green, with their existing text labels retained.
- All eight table templates use the existing `KeyboardScrollRegion` interface. It measures available width and presents rows as labelled cards when the matrix cannot fit. Sorting controls, source links, row/column order, native semantics and every value remain available. Long arithmetic/evidence fields span the mobile card. Radar charts fit their panels, and code/diff text wraps without changing its bytes.
- Shared skeletons augment existing loading messages. Empty states link to published releases, and unavailable audit/attestation pages now have a page-level heading. No dependencies were added.

Important implementation paths are `app/tokens.css`, `app/globals.css`, `app/layout.tsx`, `components/app-navigation.tsx`, `components/content-skeleton.tsx`, `components/public-ui.tsx`, `components/keyboard-scroll-region.tsx`, `components/profile-charts.tsx`, the comparison/scorecard/methodology table wrappers, the existing forms/evidence/state components, and route loading files. Browser review infrastructure lives under `apps/web/tests/e2e` and `playwright.design-review.config.ts`.

## UI behavior and interface review

The API, auth, data-loading modules, routes, scoring and payload construction are unchanged. An AST comparison verified the parameter signatures of **35 existing exported functions**. The following presentation interactions were deliberately touched:

- New mobile menu state, active navigation and Escape/focus handling improve navigation at narrow widths.
- Width measurement and derived header labels enable responsive table cards. The helper reads existing DOM headers and changes presentation attributes; it does not recompute metrics or fetch data.
- Evidence-fragment reveal now scrolls immediately. This resolves a reproduced race between smooth scrolling and navigation to a link elsewhere on the page.
- The attestation lookup associates its existing helper/error text with the input and shows the existing error message for nonempty invalid queries. UUID validation and valid-query redirects are unchanged.
- Skip-link focus, skeletons, empty-state links, heading levels and `aria-busy`/`aria-invalid` attributes complete the existing states. Public props/events were preserved.

## Tests, screenshots and results

| Check | Actual result |
|---|---|
| Production `build`, standalone `typecheck`, ESLint | Passed |
| Generated API types check | Passed |
| Shared contract suite | Passed, including 256 property cases |
| Existing browser suites: Prompts30/31/32/99/100 | 4 + 3 + 2 + 4 + 3 = **16 passed** |
| New design review | **4 passed**: three full route matrices plus keyboard/focus/reduced-motion loading |
| Rendered review | **66 views**: 21 route/state variants × 360/768/1440px, plus the signed-in submission form at all three widths |
| Text contrast and control targets | No measured failures; normal text ≥4.5:1, large text ≥3:1, checked targets ≥40px |
| Page, chart and table overflow | No failures; 36 table instances inspected across the three route matrices |
| Palette and source/interface audit | All 13 seeds match original colors; no new colors, undefined tokens or API/auth/data-module changes; 35 existing signatures preserved |

The route matrix covers all **15 existing page templates**, the `/` redirect, code and answer-only profiles, compatible/incompatible comparisons, opened task evidence, error/unknown views, curator boundary, public health, valid/revoked attestations and invalid lookup. Full screenshots and 22 review plates are available under `.cache/frontend-design-review/`; the evidence manifest is [design-refresh-evidence.json](design-refresh-evidence.json). Every plate was visually inspected at all three widths, with the narrow chart/card details also inspected at original resolution.

Actual commands:

```text
corepack pnpm --filter @polycodebench/web build
corepack pnpm --filter @polycodebench/web typecheck
corepack pnpm --filter @polycodebench/web lint
corepack pnpm --filter @polycodebench/web api:types:check
corepack pnpm --filter @polycodebench/contracts test:contracts
corepack pnpm --filter @polycodebench/web test:e2e
corepack pnpm --filter @polycodebench/web test:e2e:prompt31
corepack pnpm --filter @polycodebench/web test:e2e:prompt32
corepack pnpm --filter @polycodebench/web test:e2e:prompt99
corepack pnpm --filter @polycodebench/web test:e2e:prompt100
corepack pnpm --filter @polycodebench/web exec playwright test --config=playwright.design-review.config.ts
uv run --locked ruff check apps/web/tests/e2e/launch-design-review-api.py
uv run --locked ruff format --check apps/web/tests/e2e/launch-design-review-api.py
```

Existing browser artifacts were redirected with `PCB_BROWSER_ARTIFACT_ROOT` into `.cache/frontend-existing-e2e` to preserve historical evidence. Test-generated TypeScript configuration changes were restored from the user's original bytes before the final build/typecheck. Earlier failures found missing audit-page headings, the evidence scrolling race, an omitted methodology table wrapper and a test locator that also matched Next's route announcer; these were corrected and relevant checks rerun. An attestation run overlapping a hot reload passed when rerun with source files frozen. The final results above are the successful runs.

## Recommendations deliberately deferred

- A searchable configuration picker could improve large model selections. The current native multi-select remains usable and preserves the existing interaction contract; a new picker warrants separate interaction design and testing.
- Additional Firefox/Safari and NVDA/VoiceOver review would strengthen coverage. This run used the installed Chromium browser suite, automated contrast/geometry inspection and keyboard testing.
- Connecting curator review/import/monitor/correction controls requires authenticated workflow APIs and authorization work. Those business/data-flow changes exceed this visual refresh's hard constraints; the existing access boundary and its content are preserved.
- A new font, dark mode, modal/toast system or icon dependency was not introduced. The existing font is set, the app is light-only, and the current screens do not contain those additional surfaces.
