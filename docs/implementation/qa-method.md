# Repository Q&A methodology: fact recall, citations and the fixture pack

Prompt 27 (PCB-27-1..4, WP-20 part 4). This document is the frozen description of how
PolyCodeBench's repository question-and-answer tasks are asked, answered and graded, and where
their methodology boundary lies.

## Methodology boundary (PCB-27-4)

The native reference is the DeepCodeBench Q&A methodology (`docs/methodology/deepcodebench.md`):
PR-context repository Q&A, discrete ground-truth facts, LLM presence check, and fact recall as the
native focus. The fixture pack `taskpacks/qa/py-configkit-qa-v1` is an **authored
DeepCodeBench-inspired adaptation** labelled `inspired` - never a native reproduction. No official
DeepCodeBench task, dataset item or score is used and none is comparable. The tasks themselves are
independently curated by PolyCodeBench: one authored developer question over an authored,
pinned `cfgkit` snapshot, an authored atomic-fact oracle, and authored answer variants. No
DeepCodeBench dataset file was downloaded or copied into this workspace.

Labels are a provenance record, not a ranking: `native` is reserved for imported upstream benchmark
records, `adapted` for modified upstream tasks, `inspired` for independently curated tasks shaped
after a public methodology description. This pack is `inspired` (manifest `methodology_label`,
`stratum_id: deepcodebench-inspired`), and the inspiration statement is validated against exactly
the claims above.

## Measures (Technical Spec 17.3)

- **Fact recall is preserved as the native-style measure**:
  `100 * sum(weight * credit) / sum(weight)` over the atomic facts. Missed facts are zero, repeats
  add no credit, an empty answer recalls zero, and contradictions get no credit for the
  contradicted fact. Credit is the mean of the fixed three-vote entailment protocol (or an
  adjudication), and a deterministic factual contradiction forces zero.
- **The native presence aggregation is preserved separately** (statement or accepted-paraphrase
  presence with the same formula), so a difference between the two aggregations stays visible
  rather than silently replaced.
- **Grounding rate and the unsupported/contradicted claim diagnostics are SEPARATELY NAMED
  PolyCodeBench adaptations, never DeepCodeBench metrics.** Citation validity, evidence grounding
  and unsupported-claim counts are reported alongside fact recall and are never folded into it.
- **Incomplete verification yields `unknown`, never zero.** A missing entailment or support
  judgment makes the affected diagnostic `unknown` (None) rather than a silent zero or a
  hallucination count.
- **No six code dimensions and no invented answer-quality index.** Q&A output is prose with
  citations: the grade carries `code_dimensions: not_applicable`, the task declares no applicable
  code dimensions, and prose receives no invented security, runtime or idiom scores.

## Protocol record (PCB-27-1)

Solving runs under `config/protocols/repo-qa-v1.yaml` (`protocol_id: repo-qa-v1`):

- The question and the repository snapshot are pinned inputs; every citation references the
  pinned base snapshot digest and is validated against that snapshot only (path present, span
  inside the file, recorded digest equal to the pinned digest).
- Retrieval is read/search-only: `list_files`, `read_file`, `search`. Editing tools are a protocol
  violation (Technical Spec 17.2: editing is off for Q&A).
- Budget: 20 model turns, 60 tool calls, 600 seconds wall clock. `public_test_feedback: false`,
  `hidden_feedback: false`, network disabled.
- The submission is one JSON envelope and nothing else:
  `{"answer": ..., "claims": [{"text": ..., "citations": [{"path", "start_line", "end_line", "base_digest"}]}]}`.
  Each claim's `text` must quote the answer text; malformed or ambiguous envelopes are refused,
  never repaired. Retrieval context and truncation are logged (`RetrievalRecord`).
- The task package's output contract is `submission_kind: typed_json` with `allowed_paths:
  [answer.json]`, and the acceptance gate is the required test group `qa-facts-v1` (expressed and
  grounded fact evidence).

## Oracle and fact evidence (PCB-27-2)

The hidden oracle (`oracle.test_version: qa-oracle-v1`, `hidden/oracle.json`,
`oracle_id: configkit-qa-v1`, `version: 1`) freezes the ground truth as versioned atomic facts:
`fact_id`, predeclared `weight`, `statement`, `accepted_paraphrases`, `verifying_spans` (code spans
tied to the pinned base digest) and `exact_values`. All fact evidence is versioned: the oracle
identity and version, the fact identities and weights, and every verifying span with its base
digest. Entailment judging runs through the fixed three 0/1 votes (or an adjudication); the judge
sees the stated fact and the candidate text only - never the oracle weight, answer identity or a
score expectation.

## The fixture pack: `taskpacks/qa/py-configkit-qa-v1`

One cross-file developer question over the authored `cfgkit` snapshot (four files across three
modules: `cfgkit/loader.py`, `cfgkit/interpolate.py`, `cfgkit/errors.py`, `cfgkit/__init__.py`).
The oracle holds three atomic facts (weights 1 + 1 + 2 = 4): `load-resolves-env-references`
(loader.py:29-43), `escaped-reference-stays-literal` (interpolate.py:19-21, exact value
"1 backslash"), and `missing-variable-raises-config-error` (interpolate.py:22-23, exact value
"ConfigError"). The variant matrix (`admission/<variant>/answer.json`, suite-mode fixtures with
predeclared expectations):

| Variant | Role | Expected outcome |
|---|---|---|
| `reference` | reference | All facts in exact wording, each claim on its verifying span: fact recall `100.000000`, citations valid. |
| `alternative` | alternative | All facts in accepted-paraphrase wording, same correct spans: the same `100.000000`. Wording never changes the score. |
| `wrong-citation` | faulty | All three facts expressed, but every citation is a wrong span and is flagged invalid: the facts are expressed while the asserted citations support nothing (grounding zero/unknown). Declares `failing_cases: [qa-facts-v1]`, `expected_failure: wrong_behavior`. |
| `contradiction` | faulty | States the factual contradiction "a missing variable is silently ignored" (with a numeric escape contradiction for the deterministic `contradiction-v1` check) plus one correct fact: the contradicted fact earns no credit (`fact (c)` stays unexpressed under entailment). Declares `failing_cases: [qa-facts-v1]`, `expected_failure: wrong_behavior`. |
| `empty` | faulty | `{"answer": "", "claims": []}`: fact recall `0.000000`, claim precision undefined, grounding unknown. Declares `failing_cases: [qa-facts-v1]`, `expected_failure: wrong_behavior`. |
| `repeated` | quality_defective | All three facts with fact (a) asserted three times, each on correct spans: recall identical to the reference - repetition adds no credit. Declares `expected_issue_families: [repetition]`, `expected_quality_only_pass: true`. |

## Native versus inspired: what carries over and what does not

| Aspect | Native DeepCodeBench Q&A (as published) | This fixture (labelled `inspired`) |
|---|---|---|
| Task origin | PR-context questions over eight upstream repositories | Authored developer question over the authored `cfgkit` snapshot |
| Ground truth | Discrete facts extracted from ground-truth answers | Versioned atomic-fact oracle (`qa-oracle-v1`): weights, accepted paraphrases, verifying spans pinned to the base digest |
| Presence check | LLM call checks fact presence in predictions | Preserved native-style presence aggregation, recorded separately from entailment credit |
| Primary measure | Fact recall as the native focus | Fact recall preserved as the native-style measure, same formula `100 * sum(w * credit) / sum(w)` |
| Additional measures | None claimed | Citation validity, grounding rate, unsupported/contradicted claim diagnostics - separately named PolyCodeBench adaptations |
| Incomplete evidence | n/a | `unknown` (None), never zero |
| Code dimensions | n/a for prose answers | `not_applicable`; no six code dimensions, no invented answer-quality index |
| Scores | Official benchmark scores | None used, none reproduced, none comparable |

## What is not claimed

- No live judge or model call was made for these fixtures: entailment is exercised through
  deterministic fixture votes and the frozen vote conversion only; the judge panel is
  unprovisioned in this workspace (`config/judging/panel-v1.yaml`).
- No official DeepCodeBench score, task or dataset item is used, reproduced or compared, and
  nothing in this pack is comparable to an official DeepCodeBench score.
- No dataset import: no DeepCodeBench dataset file was downloaded or copied; the snapshot and
  oracle are PolyCodeBench-authored (CC0-1.0 authored fixture).
- No native reproduction and no exact-grader claim: `inspired` describes provenance only.
- No six code dimensions and no invented answer-quality index for Q&A output; prose receives no
  security, runtime or idiom scores.
- No performance/efficiency measurement for these tasks (declared not applicable).
