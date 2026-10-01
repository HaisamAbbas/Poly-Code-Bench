# Python language plugin (Prompt 10)

The Python plugin implements the `LanguagePlugin` contract of Technical Spec 18.1 on top of the
shared extension interfaces in `packages/plugins-api`. A plugin only **describes** work; a trusted
supervisor (`packages/evaluation`) runs it through a `SandboxProvider`, and the plugin then **reads
back** recorded bytes. Nothing in the plugin starts a process.

| Piece | Location |
|---|---|
| Typed plans, reports, profile, registry, test-evidence contract | `packages/plugins-api` |
| Plugin, plans, parsers, profile, task rules, guest tooling, rule bundle | `plugins/languages/python` |
| Plan runner and executable admission engine | `packages/evaluation` (`plan_runner.py`, `suite_admission.py`) |
| Images, locks, build script | `infra/images/python`, `scripts/build_python_images.py` |
| Image/rule identities, plugin allowlist, profile | `config/images/python-v1.json`, `config/plugins/allowlist-v1.yaml`, `config/languages/python-profile-v1.yaml` |
| Authoring/admission tool, conformance run, pilot inventory | `scripts/python_task_tool.py`, `scripts/python_conformance.py`, `scripts/python_pilot_inventory.py` |
| Authoring contract and worked fixture | `plugins/languages/python/AUTHORING.md`, `plugins/languages/python/fixtures/top-words/` |

## Images (PCB-10-1)

Two images are built from the pinned `python:3.12-slim` digest with `docker build --network none`:

* `pcb-python-runtime` - Python, pytest, Hypothesis, and the trusted guest scripts. Build and test
  plans run here (the same image is what solve guests use).
* `pcb-python-evaluator` - the runtime plus Ruff, mypy, Bandit and Semgrep.

Every package comes from a hash-verified local wheelhouse that was filled once, before the build,
from `infra/images/python/*.lock` (`uv pip compile --generate-hashes`). The build installs with
`pip --no-index --require-hashes`, then compares the installed set to the lock. Plans declare
`network: none`, contain no installer, and the guest cannot reach a package index, so a scored run
performs no online installation. `config/images/python-v1.json` records the base digest, image
digests, Python build, tool versions, lock digests, the rule-bundle digest and the guest-script
digest; every `ToolIdentity` in a plan is derived from it. A rebuild changes layer timestamps and
therefore the image digest: reseal task manifests and re-run admission after any rebuild.

Images are local development builds. Registry publication and production-worker pinning belong to
the deployment prompts; production isolation is the owner-deferred Prompt 06 gate.

## Plans and parsers (PCB-10-2)

* **Build** - `pcb_syntax_check.py` compiles each required output without writing bytecode.
* **Tests** - one plan per oracle group runs pytest with the trusted `pcb_pytest_report` plugin
  (derandomized Hypothesis profile with a fixed example count and no database, per-case alarm,
  JSON-line case/control records). Hidden tests arrive only as `overlay` inputs, so a candidate can
  never supply them. `testparse.py` + `plugins_api.testreport.reconcile` decide the gate from the
  declared inventory: missing, skipped or xfailed required cases are never passes, candidate
  failures (assertions, case timeouts, candidate import errors, hung or OOM-killed runs) are kept
  apart from harness failures (no control evidence, internal pytest errors, errors in hidden test
  modules, unknown records) which make the evaluation *incomplete*.
* **Analyzers** - Ruff, Bandit, Semgrep (selected `pcb.python.*` rules), the AST context scanner,
  mypy (`--strict` only when the task contract expects types) and, when a task declares
  dependencies, an offline advisory check. Each parser starts from the supervisor's execution
  record and then validates the tool's own report: a timeout, a crash, an undeclared exit, a missing
  or invalid report, uncovered scope, tool-reported analysis errors, or a findings exit without
  findings (Bandit exits 1 for both a finding and an uncaught crash) all yield a single
  `python.<tool>.scan` observation with status `missing`. Only a completed scan that proves itself
  is `measured` with its finding count; empty output is "clean" only under the tool's exit-code
  contract (mypy also requires empty stdout/stderr).
* **Performance** - `performance_plan` declares the workload contract (three scales, 5 warmups, 20
  measured iterations, single thread, nanosecond time and process peak RSS) and a one-iteration
  fresh-process driver. Admission runs the smallest workload once on the reference as a smoke
  check. Paired measurement, canaries and ratios are the Prompt 13 stage.
* **Symbols** - static `ast` index; no import or execution.

## Profiles (PCB-10-3)

`python-profile-v1` takes its weights from `config/languages/profiles-v1.yaml`
(readability/idioms 25, type hints 15, stdlib 15, error handling 15, lint 10, performance
awareness 20; idiom items iteration/laziness 30, stdlib/API choice 30, data/protocol modeling 25,
context/resource abstraction 15). `python-profile-v1.yaml` adds rule mappings (exact or prefix),
reviewed cross-tool equivalence families, applicability rules and ownership
(`canonical-security-issue` -> security, `resource-cleanup-failure` -> robustness, others
diagnostic-only).

* **Context, not syntax.** A mutable default is a violation only if the function mutates it or lets
  it escape (a defensive rebind first, or read-only use, is recorded `benign_in_context` and not
  penalised); Ruff's B006 and the scanner describe one canonical issue and the scanner decides.
  Missing annotations count only when the task contract expects types; mypy is then required.
* **No bonus, no duplicate penalty.** Scores come from the task's frozen opportunity count, not
  from constructs found in the candidate; violations are unique canonical issue keys, capped at the
  opportunity count; one issue reported by several tools is merged first.
* **N/A is not perfect.** Items with no opportunity are `not_applicable`; a failed required scan
  makes dependent items `missing` and withholds the aggregate.

All profile parameters are pilot values (`effective_for_scoring: false`).

Two idiom rules were extended during Prompt 10 admission (D-10-03) because the authored
anti-pattern fixtures proved the original detectors were too narrow, not because the fixtures were
weakened. Both are one canonical family each and are covered by regression tests:

| Rule | Before | Now |
|---|---|---|
| `manual-counter` | only `d[k] = d.get(k, 0) + v` | also `count = 0` + `for _ in items: count += 1`, i.e. `len(items)`. Fires only when the loop discards its target and its entire body is the increment; a loop that reads the item, or does other work, is left alone. |
| `string-concat-in-loop` | only a literal or f-string RHS | any provably textual RHS: literals, f-strings, `str()`/`repr()`/`format()`, `str.join`, and concatenation of textual operands (so `text += str(part) + "."` is caught). Numeric and unproven expressions are still ignored. |

All twelve pilot reference solutions were re-scanned after the change and remain free of both
families, so the widened rules did not manufacture findings on known-good code.

## Task packages and admission (PCB-10-4)

See `AUTHORING.md`. A suite-mode package declares reference / faulty / alternative /
quality-defective / timeout variants and an exposure-rights record; the hidden oracle lists every
test case. `python scripts/python_task_tool.py admit PACKAGE --report R.json` imports the package
with the safe importer, validates it with the plugin, and **executes** each variant: five identical
reference runs, declared faulty cases failing, the alternative passing, the quality-defective variant
passing the gate while analyzers report its defect, timeouts stopped and classified as candidate
failures, required analyzers completing on the reference, and the workload smoke run.

A passing report is **executable admission only** (`quality_admission: pending`). It lists the
gates that remain: generic evaluator integration (Prompt 12), performance baseline/canary (13), judge
anchors and calibration (14), scoring replay (15), a production execution tier, curator approval and
freeze, and owner rights confirmation. Pilot task packages are private assets under
`.protected/` (git-ignored); the committed `taskpacks/python-pilot/inventory.yaml` carries only
identities, digests and status.

Batch runs:

```text
python scripts/python_task_tool.py seal-all      # reseal every pilot package (after a rebuild)
python scripts/python_admit_all.py               # execute admission for all 12 clusters
python scripts/python_pilot_inventory.py --protected .protected/taskpacks/python-pilot \
    --reports .protected/reports --output taskpacks/python-pilot/inventory.yaml
```

`seal-all` and `admit` refuse to proceed when a package still contains a tool cache directory
(`.mypy_cache`, `.ruff_cache`, `.pytest_cache`, `.hypothesis`, `__pycache__`). A cached analysis
directory is not task content: sealing one into `hidden/reference/` makes the reference's own
analysis state part of every candidate submission and breaks the output contract. Removing the
caches from the two affected packages is what fixed `archive-path-guard`'s
`output-contract-reference` failure; the guard exists so the same authoring slip cannot return
unnoticed.

## Extending the manifest

`TaskPackageManifest` (Prompt 05) now accepts suite-mode fixtures (no stdin/stdout pair, an
`expectation` instead) and the `quality_defective` and `timeout` variants. Stdio packages are
unchanged and a package uses one mode only.
