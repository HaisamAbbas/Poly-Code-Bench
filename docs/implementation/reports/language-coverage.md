# Language extension coverage audit

Date: 2026-10-02  
Scope: Prompt 23 / WP-19. This report distinguishes local contract tests, recorded historical container evidence, and current production-admissible identities. Fixture-based results are implementation evidence only; they are not model benchmark results.

## Audit result

The shared extension paths were exercised locally for Python, Rust, C, C++, Go and a Java profile fixture. Each passed plugin/plan or task-manifest checks where an executable plugin exists, the common solve-output extraction contract, and synthetic score/replay tests. The JS/TS source plugin classes and entrypoints also pass local protocol/identity checks, but are not allowlisted or backed by task fixtures/images. Java's recipe and fixture boundary checks pass. Only Python and Rust have complete recorded real-container E2E-15 acceptance evidence (from Prompts 10 and 11). No Docker daemon was available for fresh image verification or task admission in this audit.

The language-extension requirement is **PARTIAL**. Java is not registered as a production plugin because it has no built runtime/evaluator/performance image identities. JavaScript and TypeScript have source plugin classes with distinct profile/entrypoint semantics, but neither is in the production allowlist and both lack task fixtures and full runtime image identities. Existing Go runtime/evaluator/performance records are stale relative to the fixed guest runner and must be rebuilt and re-admitted. C and C++ have local plan/manifest coverage and recorded image identities, but no fresh Docker admission evidence. E2E-15 and E2E-35 therefore remain partial.

## Identity and task inventory

“Manifest tasks” counts checked-in task manifests in each plugin fixture tree, not independent clusters or admitted production tasks. Fixture variant counts describe authored code variants, not successful admissions.

| Language | Profile / tool identity | Image identity | Fixture inventory | Current plugin path |
|---|---|---|---|---|
| Python | `python-profile-v1`; Python 3.12 recipe; pytest 9.0.2, Hypothesis 6.150.2; evaluator Ruff 0.16.8, mypy 1.19.1, Bandit 1.9.2, Semgrep 1.150.0 | `config/images/python-v1.json`; runtime `sha256:beb3dd62e10bd3f6082a3e687c37b8b8bf325acf22f7afcdc2503bb2b1b30ffa`, evaluator `sha256:dd801029a0638924b650e829cac0e6b85bd6c055da2694c52da716b6d2ab4a99`; base `python@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b` | 1 task manifest; 6 fixture variants | Registered and allowlisted. Historic Prompt 10 admission: 12 clusters; separate real-container E2E-15 evidence below. |
| Rust | `rust-profile-v1`; rustc/cargo 1.83.0, rustfmt 1.8.0; evaluator clippy 0.1.83, Miri 0.1.0 | `config/images/rust-v1.json`; runtime `sha256:a3f88da16577b2516a9f243811fe407bf58158030686b869e8fb84b203bdfb69`, evaluator `sha256:5aa0fac65e0b1c1d8696f86975699b3fa682e2b8e9c4a867a47f378cb5adca25`, performance `sha256:de4293082733f48aa17c59aad10041893efb086eb5e4e2ef9a2470795775b322`; base `rust@sha256:540c902e99c384163b688bbd8b5b8520e94e7731b27f7bd0eaa56ae1960627ab` | 1 task manifest; 5 fixture variants | Registered and allowlisted. Historic Prompt 11 admission: 12 clusters; separate real-container E2E-15 evidence below. |
| C | `c-profile-v1`; Clang 14.0.6; evaluator clang-tidy/Cppcheck 2.10/Valgrind 3.19.0 | `config/images/c-v1.json`; runtime `sha256:61b037ef0b05d0e75418510868377c4fc41bbbf2d29ffe021abef2904ddb6f75`, evaluator `sha256:374fdc9da46bee524c1999603bbee2374666dac607e38cf1713cb4d9a83808e9`, instrumented `sha256:2c3b1d1f06dfd7545ee713fac149cd8be8713ac4cdf4c1eab6d0a083df29d06a`, performance `sha256:6d7e10f0bce4041b321c90b178a12be8e52b86ce6ab9515eef7a1ad21b1b67f0` | 1 task manifest; 9 fixture variants | Registered and allowlisted; local plans and manifest validation passed. Current physical images/admission not reverified. |
| C++ | `cpp-profile-v1`; clang++/clang-tidy 14.0.6; Cppcheck identity is recorded as `unknown` | `config/images/cpp-v1.json`; runtime `sha256:f66c97026ae70d0d005d610d3af14fa2d7b084908d28d00ed64c819446502c8b`, evaluator `sha256:6d4220370dd351538b8a48c35db2e195d91dfd9557b13c068fed73d61b8ced71`, performance `sha256:6d793711bf2e80acd4913326f5f1660c7b66a76877ebf0909907f29427c7419b`; base `debian@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251` | 1 task manifest; 9 fixture variants | Registered and allowlisted. Local plan generation now maps the executable `clang++` to valid identity slug `clang-plus-plus`; local plan/manifest checks pass. Current images/admission not reverified. |
| Go | `go-profile-v1`; Go 1.26.8, Staticcheck 2026.2.1; gosec reports `Version: dev` | `config/images/go-v1.json`; recorded runtime `sha256:4aa46c5dc4c75069f8e15ed174c8c5eec1dd011a76656c1ea85f504b69e60f4c`, evaluator `sha256:e873ab88d7956f5754ba45d9cd8304e28c1de75be24c8bfbe80ad50305377148`, performance `sha256:09ddedfa5fac9bf2f0a0cf6708a4c0f0fe71eee9450cce9ff6114ecfa748617c`; base `golang@sha256:abe4f87f354c4f6d7ee3fb11b241c6b6c24a32ca50a2ebcc30493a2168e14048` | 1 task manifest; 6 fixture variants | Registered and allowlisted in the current file, but the recorded guest digest is stale after adding module-root `--cwd` support. Prior Prompt 22 admission evidence failed its reference/faulty/repetition checks. Rebuild and re-admission required; do not rely on that manifest as current. |
| Java | `java-profile-v1`; intended Temurin JDK 21, Maven 3.9.9, JUnit 5.10.2; Maven compiler 3.13.0, Surefire 3.2.5, dependency plugin 3.6.1, SpotBugs 4.8.6.0, PMD 3.21.2, Checkstyle 3.3.1 | Intended base `maven@sha256:3a4ab3276a087bf276f79cae96b1af04f53731bec53fb2e651aca79e4b10211e`, `linux/amd64`; no built runtime/evaluator/performance digest or allowlist entry | 1 task manifest; 8 variants: reference, faulty, alternative, null-unsafe, resource-leak, concurrency-defect, security-defective, timeout | Java code, fixture profile and Maven/offline plan contracts exist. It is intentionally unregistered until images are built, probed, identified and admitted. |
| JavaScript | `javascript-profile-v1`; source entrypoint exists; Node `v22.23.2`, npm `10.9.8`, Vitest `5.0.3`, ESLint `10.11.0` | Partial component records in `config/images/js-components.json`: Node base `sha256:f556d98937ce1949ce5fb51d589d38be472800d10be58d281ffac4cbed15210d`; lint `sha256:5c8504f6b8e530a95986faace2d1c14b983182d7ad035d0e4adfe8a8c40e2fc3`. No full runtime/evaluator/performance language-image manifest. | 0 task manifests; 0 variants | Source plugin has plans/parsers/guest handlers; package is outside the production allowlist and lacks fixture admission and full image identities. |
| TypeScript | `typescript-profile-v1`; source entrypoint exists; Node `v22.23.2`, npm `10.9.8`, Vitest `5.0.3`, ESLint `10.11.0`, TypeScript `7.0.2` | Partial TS component record in `config/images/js-components.json`: `sha256:584da340fde93d949b1727ad6383d5c4eb7623ea75d9166d10f16d58eb85d1f8`. No full runtime/evaluator/performance language-image manifest. | 0 task manifests; 0 variants | Shares the source package with JS but has a distinct plugin identity and type-safety/type-domain dimensions; not allowlisted and lacks task admission/full image identity. |

The JS/TS distinction is tested locally: JavaScript has no `type_safety` dimension; TypeScript includes type-safety and type-domain-modeling applicability. Source classes declare separate identities and common plan/output paths, and the local audit checks protocol methods. This does not establish image-backed execution or task admission.

## Conformance evidence

| Scope | Evidence and result |
|---|---|
| Historical Python real-container E2E-15 | `docs/implementation/evidence/prompt-10-conformance.json`: 14/14 cases passed in pinned Docker runtime/evaluator images across valid, wrong, anti-pattern, analyzer failure, missing dependency, timeout and profile applicability. Historic evidence, reused without rerunning expensive image work. |
| Historical Rust real-container E2E-15 | `docs/implementation/evidence/prompt-11-conformance.json`: 16/16 cases passed in pinned Rust images across the same seven categories. Historic evidence, reused without rerunning expensive image work. |
| Current local shared-contract audit | `tests/test_language_extension_audit.py`: registration/profile checks for existing plugins, C/C++ task and plan checks, output extraction/traversal rejection for all eight language identifiers, and synthetic profile score/replay for Python/Rust/C/C++/Go/Java. These are local fixture checks, not sandbox execution or model results. |
| Java local checks | Java Maven task/profile/guest unit tests and static recipe check pass. Profile checks confirm streams, records and SOLID terminology have no presence-only score. Java task variants carry task-specific null/resource/concurrency/security/wrong-output/timeout opportunities. No Java image has been built or run. |
| Go real-sandbox admission (Prompt 22) | `docs/implementation/evidence/prompt-22-go-admission.json`: all six fixture variants execute in the pinned runtime/evaluator images and every required scan (`gofmt`, `vet`, `staticcheck`, `gosec`, `context`) is `measured`. The prior run's `build:build harness error (tool_error)` was a build cache pointing into the read-only image layer; see D-22-01. |
| Go conformance run (Prompt 22) | `docs/implementation/evidence/prompt-22-go-conformance.json`: the seven-category conformance surface (valid solution, incorrect solution, anti-pattern, analyzer failure, missing dependency, timeout, profile applicability) plus the instrumentation-lane case. |
| Other current image-backed languages | C/C++/Go local plans, fixture manifests, output and scoring/replay contracts are exercised. No current Docker image build/probe/admission was performed in this audit. The Go guest/image digest mismatch is an additional blocker. |

## Java policy and limitations

The Java builder pins the Maven base image by digest and platform and installs dependencies during image construction; scored Maven execution is offline. `scripts/build_java_images.py --check` validates the recipe pins and fixed measurement policy without Docker. Cold mode uses zero warmups; steady-state mode uses a frozen warmup count (20) and does not adapt per candidate. Both modes carry fixed JVM/JIT flags and the same declared workload contract. No performance claim is made because no Java image or homogeneous measurement hardware was exercised.

Java analyzer plans use SpotBugs, PMD, Checkstyle and dependency analysis alongside task-specific resource, concurrency and security probes under shared typed-plan/output contracts. Analyzer failure is infrastructure/incomplete evidence; unsupported checks and candidate findings remain separate outcomes. Local plan and parser tests do not prove analyzer behavior inside the intended image.

The local audit's synthetic profiles and fixtures only establish wiring and contract semantics. They do not prove hidden-task quality, rights, production isolation, actual analyzer detection rates, stable runtime identities, or model behavior. Rust's fresh online advisory database remains unavailable, C++ records an unknown Cppcheck version, and Go's gosec identity is `dev`; these identities need remediation before stricter admission.

## Resume commands

When Docker and the required build/network inputs are available, run:

```powershell
uv run python scripts/fetch_java_components.py
uv run python scripts/build_java_images.py
uv run pytest -q tests/test_java_docker.py
uv run python scripts/fetch_go_components.py
uv run python scripts/build_go_images.py
uv run pytest -q -p no:cacheprovider tests/test_go_docker.py
uv run python scripts/go_task_tool.py admit plugins/languages/go/fixtures/top-words --report docs/implementation/evidence/prompt-22-go-admission.json
```

The Java Docker conformance module is not present yet; add its admission/runtime cases before treating the third command as available. Then run the shared E2E-15/E2E-35 language matrix, reseal affected task manifests, and update this report with actual identity probes and admissions. JS/TS require complete image identities and admitted task packs before Docker conformance can pass.
