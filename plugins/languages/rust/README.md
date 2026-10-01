# Rust language plugin (Prompt 11)

Scope: **PCB-11-1** (pinned toolchains, offline crates, distinct recipes), **PCB-11-2** (plans,
parsers, symbol index, `RustLanguagePlugin`) and **PCB-11-3** (the diagnostic/idiom profile).
Admission fixtures and the 12-cluster pilot inventory are PCB-11-4 and are not here yet.

| Piece | Location |
|---|---|
| Images, recipes, Dockerfile | `infra/images/rust/` |
| Offline components + vendored crates (only online step) | `scripts/fetch_rust_components.py` |
| Recipe builds + identity recording | `scripts/build_rust_images.py` |
| Recorded identities | `config/images/rust-v1.json`, `config/images/rust-components.json` |
| `Cargo.lock` identity | `plugins/languages/rust/src/polycodebench_lang_rust/locks.py` |
| Image/tool identity | `plugins/languages/rust/src/polycodebench_lang_rust/identities.py` |
| Guest tooling (Miri classifier, context scanner, libtest parser) | `plugins/languages/rust/src/polycodebench_lang_rust/guest/` |
| Profile (rule mappings, applicability, ownership) | `config/languages/rust-profile-v1.yaml`, `plugins/languages/rust/src/polycodebench_lang_rust/profile.py` |
| Plugin, plans, parsers, libtest evidence, symbols | `plugin.py`, `plans.py`, `parsers.py`, `testparse.py`, `symbols.py`, `taskspec.py` |
| Fixture task and recordings | `fixtures/top-words/`, `tests/fixtures/rust_tool_output/` (`scripts/record_rust_tool_fixtures.py`) |
| Tests | `tests/test_rust_locks.py`, `tests/test_rust_guest.py`, `tests/test_rust_profile.py`, `tests/test_rust_plugin.py`, `tests/test_rust_parsers.py`, `tests/test_rust_docker.py` (opt-in live) |

## Images (PCB-11-1; rebuilt in PCB-11-2)

PCB-11-2 rebuilt all three images: each now carries the pinned Python interpreter (the sandbox
provider drives every guest with it; D-11-10) and the evaluator image carries a baked Miri
sysroot (D-11-12). Digests below are the current ones.

Three genuinely distinct recipes, built with `--network none` from a pinned base
`rust@sha256:540c902e99c3…` (rustc/cargo 1.83.0):

| Recipe | Digest | Contents | Used by |
|---|---|---|---|
| runtime | `sha256:a3f88da16577…` | rustc + cargo only | build, test, symbol plans; the solve guest |
| evaluator | `sha256:5aa0fac65e0b…` | + clippy 0.1.83, rustfmt 1.8.0, Miri 0.1.0 (nightly-2026-09-30) | analyzer plans |
| performance | `sha256:de4293082733…` | rustc + cargo, baked `[profile.release]` | performance iteration plan |

**The distinction is enforced, not asserted.** `require_distinct()` fails the build unless the
analyzers actually *run* in the evaluator image, are actually absent from the other two, and the
three digests differ. This gate caught a real defect: an earlier build produced three
differently-tagged images whose contents were byte-identical in the ways that mattered, because
every recipe copied the same complete toolchain home. Note that `command -v` cannot detect this
class of problem — `rustup` installs proxy shims for every tool regardless of whether the
component exists, so only *execution* proves a component is present.

The performance recipe bakes `[profile.release]` (`opt-level = 3`, `codegen-units = 1`,
`debug-assertions`/`overflow-checks` off, `panic = "abort"`) into the image's cargo config rather
than leaving it to each task's manifest, so measurement codegen is fixed by the image and timings
are comparable across candidates and reruns.

## Offline crates

`rustup` deletes a component payload once it is unpacked, so — unlike Python wheels — there is
nothing file-level to vendor or hash-verify per package. The faithful analogue of the wheelhouse
download is a prebuilt **components image**, built once by `scripts/fetch_rust_components.py`.
That is the only step in the Rust pipeline that uses a network; `build_rust_images.py` then
builds all three recipes with `--network none`.

Miri additionally interprets `std` from source and builds a sysroot that resolves real crates
from crates.io. Those 1262 crate files are vendored into the image and `CARGO_HOME` source
replacement is baked in, so a scored run installs and fetches nothing. The vendor set is whatever
`cargo miri setup` actually resolves, so it cannot drift from the pinned nightly. An incomplete
vendor set makes the sysroot build fail loudly — classified `failed`, never clean.

## `Cargo.lock` and evaluator identity

The DoD is "toolchain/`Cargo.lock` and image digests determine evaluator identity". `locks.py`
parses the lock and digests the **resolution** (name/version/checksum, sorted), so re-ordering or
re-formatting the file is not a spurious identity change while a real dependency change is.

A lock that does not actually pin is **rejected**: a registry package with no cargo checksum
raises `LockError` rather than being digested, because such a lock would let the identity drift
silently between two runs that look identical. The digest becomes `ToolIdentity.lock_digest`, so
image digest + toolchain + lock together determine the identity of any tool whose result depends
on the build.

`config/images/rust-v1.json` records the base digest, per-recipe digests, tool versions (with
`absent` stated explicitly for tools a recipe does not ship), the recipe and Dockerfile digests,
the pinned Miri toolchain, and the guest/rule-bundle digests. A rebuild changes image digests, so
task manifests must be resealed and admission re-run.

## Not yet delivered

`config/plugins/allowlist-v1.yaml` registers `polycodebench_lang_rust.plugin:RustLanguagePlugin`,
but that module lands with PCB-11-2; loading it correctly fails until then. No Rust plan has been
executed end-to-end through the supervisor yet, because there is no `RustLanguagePlugin` to
produce one.