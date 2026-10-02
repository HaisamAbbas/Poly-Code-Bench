# Language extension coverage audit

Date: 2026-10-03  
Status: PARTIAL  
Scope: Prompt 23 / WP-19. This report separates local contracts, historical container evidence, current development-sandbox admission, and missing task-backed paths. All authored fixture runs are internal engineering evidence, not model benchmark results.

## Audit result

Java is built from the pinned offline component set and now passes its current 26-check executable task admission. Go has a current executable admission. All eight language plugin entry points and image identity sets are recorded in the administrative allowlist; that registration does not certify end-to-end language support. The audit test explicitly asserts that JavaScript and TypeScript still have zero task manifests.

E2E-15 and E2E-35 remain partial. C and C++ have local task/profile/plan checks but no current sandbox admission. JavaScript and TypeScript have distinct profiles and images but no task packs. The Go admission is current, while its prior conformance report had one invalid benign-code sample; the corrected conformance run is in progress. Python and Rust evidence is reused from earlier prompts and is identified as historical.

## Profiles, toolchains, image identities, and task counts

Task counts are checked-in fixture manifests, not independent task clusters. Variant counts are the declared admission variants.

| Language | Profile and tool identities | Image identity file and exact image digests | Tasks / variants |
|---|---|---|---|
| Python | python-profile-v1; Python 3.12, pytest 9.0.2, Hypothesis 6.150.2; Ruff 0.16.8, mypy 1.19.1, Bandit 1.9.2, Semgrep 1.150.0 | config/images/python-v1.json; runtime sha256:beb3dd62e10bd3f6082a3e687c37b8b8bf325acf22f7afcdc2503bb2b1b30ffa; evaluator sha256:dd801029a0638924b650e829cac0e6b85bd6c055da2694c52da716b6d2ab4a99 | 1 / 6 |
| Rust | rust-profile-v1; rustc/cargo 1.83.0, rustfmt 1.8.0, clippy and Miri | config/images/rust-v1.json; runtime sha256:a3f88da16577b2516a9f243811fe407bf58158030686b869e8fb84b203bdfb69; evaluator sha256:5aa0fac65e0b1c1d8696f86975699b3fa682e2b8e9c4a867a47f378cb5adca25; performance sha256:de4293082733f48aa17c59aad10041893efb086eb5e4e2ef9a2470795775b322 | 1 / 5 |
| C | c-profile-v1; Clang/clang-tidy 14.0.6, Cppcheck 2.10, Valgrind 3.19.0 | config/images/c-v1.json; runtime sha256:bf1051fbfc927ecd6f686cb7eafbcb4a3fe81a928d63d8e3bcbbfc68d36e28dd; evaluator sha256:cee0a0fb01ac94de278fd1ec98a881f6968c8c2853ab5a81b5ca4b357ecafec5; instrumented sha256:6f810d457efb77d6291ae6170198a3e5ba07f5808df3bef622bdfca040a48886; performance sha256:cee211b0006709b238adeb2a579bb9466cf3c4e86398875decc33a221798d062 | 1 / 9 |
| C++ | cpp-profile-v1; clang++/clang-tidy 14.0.6, Cppcheck 2.10 | config/images/cpp-v1.json; runtime sha256:f66c97026ae70d0d005d610d3af14fa2d7b084908d28d00ed64c819446502c8b; evaluator sha256:6d4220370dd351538b8a48c35db2e195d91dfd9557b13c068fed73d61b8ced71; performance sha256:6d793711bf2e80acd4913326f5f1660c7b66a76877ebf0909907f29427c7419b | 1 / 9 |
| Go | go-profile-v1; Go 1.26.8, Staticcheck 2026.2.1; gosec recipe pins v2.29.0 but the binary reports Version: dev, and the image identity records go-vet as absent although go vet plans execute | config/images/go-v1.json; runtime sha256:441055d335cfdc40ac70a6b30118277ef06a8961b09be95ae7387d646621437b; evaluator sha256:32e6d0fbe6e33aeaec4ab63240d41c04466a3ae2044903616ba840518e379272; performance sha256:84b80a8918f82aa80c14f451918c85cc0577b984bfaf6bab184e9e0c613e2f46 | 1 / 6 |
| Java | java-profile-v1; Temurin JDK 21.0.7, Maven 3.9.9, JUnit 5.10.2, compiler 3.13.0, Surefire 3.2.5, dependency plugin 3.6.1, SpotBugs 4.8.6.0, PMD 3.21.2, Checkstyle 3.3.1 / engine 9.3 | config/images/java-v1.json; runtime sha256:5c33f7597765c4d8ecc15b283c8bcf1385166080a4fe6bca11daf05711309a6d; evaluator sha256:5d73131b3f4b9ed45249ba3907d506c53cdcdc2c3f08148a4a8dc3d9b0c90ede; performance sha256:f4dd044becfe32c59ad2905fdb16006bca5fb45686adee490d9ca40de95f03d5 | 1 / 8 |
| JavaScript | javascript-profile-v1; Node 22.23.2, npm 10.9.8, Vitest 5.0.3, ESLint 10.11.0; tsc is absent | config/images/javascript-v1.json; runtime sha256:733dd86c72be881f702771c3648878a9615cf00326391b22777cca74281f7e86; evaluator sha256:5ff3e2279c5ae9bc930081ada765b168ab4802d3915a9b132510df53f9d0362a; performance sha256:5525cc983a8f5a4ec5538f04e585f62037abfe49286bd83a3e86a304a15ea258 | 0 / 0 |
| TypeScript | typescript-profile-v1; Node 22.23.2, npm 10.9.8, Vitest 5.0.3, ESLint 10.11.0, TypeScript 7.0.2 | config/images/typescript-v1.json; runtime sha256:c1ca49e6bca19e843db0bf55742b2f9ff7995257bb7f48a65ca49e60bece685a; evaluator sha256:3336f44d886fa5f6232608fb550298b966b23f3625076f51c56a63d97d4e7fbc; performance sha256:73ba1b3ea585367c9f1f8967f2b748fc676e2d723f845b84d4a6d801faaab84c | 0 / 0 |

JavaScript and TypeScript are not aliases: they have distinct profile ids, language ids and evaluator images. The JavaScript evaluator records tsc as absent; TypeScript records TypeScript 7.0.2 and has type-safety applicability that JavaScript does not. Neither language has a manifest, oracle, hidden tests, or admitted variant, so the images and profile entries do not stand in for a task path.

The Java variants are reference, wrong-ties, stream-alternative, null-unsafe, leaked-resource, unsafe-publication, command-injection, and nonterminating. Streams, records, and SOLID terminology earn no automatic idiom points; the Java profile rejects presence-only mappings. Its fixed JVM policy is documented in D-23-01.

## Conformance evidence

| Language/scope | Evidence and result |
|---|---|
| Python | Historical Prompt 10 report: 14/14 container conformance cases, docs/implementation/evidence/prompt-10-conformance.json. Reused; not represented as a fresh run against every current host-adapter edit. |
| Rust | Historical Prompt 11 report: 16/16 container conformance cases, docs/implementation/evidence/prompt-11-conformance.json. Reused; not represented as a fresh run against every current host-adapter edit. |
| Go | Current Prompt 22 executable admission: 24/24 checks, six variants, development sandbox, docs/implementation/evidence/prompt-22-go-admission.json. The prior 17/18 conformance artifact is preserved as prompt-22-go-conformance-first-attempt.json; its only failure was the benign sample calling Close on strings.Reader. The sample is corrected to use io.NopCloser and bufio.Reader, and the rerun is in progress. |
| Java | Current executable admission: 26/26 checks passed in the development sandbox. Five identical reference outcomes; the alternative passes; wrong-output and null variants fail; resource and unsafe-publication defects pass the correctness gate but fail their quality-only behavioral probes; the timeout is a candidate failure. All five required scans are measured on the reference. Evidence: docs/implementation/evidence/prompt-23-java-admission.json, package sha256:1c400f3de8065d6d1a332fc799bf8124b3c9b964922e85b4ff32c88eab25a762. The reference also records one unused-import finding; no Java scorecard or baseline-delta score is claimed here. |
| C and C++ | Current task manifests and profiles pass local validation and typed plan tests. No current full sandbox admission is claimed. The C++ builder was changed to preserve every language in the allowlist; Cppcheck is verified as 2.10. |
| Cross-language local audit | tests/test_language_extension_audit.py plus Java, C++, Go, Python and Rust plugin tests: 143 passed. After adding an explicit guard that JS/TS allowlisting cannot imply task admission, tests/test_language_extension_audit.py: 7 passed. These local checks cover output contracts, traversal rejection, profile scoring/replay, registrations, image digest matching and available task/plan construction; they are not substitutes for sandbox execution. |

## Integration defects fixed

Real Java image execution exposed offline-resolution and analyzer defects hidden by local plan tests: the fixture POM pointed Surefire at the default test directory and therefore ran zero tests; PMD used an unbound target-JDK property; Checkstyle needed a plugin-local Guava pin and a rule supported by its bundled engine; and the dependency pipeline re-executed a copied CPython binary that aborted before producing a report. The dependency scanner now runs through the shared command runner in-process, resolves all scopes, parses Maven coordinates correctly, applies the frozen advisory snapshot, and reports absent output as missing evidence rather than clean.

The resource and concurrency admission probes initially failed their own intended-outcome checks. ByteArrayInputStream.available() did not indicate whether close() was called, and the defective shared map was written but not read by the return path. The resource probe now observes close directly; the concurrency fixture returns the unsynchronized shared state under a barrier-start stress test. Both now fail only their intended quality-only probes while preserving functional behavior.

The language audit also fixed a C++ image-builder defect: its allowlist writer rebuilt only a subset of languages, silently dropping later plugin entries. It now derives all plugin entry points and recorded digests, and its Cppcheck version parser handles the actual major.minor output. JavaScript's evaluator identity was separated from TypeScript so JavaScript candidates cannot access tsc.

## Remaining limitations and gates

- Java performance is unmeasured. The modes, warmup counts and flags are fixed in the performance image and validated, but no performance workload or comparison was run.
- Java quality admission remains pending; the fixture admission is executable implementation evidence, not curator approval or a score.
- C/C++ need real task admission. JavaScript/TypeScript need authored task packs and real execution through solve, grading, scoring and replay.
- Go's corrected conformance rerun is in progress. Its image identity also has a gosec version-reporting gap and a go-vet metadata mismatch.
- Python/Rust records are historical; current local integration contracts pass, but the full language-extension E2E matrix is still partial.
- No model calls, production workers, human review, public deployment, or public ranking are represented.

E2E-15 and E2E-35 remain PARTIAL. WP-19 and the Phase 4 aggregate gate remain BLOCKED until every required language and relevant variant has current evidence through the core path.
