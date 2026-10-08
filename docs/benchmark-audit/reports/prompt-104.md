# Prompt104 — Broader benchmark adapters and scope conformance

## Implemented functionality and changed files

- Added a deterministic scope-conformance report for all 25 §5 families. It maps exact dataset/repository metadata pins, split policy/state, access and rights, lineage, component and modality coverage, source groups, test references, runtime status, and explicit live blockers. Added strict catalog checks against registry/importer state drift, cyclic family lineage, and unsupported `audit_conformant` / `live_verified` claims.
- Extended the bounded local importer with a GSM8K JSONL adapter. It preserves the question and full solution, extracts only a unique explicit `####` final answer, retains split/source provenance, and uses deterministic non-empty-row ordinals. Any malformed/oversized row blocks the complete sequence to prevent sampled IDs shifting. Its tests use synthetic records only; catalog importing remains blocked pending rights approval.
- Pinned repository or dataset metadata where the official refs were available and documented access blockers. Added `scripts/benchmark_scope_conformance.py` and `docs/benchmark-audit/source-observations-2026-10-09.md`.
- Updated [registry-v1.yaml](../../../config/benchmark-audit/registry-v1.yaml), [capability-matrix-v1.yaml](../../../config/benchmark-audit/capability-matrix-v1.yaml), core import/catalog contracts, services, importer tests, scope-report tests, [acceptance.md](../acceptance.md), [decisions.md](../decisions.md), [implementation-ledger.md](../implementation-ledger.md), [commands.md](../commands.md), [TASKS.md](../../../TASKS.md), and the BA7 phase report.

## Tests/commands actually run and results

- `uv run --locked pytest -q tests/test_benchmark_importers.py tests/test_benchmark_audit_catalog.py` — **29 passed**.
- `uv run --locked ruff check` on the five changed Python modules, two focused test files, and scope-report script — **passed**.
- `uv run --locked ruff format --check` on those seven Python paths — **passed; all formatted**.
- `uv run --locked mypy` on the four changed core/service modules and scope-report script — **passed; no issues in 5 files**.
- `uv run --locked python scripts/benchmark_scope_conformance.py` piped into JSON summary — **25 families, 0 live-verified, 4 synthetic-source-fixture families**.
- Read-only `git ls-remote` checks pinned available repository/dataset refs in the source-observation ledger. GAIA required authentication and HellaSwag returned a DMCA takedown response; ARC's old GitHub path was unavailable and its official data page resolved to the Hugging Face dataset. No benchmark payloads, hidden answers, code, models, or native harnesses were fetched or executed.

## Acceptance gates satisfied, pending and blocked

- **Partial — BAT-22-A/B/D and BX-59:** every §5 row now reconciles scope, lineage, version metadata, access/rights, source pins, runtime and live state. MMLU-Pro and ARC dataset repository revisions are pinned. GSM8K's local JSONL adapter is fixture-tested. Other family adapters, exact task membership, rights review, checker/template/version confirmation and all live source conformance remain incomplete.
- **Blocked — BAT-22-C:** GAIA is gated; HellaSwag remains blocked under the recorded GitHub notice; no agent environment, tool-call runtime, image/OCR/perceptual audit or custom-private owner manifest is available. No text-only or fixture-only claim is promoted to full modality conformance.
- **Pending:** native scoring integrations remain untouched; no approved source bytes, item rights, model runs, agent tasks, image assets, source scans, reviewer judgments, or production benchmark imports were exercised.
- **BWP-22 / BREQ-02, BREQ-04, BREQ-29 / BX-59: partial.** BA7 is partial. Details are in `acceptance.md` and `source-observations-2026-10-09.md`.

## Decisions or specification discrepancies recorded

- `ADDENDUM-DECISION-55` distinguishes a repository metadata pin from an exact dataset snapshot and rights-approved membership.
- `ADDENDUM-DECISION-56` prohibits mirror fallback for gated or takedown sources. `ADDENDUM-DECISION-57` keeps changed TruthfulQA and IFEval variants/checkers distinct.
- `ADDENDUM-GAP-06`: §1 says five source MDs were read, but its table lists only three historical documents; only those three are present in the workspace. No missing documents were reconstructed or substituted.
- Official metadata evidence and exact observed refs are recorded in `source-observations-2026-10-09.md`.

## Exact next command or numbered prompt

Proceed to **Prompt105 / BWP-23**, integrated lifecycle demonstration and reviewed projections. Use only approved, supported local/fixture paths and keep live/source/human/crypto/modality requirements visibly pending or blocked.
