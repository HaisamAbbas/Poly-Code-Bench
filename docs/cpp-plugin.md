# C++ language plugin

Prompt 21 / WP-19 part 3. The C++ plugin is a first-class language identity: it never reuses the
C or Rust profile, weight set, rule mappings or analyzer plans. What it shares with every other
language is the extension contract in `packages/plugins-api` and the plan/supervisor/evidence
pipeline.

* Package: `polycodebench-lang-cpp` (`plugins/languages/cpp`)
* Language/plugin id: `cpp`
* Entry point: `polycodebench_lang_cpp.plugin:CppLanguagePlugin` (group
  `polycodebench.language_plugins`)
* Profile: `cpp-profile-v1` (`config/languages/cpp-profile-v1.yaml`) with weights read from
  `config/languages/profiles-v1.yaml#profiles.cpp`
* Toolchain lock: `cpp-toolchain-v1` (`config/languages/cpp-toolchain-v1.json`)
* Images: `config/images/cpp-v1.json`, three recipes (`runtime`, `evaluator`, `performance`)

## Recipes and the pinned contract

A task may only name a compiler, a language standard and a build profile that appear in
`config/languages/cpp-toolchain-v1.json`. Anything else is rejected at task validation, so two
runs of the same task cannot differ by toolchain.

| Profile | Sanitized | Release | Used by |
|---|---|---|---|
| `debug` | no | no | build plan, test plans, analyzer compilation checks |
| `release` | no | **yes** | the performance lane only |
| `sanitize_address_undefined` | yes | no | `asan` / `ubsan` analyzer plans |
| `sanitize_thread` | yes | no | `tsan` analyzer plans |

AddressSanitizer and ThreadSanitizer cannot share a translation unit or a process. A quality plan
that asks for `["address", "thread"]` (or `["undefined", "thread"]`) is rejected at validation,
and `performance_plan` refuses to build a timed workload from a sanitized profile. Release timing
is therefore always measured on release code.

Analyzers (`AnalysisPlan.analyzer_id`):

| id | Tool | Produces |
|---|---|---|
| `clang_tidy` | `clang-tidy` with the pinned `rules/.clang-tidy` checks | `cpp.clang_tidy.*` |
| `cppcheck` | `cppcheck --xml --xml-version=2` with the pinned suppressions | `cpp.cppcheck.*` |
| `context` | `pcb_cpp_scan.py` (the context scanner) | `cpp.context.*` |
| `asan` | ASan/UBSan-instrumented run of the task's test groups | `cpp.asan.*`, `cpp.ubsan.*` |
| `tsan` | TSan-instrumented run of the task's test groups | `cpp.tsan.*` |

An analyzer that is required by the task but missing, crashed, timed out or reported an
unsupported path is `MISSING` (or `NOT_APPLICABLE`), never a clean scan with zero findings. An
absent analyzer can therefore never raise a score.

## Guest tooling

Everything below is plain-stdlib Python copied verbatim into the images at `/opt/pcb/guest`, and
rule files at `/opt/pcb/rules`. `pcb_rust_run.py` is the model for `pcb_cpp_run.py`; the two run
the same contract under different schema names.

### `pcb_cpp_run.py`

```
python -B pcb_cpp_run.py --name out/NAME --deadline SEC [--cleanup DIR]... [--merge]
                         [--max-bytes N] -- CMD ARG...
```

Writes `out/NAME.out`, `out/NAME.err` (or merged into `.out` with `--merge`) and
`out/NAME.run.json`:

```json
{"schema": "pcb-cpp-run-v1", "command": ["python", "-B", "run.py"],
 "exit_code": 0, "timed_out": false, "duration_ms": 12,
 "truncated": false, "note": ""}
```

Exit codes: the child's own status; `127` not found; `126` not executable; `128 + signal`;
`124` when the deadline fires (the whole process group is killed and partial output is kept).
Every `--cleanup` directory is removed on every exit path.

### `pcb_cpp_build.py`

```
python -B pcb_cpp_build.py --name out/build --deadline SEC [--root /workspace]
                           [--compiler clang++] [--cxxflag FLAG]... [--include DIR]...
                           [--define NAME=VALUE]... --source FILE [--source FILE]...
                           [--archive out/libpcb.a]
```

Compiles each `--source` with `-c`, collects compiler diagnostics into `out/NAME.err` and writes
`out/NAME.run.json`:

```json
{"schema": "pcb-cpp-build-v1", "exit_code": 0, "timed_out": false, "duration_ms": 431,
 "compiled": ["src/top_words.cpp"], "failed_sources": []}
```

A compile error exits `1` with the failing sources named; a clean build exits `0`. Anything the
compiler cannot start exits `126`/`127`.

### `pcb_cpp_test.py`

```
python -B pcb_cpp_test.py --name out/GROUP --deadline SEC [--compiler clang++]
                          [--cxxflag FLAG]... [--include DIR]... --source FILE...
                          --test FILE --binary out/GROUP.bin [--arg A]...
```

Compiles and links the candidate sources plus the group's test translation unit, then runs the
binary with the remaining deadline. Compiler diagnostics go to `out/GROUP.err`, the program's
combined stdout/stderr to `out/GROUP.out`, and `out/GROUP.run.json` records:

```json
{"schema": "pcb-cpp-test-v1", "phase": "run", "compile_exit_code": 0, "exit_code": 1,
 "timed_out": false, "duration_ms": 55, "truncated": false}
```

`phase` is `"compile"` when the binary was never produced (the compile exit code is then non-zero)
and `"run"` otherwise. A hung test group exits `124` with `timed_out: true`.

### `pcb_cpp_test.hpp` (rules)

The pinned test harness the hidden tests compile against. It defines `PCB_CHECK(id, expr)` and
`PCB_SKIP(id)`, which print one protocol line per case:

```
PCBCASE <case-id> start
PCBCASE <case-id> pass|fail|skip [detail]
```

The `start` line is printed before the expression is evaluated, so a run killed by the deadline
names the case that was in flight. An optional trailing `detail` after the event is the case
reason. `pcb_test::failures` counts failures so the test's own `main` can return non-zero.

### `pcb_cpp_scan.py`

```
python -B pcb_cpp_scan.py --root /workspace --output out/context.json
                          [--opportunities a,b] [--tags t1,t2] PATH...
```

Exits `0` clean, `1` when at least one violation was recorded, `2` when a file could not be
parsed (an unanswered check, never a clean one). Output:

```json
{"schema": "pcb-cpp-scan-v1", "scanner_version": "1",
 "opportunities": ["ownership"], "tags": ["ownership"], "complete": true,
 "files": [{"path": "src/top_words.cpp", "parsed": true, "error": null}],
 "findings": [{"rule": "raw-owning-pointer", "path": "src/top_words.cpp", "line": 12,
               "end_line": 12, "column": 9, "symbol": "top_words",
               "verdict": "violation", "confidence": "high",
               "message": "manual allocation is never released", "evidence": {}}]}
```

`verdict` is `violation`, `benign_in_context` or `hint`. Rules:

| rule | verdict when |
|---|---|
| `raw-owning-pointer` | a `new` result is never handed to `std::unique_ptr`/`make_unique`/`make_shared` and the class declares no destructor — `violation`; wrapped immediately — `benign_in_context` |
| `raw-delete-mismatch` | `delete` of a non-pointer or `delete[]` on a non-array allocation — `violation` |
| `rule-of-five-missing` | a class with an owning raw member and a user-declared destructor but no copy/move control — `violation` |
| `raw-pointer-nonowning` | a raw pointer parameter that is only read — always `benign_in_context`: a non-owning raw pointer is legal C++ and is never a violation |
| `redundant-container-copy` | `auto b = a;` for a container/string variable — `violation` |
| `pass-by-value-container` | a by-value container/string parameter — `violation` |
| `index-loop-container` | an index loop over a container, or a hand-written iterator loop — `violation` |
| `string-concat-loop` | `+=` on a string-typed variable inside a loop — `violation` |
| `move-on-const` | `std::move` applied to a `const` object — `violation` |
| `throw-in-destructor` | a destructor that can throw — `violation`; `noexcept(false)` — `hint` |
| `no-automatic-move` | `return local;` for a named local container that would be copied — `violation` |

### `pcb_sanitizer_report.py`

```
python -B pcb_sanitizer_report.py --detector asan|ubsan|tsan --report out/asan.json
                                  --stdout out/asan.out --stderr out/asan.err
                                  --exit-code N [--timed-out]
```

Writes `pcb-cpp-sanitizer-v1`:

```json
{"schema": "pcb-cpp-sanitizer-v1", "report_version": 1, "detector": "asan",
 "verdict": "candidate-defect", "inconclusive": false, "candidate_defect": true,
 "exit_code": 1, "timed_out": false,
 "diagnostics": [{"kind": "heap-use-after-free", "message": "...",
                  "path": "src/top_words.cpp", "line": 21, "column": 5}],
 "stdout_digest": "sha256:...", "stderr_digest": "sha256:...",
 "stdout_tail": "...", "stderr_tail": "..."}
```

The module also exports `classify(text, exit_code, timed_out, detector)` so the host parser and
its tests reuse exactly the classification the guest ran.

| verdict | when |
|---|---|
| `clean` | exit `0` and no sanitizer banner |
| `candidate-defect` | a real ASan/LSan/UBSan/TSan report naming a candidate file |
| `unsupported` | the runtime refused to run (shadow-memory/loader/signal-handler failures) |
| `failed` | crashed, aborted or timed out without a sanitizer verdict |

## Task package layout

```
manifest.yaml
visible/task.md
visible/repo/include/top_words.hpp        # stub the candidate replaces (required output)
visible/repo/src/top_words.cpp            # stub the candidate replaces (required output)
hidden/oracle.json                        # test inventory, hard conditions, robustness scenarios
hidden/quality-plan.yaml                  # opportunities, analyzers, instrumentation, performance
hidden/recipe.json                        # pinned compiler/standard/build-profile recipe
hidden/tests/behaviour.cpp                # hidden acceptance group
hidden/tests/stress.cpp                   # hidden quality-only group
hidden/reference/include/top_words.hpp    # reference solution
hidden/reference/src/top_words.cpp
hidden/perf/workload.cpp                  # optional performance workload
admission/<variant>/...                   # candidate variants used by admission
admission/exposure-rights.json
```

`hidden/recipe.json`:

```json
{"schema_version": 1, "kind": "cpp_task_recipe", "recipe_version": "cpp-recipe-v1",
 "compiler": "clang", "standard": "c++20", "build_profile": "debug",
 "include_dirs": ["include"], "link_flags": []}
```

`hidden/quality-plan.yaml` (top-level keys):

```yaml
instrumentation: supported | unsupported   # default supported
sanitizers: [address, undefined]           # or [thread]; must be pairwise compatible
opportunity_tags: [ownership, stl, moves]
opportunities: {raii_ownership: 2, ...}
required_analyzers: [clang_tidy, cppcheck, context]
dependency_inventory: []
performance: {...}                         # same shape as the Rust quality plan
judge_items: [naming_readability, decomposition]
```

## Verification

```bash
.venv/Scripts/python.exe scripts/cpp_conformance.py --report docs/implementation/evidence/prompt-21-conformance.json
.venv/Scripts/python.exe -m pytest tests/test_cpp_plugin.py tests/test_cpp_parsers.py tests/test_cpp_profile.py -q
```

See `docs/implementation/reports/prompt-21.md` for the recorded run.
