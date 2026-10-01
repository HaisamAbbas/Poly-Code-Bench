# Prompt 11 / Phase 2 — DONE (development sandbox; quality admission pending Prompts 12–15)

## 1. Implemented functionality and changed files

- **Rust language plugin** (`plugins/languages/rust/`): `RustLanguagePlugin`, typed build/test/clippy/context/Miri/dependency/performance plans (`plans.py`), parsers (`parsers.py`, `testparse.py`), symbol index (`symbols.py`), task validation and freeze view (`taskspec.py`), guest runner and scripts (`guest/pcb_rust_run.py`, `pcb_lock_audit.py`, libtest qualification in `pcb_rust_test_report.py`).
- **Pinned offline images** (PCB-11-1, rebuilt): `infra/images/rust/Dockerfile`, `scripts/build_rust_images.py`, `config/images/rust-v1.json`, allowlist — now with a guest interpreter and a baked Miri sysroot.
- **Profiles** (PCB-11-3): `config/languages/rust-profile-v1.yaml`, `profile.py`: tokens are not findings; context and required behaviour decide.
- **Shared contracts**: `executable_workspace` on `ResourcePolicy`/`SandboxSpec` (default off), honoured by the Docker driver and `PlanRunner`; `suite_admission.py` made language-neutral (D-11-16).
- **Fixtures and pilot** (PCB-11-4): `plugins/languages/rust/fixtures/top-words/`; twelve protected pilot clusters (`.protected/taskpacks/rust-pilot/`); `taskpacks/rust-pilot/inventory.yaml`; `scripts/{rust_task_tool,rust_admit_all,rust_pilot_inventory,rust_conformance,rust_quick_check,record_rust_tool_fixtures}.py`; recorded real tool output `tests/fixtures/rust_tool_output/` (10 scenarios).
- **Docs**: `docs/rust-plugin.md`, ledger updates (tickets, decisions D-11-10…D-11-20, E2E matrix, phase map, progress, this report).

### Defects found and fixed

| Defect | Fix |
|---|---|
| Rust images had no Python; the sandbox provider drives every guest with `python -I -B -S`, so no Rust plan could run | Pinned interpreter copied in with its own libc (D-11-10) |
| Workspace tmpfs is `noexec`; compiled test binaries could not execute | `executable_workspace`, default off (D-11-11) |
| Miri rebuilt its sysroot at run time in a read-only, noexec sandbox | Baked sysroot via `MIRI_SYSROOT` (D-11-12) |
| A Miri run with exit 0 and garbled output read as clean | Clean requires libtest's summary (D-11-14) |
| `observations.slug` turned dots into hyphens, so no rule mapping matched | Keep `._-`; column no longer in clone keys (PCB-11-3) |
| `discover_cases`/symbol sanitiser treated `//` inside strings as comments (found by the pilot authors) | One left-to-right literal tokenizer; regression test |
| Suite admission assumed Python layout | Plugin hints (D-11-16) |

## 2. Tests and commands run

- Rust offline: `tests/test_rust_{plugin,parsers,guest,profile,locks,pilot_inventory}.py` — 93 passed (parsers replay real recordings of 10 scenarios).
- Prior suites re-run after the shared-contract change: Python plugin/parsers/guest, plan runner, suite admission, sandbox, task packages — 185 passed, 7 skipped.
- Live Docker (`PCB_TEST_DOCKER=1`): `tests/test_rust_docker.py` (3) plus the existing plan-runner and sandbox containment tests — 22 passed.
- `scripts/rust_admit_all.py` — 12/12 packages passed executable admission (re-run by me after the authors' own runs).
- `scripts/rust_conformance.py` — 16/16 cases, all 7 categories.
- `ruff format --check .`, `ruff check .`, `check_boundaries.py`, `verify_prompt00.py` pass; strict mypy on the touched packages shows only the repo's usual unused-ignore notes under `--disable-error-code=import-untyped`.
- Not run: PostgreSQL-backed suites (unchanged by this prompt), anything cloud/registry/paid-provider.

## 3. Acceptance gates

| Ticket | Gate | Result |
|---|---|---|
| PCB-11-1 | Toolchain/Cargo.lock and image digests determine evaluator identity | passed |
| PCB-11-2 | Miri unsupported ≠ clean ≠ candidate UB; only applicable supported tasks require it | passed (real transcripts, live) |
| PCB-11-3 | clone/unwrap/unsafe tokens are not violations; context decides | passed |
| PCB-11-4 | Intended faults detected; valid alternatives accepted; metadata complete | passed (12/12 clusters, development sandbox) |

**Quality admission is not claimed.** Pending: generic evaluator integration (Prompt 12), performance (13), judge anchors (14), scoring replay (15), production execution tier (Prompt 06, owner-deferred), curator approval/freeze, owner rights confirmation, hidden-lane registration. The inventory records `fully_admitted: 0`, `frozen: 0`.

## 4. Decisions and discrepancies

D-11-01…D-11-20 in `decisions.md`. Open: the dependency audit has no real advisory snapshot (needs a network fetch, an owner action); `rules/clippy.toml` names two lints Clippy 1.83 does not know; no Rust pilot task declares a performance workload; images are local development builds (no registry publication); the pilot clusters were authored by three parallel agents and independently re-admitted, not independently *reviewed* by a human curator.

## 5. Next

Prompt 12 — Implement independent grading and normalized evidence.
