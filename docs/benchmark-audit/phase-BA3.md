# Phase BA3 — Context, sealed evaluations, and behavioral diagnostics

## Implemented functionality and changed files

- **Prompt 92 / BWP-10:** immutable model-context and temporal-assessment contracts preserve exact revision/cutoff evidence and chronology intervals. The evaluator retains unknown, overlap, mutable-alias, earlier-exposure, and later-update states. Hiding commitments bind bytes; local timestamp receipts remain explicitly non-independent. Details: [prompt-92.md](reports/prompt-92.md).
- **Prompt 93 / BWP-11:** per-artifact AES-GCM encryption, wrapped-key rotation, tenant-bound sealed manifests, append-only access events, authorization-before-handoff, and private canary contracts. The included crypto provider is local-development-only; no production KMS or database was configured. Details: [prompt-93.md](reports/prompt-93.md).
- **Prompt 94 / BWP-12:** registered behavioral methods, frozen diagnostic plans, reviewed controls, descriptive observations and assessments, usage/exposure reconciliation, retry caps, and explicit calibration/power limitations. ConStat is registered but remains unsupported until an exact implementation is pinned. Details: [prompt-94.md](reports/prompt-94.md).
- Phase ownership and evidence remain separated: chronology evidence does not prove model training; encryption does not authorize disclosure; behavioral performance remains separate from source overlap and inclusion claims.

## Tests/commands actually run and results

- Prompt92 combined audit regression: **140 passed**; Ruff, strict Mypy, TypeScript canonical checks, migration-head check, and offline P92 upgrade/downgrade rendering passed.
- Prompt93 focused regression: **37 passed**; combined Prompt85–93 audit regression: **154 passed**; Ruff, strict Mypy, TypeScript canonical checks, migration-head check, and offline P93 upgrade/downgrade rendering passed.
- Prompt94 focused regression: **31 passed**; combined Prompt85–94 audit regression: **162 passed**; Ruff, strict Mypy, TypeScript canonical checks, lock check, migration-head check, and offline P94 upgrade/downgrade rendering passed.
- All tests used local/synthetic fixtures. No PostgreSQL migration execution, source query, external timestamp verification, KMS operation, or model dispatch occurred.

## Acceptance gates satisfied, pending and blocked

- **Partial — BX-25–27 / BAT-10-A–D:** chronology, model-context, commitment, and local receipt contracts are tested. Trusted source/model chronology and an external timestamp authority remain unavailable.
- **Partial — BX-28–30 / BAT-11-A–D:** local crypto, append-only access, recipient/digest binding, and canary controls are tested. Approved production KMS, live authorization, audit database, and independently reviewed source/date evidence are absent.
- **Partial — BX-31–33 / BAT-12-A–D:** method registry and fail-closed applicability, frozen experiment controls, observation reconciliation, and calibration/power blockers are implemented. There is no pinned executable method, live access writer, database execution, controlled owned-training dataset, or validated power analysis.
- **BWP-10, BWP-11, and BWP-12 remain partial.** Synthetic fixtures verify contracts, not live evidence or production integrations.

## Decisions or specification discrepancies recorded

- **ADDENDUM-DECISION-27–30:** risk remains a fixed observed heuristic with explicit missingness; reviewed signals do not imply probabilities; v2 history uses guarded migrations.
- **ADDENDUM-DECISION-31–33:** ConStat is not approximated; model retries bind one access event each and reconcile to gateway accounting; power and training-compute uncertainty stay explicit.
- The specification’s §1.1 mismatch between its stated five source Markdown files and its three listed/hashed files remains documented in [prompt-93.md](reports/prompt-93.md).

## Exact next command or numbered prompt

Proceed in order to **Prompt 95 / BWP-13 — Firewall admission and independently validated replacements**. Preserve official benchmark versions and keep admission, rights, validity, lineage, and independent review gates explicit.
