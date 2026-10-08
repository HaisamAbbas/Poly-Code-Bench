# Benchmark audit implementation ledger

Status: active. Prompts83–84 are complete; Prompt85 is next. This ledger describes the actual worktree and never treats metadata or fixtures as live audit evidence.

## Repository inventory at Prompt83

- Python modular monolith under `packages/*`; Next.js/TypeScript frontend under `apps/web`.
- Canonical JSON helpers exist at `packages/core/src/polycodebench_core/canonical.py`; §7 audit evidence-document schemas/persistence are not implemented. Prompt84 adds separate catalog/planning contracts only.
- Persistence/migrations live in `packages/persistence/src/polycodebench_persistence`. The tracked `stage_job` has attempt, evaluation and release FKs only, versus six scopes in addendum §8.
- Fenced queue definitions/repository are `packages/core/src/polycodebench_core/jobs.py` and `packages/persistence/src/polycodebench_persistence/jobs.py`.
- API routes mount in `packages/api/src/polycodebench_api/app.py`; public release/submission routes have no benchmark audit operations.
- Tracked benchmark importers, corpus connectors, match review, risk/temporal assessment, sealing, monitoring, health and audit attestations are absent.
- Prompt84 adds strict registry/capability contracts at `packages/core/src/polycodebench_core/benchmark_audit_registry.py`, safe duplicate-rejecting YAML loading and a no-dispatch resource planner at `packages/services/src/polycodebench_services/benchmark_audit_catalog.py`, plus versioned configuration in `config/benchmark-audit/`. The catalog has 25 §5 families, eight source policies and an explicit capability row for each family. No importer or connector is claimed implemented.
- The worktree contains uncommitted task generation/screening/overlap/exposure/canary code under `packages/taskgen` and contamination-control docs. These are inspected as work in progress and are excluded from Prompt83 commits.
- Native benchmark metrics and code scoring remain in the existing evaluation/scoring/plugin paths; audit health is a separate module and must not change them.

## Contract reuse

| Contract | Current evidence | Audit implication |
|---|---|---|
| Canonical JSON | `polycodebench_core.canonical` | Extend/reuse; no duplicate digest profile. |
| Durable queue | core jobs, persistence jobs and Alembic migrations | Add explicit audit scope, fencing, idempotency and reservations. |
| Immutable evidence | persistence artifacts/object store and release manifests | Reuse SHA-256 refs/visibility; add strict audit payloads. |
| Curation/exposure | task admission plus uncommitted taskgen | Inspect before reuse; no persisted benchmark audit contract exists. |
| Gateway/accounting | orchestration gateway and persistence call ledger | Diagnostics use ordinary accounting and stay out of ordinary recommendations. |
| Authorization | API identity/auth and service RBAC | Derive tenant from authenticated identity; public routes never dispatch work. |
| Frontend | `apps/web`, with local rules in `apps/web/AGENTS.md` | Follow its Next.js-version-specific documentation before UI edits. |

## Exact-byte source bridge

The addendum §1.1 says five documents were read but names/hashes only three and leaves a blank row. The published three hashes match exact current bytes. The two related repository implementation supplements below are not asserted to be in the source bridge; hashes are recorded to preserve their input identity.

| Source | SHA-256 | Authority |
|---|---|---|
| `PolyCodeBench-Architecture-v1.md` | `6dfee84b9787f315d7d5aa7afd9b1de29760aac16b5d3ac887b7ecd76d899d69` | addendum §1.1 |
| `PolyCodeBench-Technical-Implementation-Spec-v1.md` | `2dbfd0f00c561b9348419a2659794913658fb47d15090e09a3e905a89cdea7bd` | addendum §1.1 |
| `PolyCodeBench-Codex-End-to-End-Prompt-Pack-v1.md` | `0687c2e1e6340fd3b0f69b553418c290aeb59aa7cdf3a88b9d2ef3159c7e1344` | addendum §1.1 |
| `docs/implementation/requirements-matrix.md` | `35092955bddeee000da5af886968b821f939f7af18505226252ce1830ba26122` | supplemental contract |
| `docs/implementation/tickets.md` | `45b2bcef28fcd359471e157711075e2e619c72f7b83c1f7448cfc38dc4efa3c4` | supplemental contract |

Hashes computed from exact current bytes on 2026-10-08. No source document was edited.

The existing checkout contains REQ/WP/E2E identifiers in the source specifications and `docs/implementation/requirements-matrix.md`; repository-wide Markdown search did not find the named DREQ/DWP/DXE or AREQ/AWP/AE2E families. Their absence is recorded as ADDENDUM-GAP-04 rather than repaired by inventing IDs.

## Prerequisites and blockers

| Capability | State | Exact evidence/unblock action |
|---|---|---|
| HumanEval/MBPP/SWE-bench inputs | Repository metadata pinned for HumanEval, MBPP, SWE-bench and SWE-bench Verified; item bytes/splits not imported | Confirm exact immutable dataset bytes/splits and authorized local snapshots; gated access stays blocked. |
| Corpus coverage | No approved benchmark-audit snapshots found | Owner-approved finite scopes, rights/retention, snapshots and request caps. |
| Human review | No assigned audit reviewer evidence found | Independent authorized reviewer and recorded review events. |
| Model diagnostics | No approved target/reference plan found | Purpose, exact model context, endpoint, labels and budget; otherwise blocked. |
| Timestamp authority | No audit receipt provider configured | Approved independently verifiable receipt/key or local-only claim. |
| Seal key custody | No audit envelope-encryption/KMS workflow evidenced | Approved key manager, access/rotation and restore plan. |
| Agent/image modalities | No conformant audit adapters found | Authorized exact assets and modality-specific parser/runtime. |

Independent CPU/local work continues around these gates. The exact historical baselines and queue discrepancy are in `decisions.md`.

## Prompt84 / BWP-02

- Complete: 25 §5 benchmark records with explicit version/split/access/rights/modality/status fields; eight finite source policies; 25 component/source/modality/runtime capability rows; strict catalog cross-reference validation; safe duplicate-key YAML loading; and a pure bounded planner.
- The planner computes the proposed 300-task/8-source/5-stage ceiling (12,000 planned query units and 30,000 candidate slots) while returning `dispatch_allowed=false`, zero model calls, unknown prices and per-benchmark/source blockers.
- Current source checks pin metadata only: HumanEval, MBPP, SWE-bench repository, SWE-bench Verified dataset revision, EvalPlus v0.3.1 and its two data version labels. No task payloads were downloaded. All source connectors remain unimplemented and corpus scopes unapproved.
- Next: Prompt85 / BWP-03. The migration must address the tracked three-scope queue, not the five-scope baseline assumed in §8.
