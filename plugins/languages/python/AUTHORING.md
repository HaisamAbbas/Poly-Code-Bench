# Authoring a Python pilot task package

Audience: an author writing one independent Python code-generation task cluster. A package is
judged only by what *runs*: your reference, faulty, alternative, quality-defective and timeout
solutions are executed in the Docker sandbox and must behave exactly as declared.

Worked example (complete, passes admission): `plugins/languages/python/fixtures/top-words/`.
Read its `manifest.yaml`, `hidden/oracle.json`, `hidden/quality-plan.yaml` and tests first.

## Commands (run from the repository root)

```
uv run --offline python scripts/python_task_tool.py seal     PACKAGE     # fill computed fields
uv run --offline python scripts/python_task_tool.py validate PACKAGE     # importer + plugin checks
uv run --offline python scripts/python_task_tool.py admit    PACKAGE --report REPORT.json
```

`seal` rewrites `manifest.yaml` (file lists, digests, runtime image); run it after every edit.
`admit` takes several minutes. Read `REPORT.json` (`runs[].gates/failing_cases/reasons/
issue_families`) to see what really happened, then adjust. Never edit the report.

## Package layout

```
manifest.yaml                 task identity, output contract, acceptance, fixtures (see example)
visible/task.md               statement given to the model: behaviour, edge rules, constraints
visible/repo/solution.py      stub scaffold given to the model (signature only)
visible/tests/test_public.py  2-3 public example tests (feedback tests; keep few and simple)
hidden/oracle.json            the complete inventory of hidden test cases (see below)
hidden/quality-plan.yaml      typing expectation, opportunities, analyzers, workload
hidden/tests/test_*.py        hidden tests (import the candidate with: from solution import ...)
hidden/reference/solution.py  your reference implementation
hidden/perf/workload.py       prepare(scale, seed) / run(module, data) / verify(data, result)
admission/<variant>/solution.py   faulty / alternative / quality-defective / timeout solutions
admission/exposure-rights.json    authorship, exposure and rights record (see below)
```

Output contract: one file `solution.py` (`allowed_paths: [solution.py]`).

## Hard requirements

1. **Original problem.** Do not reproduce HumanEval, MBPP, LeetCode, Exercism, Advent of Code,
   textbook or well-known library problems. Invent a small specification with your own rules and
   edge cases; state every rule the tests rely on in `visible/task.md`. Hidden tests must not test
   anything the statement does not specify.
2. **Independent tests.** Hidden tests must not call or copy the reference. Use hand-computed
   examples plus property tests (Hypothesis) checked against a *differently written* model inside
   the test file. No wall-clock timing assertions, no randomness without a fixed seed, no network,
   no files outside a `tmp_path`, no dependencies beyond the standard library and pytest/hypothesis.
3. **Inventory = tests.** `hidden/oracle.json` lists every test function (node ids like
   `tests/test_x.py::test_name`, `Class::method` for classes). `validate` fails on any difference.
   Parametrized tests are listed once by base name. Groups named in
   `acceptance.required_test_group_ids` are required; extra groups with `classification:
   quality_only` and `required: false` hold robustness scenarios that do not gate correctness.
4. **Variants** (all declared under `fixtures:` with an `expectation`):
   * `reference` - passes everything; five runs must give identical outcomes.
   * at least two `faulty` variants - plausible code with a *specific* bug each (an edge case,
     off-by-one, wrong tie-break, ...). Declare `failing_cases` (cases that must fail); the variant
     must be rejected by the required groups. Do not make them fail everything.
   * at least one `alternative` - a genuinely different valid algorithm/structure; must pass.
   * at least one `quality_defective` - passes every required test but contains a real defect the
     analyzers detect. Declare `expected_issue_families` (read the families from your first
     admission report: `runs[].issue_families`, e.g. `mutable-default`, `bare-except`,
     `manual-counter`, `range-len-index-loop`, `string-concat-in-loop`, `try003`, `subprocess-shell`).
   * at least one `timeout` - passes cheap cases but hangs on a specific input; declare
     `expected_failure: candidate_timeout`. Keep `case_timeout_seconds` (<= 10) and
     `suite_timeout_seconds` (<= 60) small so admission stays fast.
5. **Quality opportunities are honest.** `quality-plan.yaml` counts an opportunity only if the
   task genuinely lets a solution show (or miss) that practice. `opportunities` keys are the
   diagnostic items `readability_idioms, type_hints, stdlib_use, error_handling, lint_style,
   performance_awareness` and idiom items `iteration_laziness, stdlib_api_choice,
   data_protocol_modeling, context_resource_abstraction`. Items with no opportunity are omitted.
   `typing_expectation: required` makes mypy `--strict` a required analyzer (the reference must
   then be clean under it); otherwise use `none` and do not list `type_hints`.
   Applicable dimensions in `manifest.yaml` (`quality_plan.applicable_dimensions`) must be backed
   by evidence: `security` needs the tag `security_surface` in `opportunity_tags`; `efficiency`
   needs a `performance` workload (three scales, weights summing to 10000); `robustness` needs at
   least one robustness scenario in the oracle; `idiomatic` / `code_quality` need opportunities.
6. **Exposure and rights record** `admission/exposure-rights.json`, honest and complete:
   `{"authorship": "...", "originality_statement": "...", "first_public_at": null,
   "public_exposure_review": "not performed against external corpora" (say what you did do),
   "access_history": [{"party": "...", "access": "...", "date": "2026-10-01"}],
   "provider_transmission_policy": "visible bundle only",
   "rights": {"license_expression": "CC0-1.0", "status": "authored_fixture",
   "owner_confirmation": "pending"}}`. Do not claim reviews or approvals that did not happen.
7. **Seal, validate, admit** must all exit 0 for the final package. Report any check you could not
   satisfy instead of weakening a test, a variant expectation or an analyzer requirement.

## Available detectors (for quality-defective variants)

Context scanner rules: `mutable-default-shared`, `missing-annotation` (typing required),
`aggregate-over-list-comprehension`, `range-len-index-loop`, `readlines-loads-everything`
(tag `streaming`), `manual-counter`, `string-concat-in-loop`, `sequence-concat-in-loop`,
`membership-in-list-inside-loop`, `bare-except`, `swallowed-broad-exception`,
`generic-exception-raised`, `assert-validation`, `open-without-context-manager`,
`lock-acquire-without-release-guard`. Ruff families (PERF, SIM, C4, B, UP, RET, BLE, TRY ...),
Bandit and the Semgrep rule set (`subprocess-shell-true`, `eval-or-exec`, `unsafe-deserialization`,
`hardcoded-credential`, `path-traversal-join`, `tarfile-extractall`, `insecure-temp-file`,
`weak-hash`) and mypy errors cover the rest.

## Where to put it

Pilot packages are private task assets: create them under
`.protected/taskpacks/python-pilot/<task-slug>/` (git-ignored). Never copy hidden or admission
files into `visible/`, into docs, or into any committed path.
