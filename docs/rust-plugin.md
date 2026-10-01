# Rust language plugin (Prompt 11)

The Rust plugin implements the same `LanguagePlugin` contract as the Python plugin
(`polycodebench_plugins_api`). The plugin only *describes* work (typed argument vectors, pinned
image digests, exit semantics) and *parses* recorded bytes; the trusted supervisor
(`PlanRunner`) executes plans in the sandbox. Scope: development sandbox tier. Production
isolation is the owner-deferred Prompt 06 gate.

## Images (PCB-11-1, rebuilt in PCB-11-2)

Three recipes built with `--network none` from the prebuilt components images: `runtime`
(rustc + cargo), `evaluator` (+ Clippy, rustfmt, pinned nightly Miri with a baked sysroot) and
`performance` (baked release profile). Each carries the pinned Python interpreter (with its own
libc, started through its own loader) because the sandbox provider drives every guest with it.
Identities: `config/images/rust-v1.json`; the `Cargo.lock` digest joins the tool identity.

## Plans and parsers (PCB-11-2)

| Plan | Tool | Verdict comes from |
|---|---|---|
| build | `cargo build --message-format=json` | Cargo's own `build-finished` message, not the exit code |
| test (one per oracle group) | `cargo test --test <file> -- --test-threads=1` | libtest output, qualified by test binary (`behaviour::ordering::x`); a `test x ... ` line with no outcome is the case in flight at a timeout or fatal signal |
| clippy | `cargo clippy` with the selected lints | JSON diagnostics; exit 0 even with warnings, so a missing `build-finished` is a missing scan |
| context | `pcb_rust_scan.py` | AST-free line scanner; violation / benign / hint |
| miri (if the task's `miri` is `required` or `optional`) | `cargo +nightly miri test` | diagnostic text: clean / candidate UB / unsupported |
| dependency (if an inventory is declared) | `pcb_lock_audit.py` | pinned advisory snapshot; fails closed while the snapshot is empty |

Every plan runs through `guest/pcb_rust_run.py`: it captures output to files, enforces the
deadline from inside the guest (so a hung test leaves partial output), and removes `target/`.
Compiled languages set `executable_workspace` on the plan's `ResourcePolicy`; the default for
every other plan is a workspace that cannot execute anything.

## Profile (PCB-11-3)

`config/languages/rust-profile-v1.yaml` maps rules to the weighted items of
`profiles-v1.yaml#profiles.rust`. `clone`, `unwrap`/`expect` and `unsafe` are tokens, not
findings: a lint that fires on the bare token counts only when the context scanner confirms a
violation at the same site; unjustified `unsafe` is a hint for review. Required behaviour
decides applicability (a frozen opportunity per item; none means `not_applicable`).

## Task packages and admission (PCB-11-4)

Layout (see `plugins/languages/rust/fixtures/top-words/`): `visible/{task.md,repo/{Cargo.toml,
Cargo.lock,src/lib.rs}}`, `hidden/{oracle.json,quality-plan.yaml,tests/*.rs,reference/src/lib.rs}`,
`admission/<variant>/src/lib.rs`, `admission/exposure-rights.json`. The output contract allows
`src/lib.rs`. `scripts/rust_task_tool.py seal|validate|admit` mirror the Python tool;
`scripts/rust_quick_check.py` is a seconds-long authoring loop (not evidence).

The twelve pilot clusters live in git-ignored `.protected/taskpacks/rust-pilot/`; the committed
`taskpacks/rust-pilot/inventory.yaml` carries identities, digests, source/split/applicability
metadata and admission status only. Executable admission is not quality admission: the gates that
remain are listed in the inventory (`pending_gates`).

## Commands

```
export PYTHONPATH=plugins/languages/rust/src
.venv/Scripts/python.exe scripts/build_rust_images.py
.venv/Scripts/python.exe scripts/rust_task_tool.py seal-all --check
.venv/Scripts/python.exe scripts/rust_admit_all.py
.venv/Scripts/python.exe scripts/rust_pilot_inventory.py --protected .protected/taskpacks/rust-pilot \
    --reports .protected/reports --output taskpacks/rust-pilot/inventory.yaml
.venv/Scripts/python.exe scripts/rust_conformance.py --report docs/implementation/evidence/prompt-11-conformance.json
PCB_TEST_DOCKER=1 .venv/Scripts/python.exe -m pytest tests/test_rust_docker.py
```

Rebuilding the images changes their digests: reseal every package (`seal-all`), re-admit, and
regenerate the inventory.

## Known limits

- The dependency audit has no real advisory database (vendoring one needs a network fetch).
- `rules/clippy.toml` names two lints Clippy 1.83 does not know; they only produce ignored
  warnings, and editing the rules changes the recorded rule-bundle digest.
- Candidate code and the libtest harness share a test binary; process-level isolation between
  them is the sandbox's, not the plugin's.
- No Rust pilot task declares a performance workload (Prompt 13).
