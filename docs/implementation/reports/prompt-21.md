# Prompt 21 — Add C++ support

Date: 2026-10-02 · Phase 4 · WP-19 part 3 · Read T §§12–13,18; A §11 · E2E-15, E2E-35

## Prompt 21 / Phase 4 — PARTIAL

The C++ plugin, pinned toolchain lock, image records, task fixtures and profiles already existed
from earlier work. This prompt audited them against the four PCB-21 tickets and closed the
defects that made their DoD claims false. Four defects were fixed in the C++ plugin, one in a
shared package that silently disabled the C++ quality profile, and three in the shared fixture
contract that made eight fixtures across C and C++ unvalidatable.

E2E-15 and E2E-35 remain **pending**: no C++ image was built or admitted in this prompt, so
there is no real-container evidence for C++. Everything below is local contract and fixture
evidence and is labelled as such.

### 1. Implemented functionality and changed files

**C++ analyzer evidence — the DoD inversion (PCB-21-2).** `parsers._clang_tidy` read only
`out/clang_tidy.err` and reported `findings=0` whenever its output was not there or not matchable.
`findings=0` is `MeasurementStatus.MEASURED`, not `MISSING`, so every clang-tidy-fed profile item
scored full marks. A candidate whose source merely made clang-tidy unreadable therefore scored
*above* one that was genuinely clean — the exact inversion "no analyzer omission silently raises
scores" forbids. The parser now reads both captured streams and raises when a successful run
produced no readable diagnostic, which the existing `_guard` already converts to `MISSING`.

- `plugins/languages/cpp/src/polycodebench_lang_cpp/parsers.py` — dual-stream read; the
  "did it actually look?" assertion; `_text` helper matching the C plugin's convention.
- `plugins/languages/cpp/src/polycodebench_lang_cpp/testparse.py` — the clang diagnostic pattern
  no longer requires the record to start at column 0.
- `plugins/languages/cpp/src/polycodebench_lang_cpp/guest/pcb_sanitizer_report.py` — `re.MULTILINE`
  on the UBSan report pattern, and its path group anchored to the line start. Without the flag `^`
  could only match at offset 0, so a UBSan report anywhere but the start of the text returned
  nothing and the run was recorded clean.

**Shared evaluator — C++ quality was never scored.** `evaluator.py` resolved the profile by
duck-typing `python_profile` then `rust_profile`. Every other plugin publishes `language_profile`
under the shared `LanguageProfileEvaluator` protocol, so for C, C++, Go and Java the profile was
`None`: findings reached the scorer, but no profile item was ever scored, and a language's whole
quality section read as empty rather than unevaluated.

- `packages/evaluation/src/polycodebench_evaluation/evaluator.py` — reads the protocol attribute.

**Instrumented fixtures were declared but never checked (PCB-21-4).** `SuiteAdmission` analyzed
only `reference` and `quality_defective` fixtures, but the ownership/leak/race fixtures are
`faulty`, so no analyzer ever ran on them. Their expectations were also rejected by the shared
schema outright, which meant those manifests could not validate at all.

- `packages/services/src/polycodebench_services/task_packages.py` — `FixtureExpectation` gains
  `expected_lane_findings` and the `candidate_crash` / `build_error` failure modes.
- `packages/evaluation/src/polycodebench_evaluation/suite_admission.py` — analyzes any fixture that
  declares lane findings; adds `instrumented-lane-defect-detected` and
  `crash-and-build-fixtures-rejected` gates.
- `plugins/languages/cpp/fixtures/top-words/manifest.yaml` — lane families corrected from
  sanitizer wording (`resource-leak`, `double-free`) to the profile's real equivalence families
  (`manual-ownership`, `undefined-behaviour`, `data-race`). Had the new gate run as first written,
  every lane fixture would have failed on names rather than on behaviour.

The same audit found the pre-existing `quality-defect-detected` gate was already unsatisfiable:
`expected_issue_families` is compared against the *family* component of each observation's
`issue_key`, but the manifest listed scanner *rule* names. It now declares the four real families
(`manual-ownership`, `value-copy`, `index-loop`, `throwing-destructor`); running the real context
scanner over `admission/quality-defective/src/top_words.cpp` produces exactly those four. The five
original rule names collapse to four families because two of them share `value-copy` — which is
the single-penalty behaviour PCB-21-3 requires, not a lossy edit.

- `tests/test_cpp_profile.py` — three regression tests pinning the analyzer-omission defect.

**Deliberately not changed.** A `ubsan` key in `PARSERS` and a "second sanitizer plan" were
considered and rejected: `ubsan` is not in `KNOWN_ANALYZERS` (so the entry would be unreachable
dead code) and UBSan shares the asan build profile, runtime and `undefined-behaviour` family; and
ASan and TSan are declared incompatible, so at most one instrumented lane may exist per task. The
existing single-plan `break` is correct.

### 2. Tests/commands actually run and their results

- `.venv/Scripts/python.exe -m pytest tests/test_cpp_plugin.py tests/test_cpp_profile.py tests/test_cpp_locks.py -q -p no:randomly` → **PASS, 128 passed**.
- `.venv/Scripts/python.exe -m ruff check` on the four changed source areas → **PASS, all checks passed**.
- `.venv/Scripts/python.exe -m mypy` on the three changed package files → **3 errors**, all
  pre-existing in `evaluator.py` (confirmed by stashing the change: the same three errors appear
  at the pre-edit line numbers 661/662/665).
- Pre-fix proof: with the parser fix reverted, `test_an_analyzer_that_printed_nothing_is_missing_not_clean`
  fails with `MeasurementStatus.MEASURED` and
  `test_clang_tidy_findings_are_found_whichever_stream_carries_them` finds 0 findings for a
  stdout-carried diagnostic. Both pass with the fix restored.
- `.venv/Scripts/python.exe -m pytest tests/ -q -p no:randomly --ignore=tests/test_analyzer_contracts.py`
  → **46 failed, 820 passed, 166 skipped**. All 46 failures are in `tests/test_python_parsers.py`
  (22) and `tests/test_rust_parsers.py` (24), all `ValidationError` on `PlanOutput.empty_is_clean`
  and `ToolIdentity.advisory_snapshot_state` — fields the externally-modified plugins-api models no
  longer declare. **Not caused by this prompt.**
- Not run, and why: no C++ Docker image was built and no task was admitted, so no real-container
  E2E-15/E2E-35 evidence exists and the two new admission gates are unexecuted. Repository-wide
  mypy/ruff was not run.

### 3. Acceptance gates

- **PCB-21-1 — implemented, verified locally.** Pinned standard/compiler/build recipes with
  release and sanitizer profiles resolved through separate lock entry points; incompatible
  instrumentation rejected at validation. Confirmed directly that the serialized performance plan
  contains no `-fsanitize` token and that warmup (5) and measured iterations (20) are frozen.
- **PCB-21-2 — implemented, verified locally.** The analyzer-omission inversion is closed and
  pinned by tests that fail on the pre-fix code. UBSan and indented-diagnostic parsing fixed.
- **PCB-21-3 — implemented, verified locally; no defect found.** Single composite ownership holds
  through equivalence families; non-owning raw pointers produce no finding; duplicate reports
  cannot move a score. No change was needed.
- **PCB-21-4 — implemented; local verification only.** All nine C++ fixtures and all six
  languages' manifests validate; lane expectations are now parsed and enforced by two new gates.
  The gates themselves are **unexecuted** because admission requires Docker images not built here.
- **Pending:** E2E-15 and E2E-35 for C++ (no image build/admission); the two new admission gates.

### 4. Decisions or specification discrepancies recorded

- **D-21-01** — the shared evaluator reads `language_profile`, the protocol attribute, instead of
  probing per-language names. This changes scoring interpretation for C, C++, Go and Java from
  "unscored" to "scored"; it does not alter any already-published score.
- **D-21-02** — an analyzer that printed nothing is missing evidence, not a clean scan.
- **D-21-03** — instrumented fixtures declare lane expectations in the shared schema rather than
  being special-cased per language.

Specification discrepancy: the C and C++ fixture manifests declared expectations the shared
schema rejected. This is recorded and resolved in the schema (D-21-03) because the declarations
describe real instrumented outcomes rather than errors in the manifests.

### 5. Exact next command or numbered prompt

`Next: Prompt 22 — Add Go support.`

When Docker and the network-fetch step are available, the C++ real-container gate is:
`uv run python scripts/fetch_cpp_components.py` then the C++ image build and admission, which
would execute the two new admission gates for the first time.

### 2026-10-05 follow-up — executable admission

The statement above is historical and is superseded by this follow-up. The pinned C++ development
images were built and the synthetic fixture pack passed **27/27 executable-admission checks**
across eight authored variants. The five reference repetitions matched; clang-tidy, cppcheck,
context and ASan findings were measured on the reference and applicable defective variants; the
performance smoke passed. Evidence: `evidence/prompt-21-cpp-admission-followup.json` (report
digest `sha256:aa8341ae2c57922c578be9f6f4e7991539af2ac8ff79a4600589d50585670056`).

This is local `development_sandbox` synthetic-fixture evidence. Quality admission, curator/owner
approval, downstream scoring/replay, and E2E-15/E2E-35 remain pending. The local TSan runtime could not initialize (`unexpected memory mapping`), so the race/concurrency
fixture is not covered. That acceptance dimension remains unverified; no race finding is claimed.
