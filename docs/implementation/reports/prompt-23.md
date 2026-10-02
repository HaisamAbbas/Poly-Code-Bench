# Prompt 23 — Java and language-extension audit

**Status: PARTIAL.** The Java implementation, fixture/profile coverage, local language audit and integration-defect fixes are present. WP-19 and the Phase 4 gate remain blocked because Java has no built/admitted images, JS/TS have profiles but no executable plugins, Go's saved image identity is stale after a guest fix, and no Docker daemon was available for fresh C/C++/Go admission.

## Implemented

- Added Java Maven/JUnit task parsing and plan construction, Temurin/Maven/JUnit offline recipes, pinned analysis components, fixed cold/steady JIT policy, static recipe validation, analyzer parsers and task-specific security/resource/concurrency checks.
- Added one Java task manifest and eight reference/faulty/alternative/null/resource/concurrency/security/timeout fixtures. Syntax names such as streams, records and SOLID terms do not award score by presence.
- Added a cross-language local audit for plugin registration and identities, C/C++ manifest and plans, all-language solve-output safety, and synthetic score/replay wiring. JS and TS source identities and profile semantics are checked separately; missing production allowlist entries, fixtures and full image identities are explicit.
- Fixed Go plan construction and module-root working-directory support; fixed C++ `clang++` identity slug and sanitizer exit classification. The existing Go image's guest digest now needs rebuilding and re-admission.

## Verification

- Language/plugin/Java suite: one run reached 161 passed and one Windows guest subprocess test failed before process creation with `WinError 1455` (paging file too small), plus one Linux-only skip. The isolated rerun of that test passed; the effective result is 162 passed and one Linux-only skip across those two runs.
- Shared solve/scoring/replay suite: 79 passed; four standard-agent tests skipped because Docker was not enabled.
- Targeted mypy: 19 source files passed. `scripts/build_java_images.py --check` passed. `uv lock --check` passed.
- Ruff initially found pre-existing guest formatting exceptions on the Go runner; the Go guest is now included with the other source-copied guest exceptions. Final Ruff run remains to be recorded after this config change.
- Generated schemas and package boundaries passed before the final documentation updates; rerun included in final checks.

## Gate and limitations

E2E-15 is partial: Python and Rust retain historic real-container evidence; the other required language paths do not all have current real-container conformance or admitted identities. E2E-35 is partial: profile semantics are checked locally, but JS/TS image-backed task execution is absent and no fresh sandbox admissions validate all language variants. The machine-readable local evidence is `docs/implementation/evidence/prompt-23-language-audit.json`; the per-language tool/image/profile identities and fixture counts are in `docs/implementation/reports/language-coverage.md`.

No Java, C, C++, or Go Docker image was built or admitted in this turn; no live providers, production workers, model outputs, or public release were used. Java's base recipe identity is not a built image identity. Go's previous Docker evidence is retained as a failure record and is not acceptance evidence. C++ Cppcheck is `unknown`, Go gosec is `dev`, and Rust advisory data lacks a fresh snapshot.

## Phase 4 aggregate gate

**BLOCKED.** WP-19 requires verified core paths and actual conformance evidence for Python, Rust, JavaScript, TypeScript, C, C++, Go and Java. The local audit proves integration contracts for some implementations but does not substitute for language execution/admission. Historical Python/Rust evidence remains valid for its recorded versions. Java image construction/admission, JS/TS image identity and task admission, Go image rebuild/admission, and fresh C/C++/Go image verification remain open. E2E-15 and E2E-35 stay partial. No ranking claim is made and Prompt 24 is not unblocked by this report.

## Next action

Resume with Docker available: add Java Docker admission/runtime cases, run `uv run python scripts/fetch_java_components.py` and `uv run python scripts/build_java_images.py`, rebuild Go using `uv run python scripts/build_go_images.py`, and run the language-specific Docker suites. Complete JS/TS image identities, add task fixtures, admit both identities and run their conformance cases. Close E2E-15/35 only after those results and current image identities are recorded.
