Prompt 23 / Phase 4 — PARTIAL / BLOCKED

1. Implemented functionality and changed files
   - Implemented Java's offline-seeded JDK/Maven/JUnit recipes, separate runtime/evaluator/performance identities, fixed cold and steady-state JVM policies, SpotBugs/PMD/Checkstyle/dependency analysis, and task-specific correctness/resource/concurrency/security fixtures.
   - Fixed the dependency audit pipeline, offline analyzer failures, and two false-negative behavioral probes. The Java task now passes executable admission at package digest sha256:1c400f3de8065d6d1a332fc799bf8124b3c9b964922e85b4ff32c88eab25a762.
   - Audited the eight language profiles, identities, plugin entries, task manifests, solve output contracts, local grading/scoring/replay paths and capability metadata. Fixed the C++ allowlist generator so it preserves every language; recorded Cppcheck 2.10; separated JavaScript and TypeScript evaluator identities; made the audit assert that JS/TS registration does not imply an admitted task pack.
   - Main paths: plugins/languages/java, scripts/build_java_images.py, scripts/java_task_tool.py, scripts/build_cpp_images.py, tests/test_language_extension_audit.py, tests/test_cpp_build_images.py, docs/implementation/reports/language-coverage.md and docs/implementation/evidence/prompt-23-language-audit.json.

2. Tests/commands actually run and their results
   - Java image build completed from pinned offline components. uv run python scripts/build_java_images.py --check: PASS.
   - uv run python scripts/java_task_tool.py seal plugins/languages/java/fixtures/top-words and validate ...: PASS; package digest above.
   - uv run python scripts/java_task_tool.py admit plugins/languages/java/fixtures/top-words --report docs/implementation/evidence/prompt-23-java-admission.json: PASS, 26/26 checks, development-sandbox tier. Five identical reference passes; alternative passes; wrong-output/null/timeout are candidate failures; resource and unsafe-publication variants pass correctness but fail their quality-only probes; all five required reference scans are measured. Quality admission remains pending.
   - uv run pytest -q tests/test_language_extension_audit.py tests/test_cpp_build_images.py tests/test_java_build_images.py tests/test_java_guest.py tests/test_java_plugin.py tests/test_java_taskspec.py tests/test_java_testparse.py tests/test_go_plugin.py tests/test_cpp_plugin.py tests/test_python_plugin.py tests/test_rust_plugin.py: PASS, 143 tests.
   - After adding an explicit JS/TS zero-manifest guard, uv run pytest -q tests/test_language_extension_audit.py: PASS, 7 tests.
   - Ruff over the changed Java/C++/audit paths: PASS. Ruff over scripts/go_conformance.py and tests/test_language_extension_audit.py: PASS. uv run mypy plugins/languages/java/src/polycodebench_lang_java: PASS, 16 source files. Java pinned recipe/JIT check: PASS.
   - The prior Go conformance report was 17/18 because its benign sample called Close on strings.Reader. That invalid fixture is corrected and its full rerun is in progress; the prior report is preserved at docs/implementation/evidence/prompt-22-go-conformance-first-attempt.json.
   - Reused, explicitly historical evidence: Python 14/14 at docs/implementation/evidence/prompt-10-conformance.json and Rust 16/16 at docs/implementation/evidence/prompt-11-conformance.json. They were not rerun because the pinned image work is expensive; current adapter contracts are covered by the local suite.
   - Not run: current C/C++ full sandbox admissions (local task/plan contracts pass); JavaScript/TypeScript task admission (both have zero task manifests); Java performance measurement (the fixture declares no performance workload); full-workspace pytest and production-worker execution. No model calls or publication occurred.

3. Acceptance gates
   - Satisfied: PCB-23-1 pinned recipes, offline dependency closure and fixed JVM modes; PCB-23-2 Java analyzers and probe outcomes through shared runner/observation contracts; PCB-23-3 Java profile and all eight declared fixture variants admitted. Java executable evidence is development-sandbox evidence, not a scored benchmark result.
   - Partial: PCB-23-4 / WP-19. C and C++ lack current sandbox admission; JavaScript and TypeScript lack task packs; the corrected Go conformance result is pending. Plugin registration and image identities are not counted as task conformance.
   - E2E-15: PARTIAL. Historical Python/Rust evidence, current Go admission, and current Java admission exist; C/C++ execution and JS/TS task paths remain unverified.
   - E2E-35: PARTIAL. Java's profile and contextual probes pass, and JS/TS applicability is distinct locally; required language/variant coverage is incomplete.
   - Phase 4 aggregate gate: BLOCKED on complete current language conformance.
   - Phase 2 aggregate gate remains BLOCKED: 144 expected attempts, 0 completed, 0 model-failed, 144 infrastructure/pre-dispatch-blocked, 0 provider deliveries. Missing provider authorization/configurations, an active hard budget, distinct calibrated judges, production workers and a run-start route remain recorded in prompt-17-preflight.json.

4. Decisions or specification discrepancies recorded
   - D-23-01 records Java's task-frozen cold/steady-state mode and image-declared flags/warmup counts. No specification discrepancy was found.
   - The Java reference reports one existing Checkstyle unused-import finding; admission preserves it as evidence. This fixture run does not generate a scorecard or claim a baseline-delta score.

5. Exact next command or numbered prompt
   - Next: Auxiliary R1 — finish current Go conformance rerun; obtain current C/C++ task admission; author JavaScript and TypeScript task packs and run them through admission, solve, grading, scoring and replay; then re-enter Prompt 23. Prompt 24 remains gated until WP-19 and the Phase 4 aggregate gate pass.
