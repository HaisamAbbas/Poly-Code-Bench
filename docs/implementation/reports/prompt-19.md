# Prompt 19 — JavaScript and TypeScript support

Date: 2026-10-05 · Phase 4 · WP-19, part 1 · E2E-15 / E2E-35

## Prompt 19 / Phase 4 — PARTIAL

JavaScript now has one admitted authored fixture pack. TypeScript has separate profile and image
identities but no task pack of its own. The JS admission is development-sandbox engineering evidence;
it does not close the full cross-language path, quality approval, or Phase 4 gate.

### 1. Implemented functionality and evidence

- JavaScript has a pinned Node toolchain, runtime/evaluator/performance images, distinct profile,
  analyzer plans and an authored `top-words` task pack with six variants.
- The saved admission report records 25/25 checks passing in the development sandbox. Its five
  reference repetitions pass consistently; the declared faulty and timeout variants fail as
  candidates, the valid alternative passes, and required ESLint/context-scan evidence is measured.
- Evidence: `docs/implementation/evidence/prompt-19-js-admission.json`, task package digest
  `sha256:ea093675fe2762cdd095dba427fc7308a3f350b6bb53756e51d41be6e3debe8f`, report digest
  `sha256:c10b77ad603bcd5a4f5605578eb212ca0cc7e1bc73680ce5b3a0575cb73e89e9`.
- TypeScript retains its distinct profile and image identities, but it has no manifest, oracle,
  hidden tests or admitted variant. The JavaScript profile does not apply TypeScript-only metrics.

### 2. Verification actually run

- `uv run pytest -q tests/test_language_extension_audit.py` — PASS, 7 tests on 2026-10-05. The
  audit confirms the JavaScript manifest exists and that TypeScript still has no task pack.
- The 25/25 JavaScript executable-admission result is the saved 2026-10-03 development-sandbox
  artifact above. It was not rerun during this audit correction.
- No live model run or benchmark score is claimed. The fixture and release evidence are synthetic
  internal engineering data.

### 3. Ticket and aggregate status

- **PCB-19-1:** Partial. The JS pinned recipe and authored task admission exist; TypeScript still has
  no task pack, and full offline/core-path conformance is not established for both languages.
- **PCB-19-2:** Partial. Build, test, symbol and analyzer plans exist; task-driven behavior for the
  TypeScript profile and full JS/TS path coverage remain open.
- **PCB-19-3:** Partial. JS and TS have distinct applicability/profile definitions. The saved JS
  pack does not exercise every async, error, concurrency and type-safety rule in the ticket.
- **PCB-19-4:** Partial across the ticket. JavaScript admission passes; TypeScript task admission,
  curator approval, owner-rights confirmation and downstream generic scoring/replay integration
  remain open. `quality_admission` is `pending` in the report.
- E2E-15 and E2E-35 remain partial. The Prompt 23 `0 / 0` JavaScript count is a historical snapshot
  and is superseded for current fixture count by Prompt 19 evidence. See
  `docs/implementation/reports/language-coverage.md` for cross-language limits.

### 4. Decisions and limitations

- JavaScript and TypeScript remain separate language identities; the JavaScript runtime has no
  TypeScript compiler, and JS results do not imply TypeScript support.
- The admission's development-sandbox tier is not curator approval, a frozen public release, a
  calibrated score, or benchmark evidence.
- C++ admission, current Go conformance and complete cross-language coverage remain separate WP-19
  gates.

### 5. Exact next work

- Continue WP-19 by authoring and admitting a TypeScript fixture pack, exercising the required
  solve/grading/scoring/replay path and language-specific behavioral cases, then obtain quality and
  curator approval. Reassess E2E-15/35 only after all language gaps have current evidence.
