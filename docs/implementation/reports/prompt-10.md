# Prompt 10 / Phase 2 — DONE (development sandbox; quality admission pending Prompts 12–15)

## 1. Implemented functionality and changed files

- **Language plugin** (`plugins/languages/python/`): `LanguagePlugin` implementation, typed build/test/symbol/analyzer/performance plans, test-report reconciliation, symbol index, task validation and freeze view, entry point registered through the `polycodebench.language_plugins` group with an administrator allowlist.
- **Shared extension interfaces** (`packages/plugins-api/src/polycodebench_plugins_api/`): `contracts.py`, `protocols.py`, `registry.py`, `results.py`, `testreport.py`, `admission.py`.
- **Pinned offline images** (PCB-10-1): `infra/images/python/{Dockerfile,runtime.in,runtime.lock,evaluator.in,evaluator.lock}`, `scripts/build_python_images.py`, `config/images/python-v1.json`, `config/plugins/allowlist-v1.yaml`. Runtime `sha256:beb3dd62e10b…`, evaluator `sha256:dd801029a063…`, base `python@sha256:44ff437bba87…` (Python 3.12.14); pytest 9.0.2, Hypothesis 6.150.2, Ruff 0.16.8, mypy 1.19.1, Bandit 1.9.2, Semgrep 1.150.0; 8/80 locked packages; `docker build --network none` from a hash-verified wheelhouse, installed set compared to the lock.
- **Profiles** (PCB-10-3): `config/languages/python-profile-v1.yaml`, `profile.py`, `observations.py`, AST context scanner `guest/pcb_context_scan.py`.
- **Admission engine** (PCB-10-4): `packages/evaluation/src/polycodebench_evaluation/{plan_runner.py,suite_admission.py}`, `scripts/{python_task_tool.py,python_admit_all.py,python_pilot_inventory.py,python_conformance.py,record_python_tool_fixtures.py}`, public fixture `plugins/languages/python/fixtures/top-words/`, 12 pilot clusters under git-ignored `.protected/taskpacks/python-pilot/`, committed identity-only `taskpacks/python-pilot/inventory.yaml`.
- Docs: `docs/python-plugin.md`, `plugins/languages/python/AUTHORING.md`.

### Fixes made this session (inherited work was 8/12 clusters passing)

| Defect | Fix |
|---|---|
| `manual-counter` missed `count = 0` + `for _ in items: count += 1` (the `len()` idiom) | Added `_counting_loop`; fires only when the loop discards its target and its entire body is the increment (D-10-03) |
| `string-concat-in-loop` missed `text += str(part) + "."` | Widened `_is_string_like` to provably textual operands: `str`/`repr`/`format`, `join`, textual concatenation; numeric/unproven still ignored (D-10-03) |
| `.mypy_cache`/`.ruff_cache` sealed into `archive-path-guard`'s hidden reference, breaking `output-contract-reference` | Caches removed; `_files()` ignores tool-cache dirs and `seal`/`admit` refuse any package containing them (D-10-04) |
| `ledger-stream` defective declared `range-len-index-loop` but had none | Added a genuine index-only loop to the variant (D-10-05) |
| `rate-limiter` defective declared `mutable-default` but `seen=[]` was never used, so correctly `benign_in_context` | Made the variant actually mutate `seen`, which is what makes it a shared-state defect (D-10-05) |
| 8 strict-mypy findings in new code; guest scripts not covered by any override | Fixed the 8 at source; added the documented `polycodebench_lang_python.guest.*` override (D-10-09) |
| Two stray `.whl` files at the repo root, unrelated to the pinned wheelhouse | Deleted |

Widening the two detectors required an image rebuild (the guest tree is pinned), resealing all manifests against the new digests, and re-running admission — all done.
## 2. Tests and commands run

- `python scripts/build_python_images.py` — PASS (both images, `--network none`, lock drift check).
- `python scripts/python_task_tool.py seal-all` — PASS (12/12 resealed).
- `python scripts/python_admit_all.py` — **PASS: 12/12 packages**, 21–24 checks each, every variant executed in the pinned images.
- `python scripts/python_pilot_inventory.py …` — PASS: 12 packages, 12 admission-passed, clusters 12/12.
- `python scripts/python_conformance.py --report docs/implementation/evidence/prompt-10-conformance.json` — **PASS: 14/14 cases** across all 7 categories.
- `python -m pytest tests -q -p no:cacheprovider` — PASS: **346 passed, 136 skipped**.
- `PCB_TEST_DOCKER=1 python -m pytest tests/test_python_conformance_docker.py tests/test_python_guest.py` — PASS: **2 passed in 594s** (real sandbox).
- `python -m mypy --disable-error-code=import-untyped packages/plugins-api/src packages/evaluation/src plugins/languages/python/src` — PASS: 26 files, strict, no issues.
- `ruff format --check .` (201 files) / `ruff check .` / `check_boundaries.py` / `verify_prompt00.py` — all PASS.
- **Not run:** production execution tier, registry publication, live model/judge endpoints, cloud. No paid, remote or publication action was taken.

## 3. Acceptance gates

- **Satisfied (development sandbox):** PCB-10-1, PCB-10-2, PCB-10-3, PCB-10-4. E2E-04 Python subcase, E2E-15 Python, E2E-16 Python.
- **Awaiting later prompts (deliberately not claimed):** generic evaluator stage integration (Prompt 12), performance baseline/canary and paired measurement (13), judge anchors and human calibration (14), deterministic scoring replay (15), Rust/other languages (11, 19–23). E2E-04, E2E-15 and E2E-16 therefore remain `not_run` overall — the Python subcases passed.
- **Blocked/external:** production VM isolation (Prompt 06, owner-deferred); curator approval and task freeze; owner rights confirmation; hidden-lane object-store registration; live providers.
- **Explicitly not admitted:** every report carries `quality_admission: pending`; the inventory carries `fully_admitted: 0`, `frozen: 0`. No pilot task is frozen. Hidden bundles, references, variants and reports stay in git-ignored `.protected/`.

## 4. Decisions and discrepancies

D-10-01 … D-10-10 in `decisions.md`. The material one is **D-10-05**: when a fixture failed to demonstrate its pre-registered `expected_issue_families`, the response was to fix a genuinely too-narrow detector or to make the defective variant exhibit the anti-pattern it claimed — never to relax the declaration. **D-10-07** records that a green admission report means the variants ran and the oracles discriminate, nothing more.

## 5. Next

Next: Prompt 11 — Implement Rust support.