# Prompt 22 — Add Go support

## What was implemented

The Go plugin, its pinned toolchain and its task package existed, but **no Go task could ever
execute**. Every fixture variant reported `build:build harness error (tool_error)`. That single
environmental defect was the whole blocker; four more defects surfaced once it was removed.

### The decisive defect: a build cache inside a read-only image

The plans set `GOCACHE=/opt/pcb/cache/go-build`. The sandbox runs each guest with `--read-only`
as uid 65532, which owns only `/workspace` and `/tmp`, so:

```
failed to initialize build cache at /opt/pcb/cache/go-build: mkdir ...: read-only file system
```

`go build` exits 2 before compiling anything. Exit 2 is a declared `TOOL_ERRORS` code, so the
supervisor classified it as a *harness* error and `parse_build` returned `incomplete` for all six
variants. The asymmetry in the prior report is the proof: `go.gofmt.scan` and `go.context.scan`
were `measured` because they need no build cache, while `go.vet`, `go.staticcheck` and `go.gosec`
were `missing` because they load packages through it.

Fixed by moving `GOCACHE`, `GOMODCACHE`, `GOPATH` and `GOTMPDIR` under the workspace tmpfs.
`GOTMPDIR` is included because the compile work directory otherwise defaults to `/tmp`, which the
sandbox caps at 16 MiB — an `ENOSPC` there is indistinguishable from a candidate build error. The
guest runner creates all of them, because Go creates three itself but *requires* `GOTMPDIR` to
exist.

### Four further defects

1. **Performance recipe could not exclude instrumentation.** The build's distinctness gate
   demanded the performance image be unable to run `go test -race`; it can, because the detector
   ships inside the pinned `golang` base. Demanding a missing feature would mean shipping a
   different toolchain to obtain a property the scoring rule cares about. Go now records
   `instrumentation` and `accepts_instrumented_plans` per recipe — the arrangement the C sanitizer
   runtimes already use — with reciprocal guards: `performance_plan` calls
   `require_release_recipe("performance")`, and the race plan calls
   `require_instrumented("runtime")`. The second guard is what stops a false *clean*: a race plan
   aimed at a recipe with no detector would report `clean` for a race never looked for.
2. **staticcheck reported `missing` on a correct candidate.** It keeps a fact cache under
   `$XDG_CACHE_HOME`, which resolved to the unwritable root. Every plan now pins it into the
   workspace. `HOME` was rejected — `ExecRequest.safe_environment` protects it deliberately — so
   the supported lever is used.
3. **gosec reported `missing` on a correct candidate — clean code was penalised.** `gosec -out`
   writes its report *only when it has a finding to report*; a clean run leaves the path
   non-existent, which the parser (correctly refusing to treat an absent report as clean) called
   `missing`. gosec writes JSON to stdout, so the plan captures that stream and `-out`/`-quiet` are
   dropped. Clean is now an observable empty `Issues` list rather than an absence.
4. **Go's hidden tests were never staged.** A Go `_test.go` file must be compiled as part of the
   package it exercises, so the suite lives at `hidden/topwords/`, not `hidden/tests/`. The shared
   `SuiteAdmission` hardcoded the Python layout and raised `PlanInputError` for every variant. The
   engine now reads `overlay_roots`/`overlay_suffixes` off the plugin — following the existing
   `overlay_prefix`/`candidate_suffixes` extension points — rather than growing a per-language
   branch.

Additionally: `build_go_images.py` now refreshes the Go allowlist entry, which every other
language builder already did and Go did not — a rebuild previously left every Go plan unrunnable.
Dead constants (`RACE_ENV`, `_MODULE_FILES`, `_BUILD_ERROR_AT`) were removed and the documented
`MAX_PLAN_SECONDS` invariant is now enforced in `resources()` rather than stated in a comment.

## Changed paths

| Path | Change |
|---|---|
| `plugins/languages/go/src/polycodebench_lang_go/plans.py` | Workspace cache paths incl. `GOTMPDIR`; `XDG_CACHE_HOME`; gosec reads stdout; both recipe guards wired; `MAX_PLAN_SECONDS` enforced |
| `plugins/languages/go/src/polycodebench_lang_go/identities.py` | `instrumentation` / `accepts_instrumented_plans` fields; `require_release_recipe`, `require_instrumented` |
| `plugins/languages/go/src/polycodebench_lang_go/parsers.py` | gosec parses `out/gosec.out` |
| `plugins/languages/go/src/polycodebench_lang_go/guest/pcb_go_run.py` | Creates `GOCACHE`/`GOMODCACHE`/`GOPATH`/`GOTMPDIR`/`HOME`/`XDG_CACHE_HOME` |
| `plugins/languages/go/src/polycodebench_lang_go/plugin.py` | `overlay_roots`, `overlay_suffixes`; dead constants removed |
| `packages/evaluation/src/polycodebench_evaluation/suite_admission.py` | `overlay_roots`/`overlay_suffixes` hints (defaults unchanged for other languages) |
| `infra/images/go/recipes.yaml` | `instrumentation` and `instrumented_recipes` declarations |
| `scripts/build_go_images.py` | Records instrumentation; gates on declaration not capability; refreshes the allowlist |
| `scripts/go_quick_check.py` | Stages the variant (was overwriting it with the visible stub); passes the plan env; fixed the `context` invocation |
| `scripts/go_conformance.py` | Every instrumented flag and the recipe guard asserted; `_concurrent_view` syncs `required_analyzers` in the quality plan |
| `tests/test_go_docker.py` | **New.** Real-sandbox E2E-15/35 evidence; previously referenced by two test modules and `language-coverage.md` but absent |
| `config/images/go-v1.json`, `config/plugins/allowlist-v1.yaml`, `.../top-words/manifest.yaml` | Rebuilt identities; digests resealed |

## Executable admission result

`docs/implementation/evidence/prompt-22-go-admission.json` — `executable_admission_passed: true`,
24/24 checks, report digest `sha256:3a2d72e6571a7d577bb509ce24660b56276e6e9a3d828f0682c550c97100b92b`,
development sandbox tier, images `441055d335cf` (runtime) and `32e6d0fbe6e3` (evaluator).

| Variant | Gates | Repetitions | Issue families observed |
|---|---|---|---|
| reference | pass ×5 | 5 | none |
| faulty-ties | fail | 1 | none |
| alternative-heaps | pass | 1 | none |
| quality-defective | pass | 1 | error-chain, error-sentinel, goroutine-lifecycle, ignored-error, string-building |
| timeout-case | fail | 1 | none |
| race-defective | pass | 1 | string-building |

The reference and the alternative-valid implementation report **no** issue families at all. That is
the PCB-22-4 DoD stated as data: correct code is not penalised, and the two defective variants are
separated from them by exactly the families they were written to carry.

`quality_admission` is `pending`, as it is for every language: it waits on the Prompt 12/13/14/15
stages and on curator approval, not on anything in this prompt's scope.

## Other evidence

- `docs/implementation/evidence/prompt-22-go-conformance.json` — seven conformance categories plus
  the instrumentation-lane case.
- Audit status (2026-10-05): the latest saved conformance artifact is 17/18, `passed: false`,
  digest `sha256:667ca47e3b463859198c89055264e9fb9908a56ba45cbdb752d483f3fcf0ecdc`. Its sole
  failure is the benign sample candidate gate (`gate=fail score=10000 measured=[]`). The earlier
  failure from calling `Close` on `strings.Reader` remains preserved in
  `prompt-22-go-conformance-first-attempt.json`; the changed sample has no saved passing rerun.
- `tests/test_go_plugin.py` `test_a_clean_staticcheck_run_is_measured_zero_not_missing` and
  `test_an_analyzer_that_died_before_reporting_is_still_missing` — mutation-checked: removing the
  `empty_report` handling from the parsers makes the first fail.
- `tests/test_go_docker.py` — opt-in (`PCB_TEST_DOCKER=1`) real-container evidence.
- `docs/implementation/decisions.md` — D-22-01 … D-22-13.

### A seventh defect: line endings decided the score

Every variant reported a `formatting` finding. `gofmt -d` on the reference showed the whole file
rewritten: the working tree had checked the sources out as CRLF, and `gofmt` accepts LF only. Since
`gofmt` is a scored required analyzer, a developer's `core.autocrlf` setting silently decided that
the *reference solution* was misformatted. Fixed with a `.gitattributes` (`*.go`, `*.mod`, `*.sum`
as `eol=lf`) plus normalising the ten checked-in fixture files; `.gitattributes` alone would only
have fixed future checkouts. The Go sources embedded in `scripts/go_conformance.py` are Python byte
literals and inherit the same trap, so `go_source()` normalises them — otherwise the clean-sample
case fails on a CRLF checkout, which is the exact false positive it exists to detect.

## Known limitations

- `gosec --version` reports `Version: dev` for a module install, so the recorded evaluator identity
  is not the pinned `v2.29.0` the recipe declares. The pinned spec *is* recorded in
  `config/images/go-components.json`; this is a gosec reporting defect, not a build one.
- Development-tier only: every result comes from the local Docker driver, not a production worker.
