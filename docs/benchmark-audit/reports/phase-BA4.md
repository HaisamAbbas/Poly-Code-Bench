# Phase BA4 — Task lifecycle

## Implemented functionality and changed files

Prompt95 added finite-scope firewall decisions, bounded replacement plans, review and lineage requirements, and immutable derived-manifest contracts. Prompt96 added versioned monitor policies, serialized source/query/storage/cost reservations, bounded retry and catch-up, and deduplicated in-app alerts. Prompt97 added denominator-first health metrics, missingness disclosure, family-aware prevalence, detector-quality intervals, and trend discontinuity checks. Detailed implementation and evidence are in [Prompt95](prompt-95.md), [Prompt96](prompt-96.md), and [Prompt97](prompt-97.md).

## Tests/commands actually run and results

- Prompt95 focused firewall/document/persistence tests: **34 passed**; combined Prompts85–95 regression: **173 passed**. Ruff, strict Mypy, TypeScript contract properties, sole migration-head check and offline migration upgrade/downgrade rendering passed.
- Prompt96 focused schedule/document/persistence tests: **34 passed**; combined Prompts85–96 regression: **184 passed**. Ruff, strict Mypy, TypeScript contracts, sole migration-head check and offline migration upgrade/downgrade rendering passed.
- Prompt97 focused health tests: **10 passed**; combined Prompts85–97 regression: **194 passed**. Ruff, strict Mypy, TypeScript contracts, sole migration-head check and offline migration upgrade/downgrade rendering passed.
- All tests used local or synthetic inputs. Migrations were rendered offline; no database migration, live source query, model call, or monitoring dispatch occurred. See [commands.md](../commands.md) and the three prompt reports for exact invocations.

## Acceptance gates satisfied, pending and blocked

- **Partial — BX-34–37 / BWP-13:** firewall, replacement, ancestry, and derived-manifest contracts are tested; approved source rights, production worker authority, independent human review, live imports, and database execution remain unavailable.
- **Partial — BX-38–40 / BWP-14:** bounded scheduling, quota reservations, retry rules, and in-app alert dedupe are implemented; production scheduler/source integration, trusted role verification, authenticated inbox, and PostgreSQL concurrency/recovery evidence are absent.
- **Partial — BX-41–43 / BWP-15:** local Decimal/count goldens and trend-break checks pass; live source/query coverage, independent detector labels, database execution, and reviewed health projection remain unavailable.
- No BA4 gate is complete from synthetic fixtures or offline migration rendering.

## Decisions or specification discrepancies recorded

Prompts95–97 preserve immutable official membership, keep candidate/no-hit evidence separate from approved rights and review, require bounded and separately authorized replacement work, and keep sampled health descriptive without extrapolation. Monitor dispatch remains disabled without approved source and role authority. Missing production evidence stays partial; see [decisions.md](../decisions.md).

## Exact next command or numbered prompt

Proceed to **Prompt98 / BWP-16**, implementing private API, CLI, SDK, and permission contracts while keeping unsupported transitions fail-closed.
