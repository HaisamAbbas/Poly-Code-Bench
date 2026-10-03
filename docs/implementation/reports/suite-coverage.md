# Track B suite coverage (Prompt 28, PCB-28-4)

## What this report covers

Every required Track B family, audited through the entrypoint that actually grades it. The
authoritative verdict is machine-produced:
`uv run python plugins/suites/swebench/scripts/prompt28_evidence.py` emits
`docs/implementation/evidence/prompt-28-e2e38.json`, whose `track_b_coverage` block carries the
per-family verdict and the `wp20_closed` flag. Nothing in the table below was written without that
run behind it.

| Family | Solve behavior | Grader entrypoint | Admitted pack | Evidence | Verdict |
|---|---|---|---|---|---|
| `codegen` | solve via single-shot or standard agent; graded by the language plugin's test plan | `polycodebench_evaluation.suite_admission.SuiteAdmission.admit` | `plugins/languages/python/fixtures/top-words` | `evidence/prompt-12-eval-python.json` | passed |
| `repo_repair` | native record import; resolution graded by the pinned upstream evaluator | `polycodebench_suites_swebench.overlay.grade_native_candidate` | `plugins/suites/swebench/tests` (authored instances) | `evidence/prompt-24-e2e36.json` | passed |
| `repo_task` | authoring contract with sealed acceptance; executable criteria plus bounded rubric | `polycodebench_evaluation.repo_task_grading.grade_repo_task` | `taskpacks/repo-tasks/ini-interpolate` | `evidence/prompt-25-admission-ini-interpolate.json` | passed |
| `self_repair` | fixed rounds on public feedback only; initial/final outcomes preserved | `polycodebench_evaluation.suite_admission.SuiteAdmission.admit` (the final round is a frozen candidate; self-repair has no separate grader) | `taskpacks/self-repair/py-listsort-v1` | `evidence/prompt-26-e2e-37.json` | passed |
| `repo_qa` | answer with base-snapshot citations; fact recall with grounding diagnostics | `polycodebench_evaluation.qa_grading.grade_qa` | `packages/core/src/polycodebench_core/qa_contracts.py` | `evidence/prompt-26-e2e-37.json` | passed |
| `output_prediction` | read code and input; predict output; execution tools disabled | `polycodebench_suites_swebench.prediction_grading.grade_prediction_task` | `taskpacks/prediction/output-prediction` | `evidence/prompt-28-e2e38.json` | passed |
| `test_prediction` | predict a declared test's result under scenario constraints | `polycodebench_suites_swebench.prediction_grading.grade_prediction_task` | `taskpacks/prediction/test-prediction` | `evidence/prompt-28-e2e38.json` | passed |

**WP-20 is closed by the run, not by this table**: the audit refuses to build a verdict that omits
a family, follows the grader entrypoints by import, and fails a family whose pack or evidence path
is absent from the tree.

## What Prompt 28 added

- `polycodebench_suites_swebench.prediction` — the answer-only contracts: frozen normalization
  (`exact_bytes`, `normalized_text`, `typed_json`), the execution-tool refusal, the one-pass
  submission parser and the total deterministic comparison.
- `polycodebench_suites_swebench.prediction_grading` — `grade_prediction_task` (bound, reproducible
  verdicts) and `audit_track_b` (the coverage verdict).
- `config/protocols/prediction-v1.yaml` — the frozen prediction cohort: zero tools, zero tool calls,
  one model turn, no feedback, network disabled. The restriction is structural, so a different tool
  policy is a different cohort rather than a softer one.
- `packages/core/src/polycodebench_core/prediction_prompts.py` — `pcb-prediction-v1`: the
  deterministic rendering of a prediction run's system and task messages.
- Two admitted task packs under `taskpacks/prediction/`, both labelled `adapted` (authored
  LiveCodeBench-inspired adaptations per `docs/methodology/livecodebench.md`, consistent with the
  deviations register).

## Deterministic grading, verified behaviour

All 16 graded cases in `evidence/prompt-28-e2e38.json` come from the run:

- `exact_bytes` compares characters and nothing else; the mode declares no rules, and a rules
  document that contradicts its mode is refused rather than defaulted.
- `normalized_text` applies exactly its declared rules: declared whitespace collapse makes two
  run-shapes match, and a different digit still fails.
- `typed_json` honours its declared key-order rule and numeric tolerance: reordered keys match,
  `41` against `40` under a zero tolerance does not.
- A parse error is a wrong answer (`parse_error: ...` in the verdict reason), not a retry.
- A judge cannot rescue a mismatch: the grade report has no judge field at all.

## Scope limits (read before citing)

- **No live model.** The graded submissions are the fixtures' authored answers; this evidence shows
  the grader's behaviour, not model skill.
- **Local fixture tier.** No production-worker run.
- **`repo_qa` evidence pointer is provisional.** PCB-27 is still `not_started` in the tickets; the
  audit points at Prompt 26's E2E-37 artifact because Q&A's grading module already exists, and a
  dedicated Q&A evidence file must replace that pointer when Prompt 27 completes.
- **Quality admission stays pending** for both prediction fixtures, as for every other Phase 5
  pack.
- Prompt 27's Q&A modules were present in the tree uncommitted while this prompt ran; the audit
  imports them as they stand and does not commit or claim that work.

## Prior evidence, preserved

Prompt 24, 25 and 26 evidence is reused as recorded rather than re-run: E2E-36
(`evidence/prompt-24-e2e36.json`), the repo-task admissions (`evidence/prompt-25-*.json`) and
E2E-37 (`evidence/prompt-26-e2e-37.json`). No new run of those scenarios is claimed here.
