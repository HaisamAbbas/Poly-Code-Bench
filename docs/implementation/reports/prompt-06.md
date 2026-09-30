# Prompt 06 / Phase 2 — PARTIAL

## 1. Implemented functionality and changed files

- Added strict sandbox contracts and typed lifecycle operations in `packages/runner/src/polycodebench_runner/contracts.py` and `provider.py`; exported the public runner API from `__init__.py`.
- Implemented the development `LocalDockerSandboxProvider`: pinned images, provider/stage/fence/lane-scoped labels, network-disabled candidate container, read-only root, no host namespace/socket mounts, non-root identity, bounded CPU/memory/swap/PIDs/tmpfs, safe staging/snapshot, bounded execution/output, cancellation, TTL collection and verified destroy. Candidate argv is passed as argument vectors; staged paths and bytes use JSON stdin and a fixed guest writer.
- Implemented the EC2 disposable-guest driver, strict AWS caller identity verification, scoped stage capability, SSH forced-command control channel, guest resource/path/process policy, distinct private lane configuration, bootstrap and Terraform plan under `infra/sandbox/`; local and production policy documents are under `config/sandbox-policies/` and covered by a policy drift test.
- Hardened guest bootstrap so the forced-command account is not in Docker's root-equivalent group and accepts only Ed25519 or P-256 ECDSA supervisor keys. Documented that exact approved candidate image digests must be preloaded into the reviewed AMI because runtime pulls are disabled. Fixed expiration cutoff handling for epoch zero.
- Added adversarial provider/contract fixtures in `tests/test_sandbox.py`, a live local Docker evidence record at `docs/implementation/evidence/prompt-06-local-docker.json`, and the security/deployment choice D-06-01.
- Updated CI mypy coverage for runner/guest/test code and removed a now-unneeded type ignore in `scripts/validate_task_contracts.py` so the configured check passes.

## 2. Tests/commands actually run and their results

- `$env:UV_CACHE_DIR='.cache/uv'; $env:PCB_TEST_DOCKER='1'; uv run --locked --offline --all-packages --group dev pytest -q -p no:cacheprovider --tb=short tests/test_sandbox.py::test_live_docker_containment_and_cleanup` — PASS, 1 passed in 9.19s against Docker Linux engine 29.7.2 and pinned Python 3.12 slim digest. Observed identical-stage replay/readback, network and metadata-IP denial, absent Docker socket, symlink snapshot rejection, PID/memory/swap/disk/time limits, cancellation, orphan TTL collection and verified destroy. Evidence: `docs/implementation/evidence/prompt-06-local-docker.json`.
- `$env:UV_CACHE_DIR='.cache/uv'; uv run --locked --offline --all-packages --group dev pytest -q -p no:cacheprovider --tb=short` — PASS, 49 passed, 19 skipped in 3.24s. PostgreSQL integration tests skipped because `PCB_TEST_DATABASE_URL` is unset; opt-in Docker cases were run separately above.
- `$env:UV_CACHE_DIR='.cache/uv'; uv run --locked --offline --all-packages --group dev ruff format --check .` and `ruff check .` — PASS, 95 files formatted; lint clean.
- `uv run --locked --offline --all-packages --group dev mypy --disable-error-code=import-untyped packages/core/src packages/configuration/src scripts` — PASS, 17 source files.
- `uv run --locked --offline --all-packages --group dev mypy --follow-untyped-imports packages/runner/src infra/sandbox/guest/pcb-guest-control.py tests/test_sandbox.py` — PASS, 5 source files.
- `uv run --locked --offline --all-packages python scripts/check_boundaries.py`; `scripts/smoke_workspace.py`; `scripts/export_contract_schemas.py --check`; `scripts/validate_task_contracts.py` — PASS: package boundaries, ten imports/config smoke, 21 generated contract outputs and pilot contract checks.
- `uv build --offline --all-packages` — PASS: source and wheel for all ten packages.
- `git diff --check` — PASS (only Git CRLF normalization warnings for three edited tracked files).
- `Get-Command aws,terraform -ErrorAction SilentlyContinue` — neither command is installed. `terraform validate/plan`, AWS provisioning, hosted CI, PostgreSQL integration variants and real EC2 E2E were not run. No cloud calls, paid work, model calls, uploads or releases were made.
- `uv run --locked --offline --all-packages --group dev pytest -q -p no:cacheprovider --tb=short tests/test_sandbox.py` ? PASS, 11 passed, 1 skipped (live test is opt-in).
- `bash -n infra/sandbox/aws/guest/bootstrap-control.sh` ? local syntax check could not start: Windows WSL Bash returned `E_ACCESSDENIED`. Added this syntax check to GitHub Actions; hosted CI has not run.

## 3. Acceptance gates

- Implemented: PCB-06-1 through PCB-06-4 code scope and local/provider contract coverage. Ticket verification remains `blocked` because each ticket’s required production-driver acceptance is not satisfied by local emulation.
- Local E2E subcases: E2E-05 network/metadata/socket/path denial and E2E-06 memory/PID/time/disk/cancellation/cleanup passed against actual local Docker. E2E-05 and E2E-06 full scenarios remain `not_run` in the matrix because production VM evidence is required.
- Blocked: production EC2 attestation, production destruction verification, `terraform validate/plan`, and full E2E-05/06. Owner deferral (2026-09-30): cloud access is unavailable for now. Required inputs when resumed: explicitly authorized AWS account and region; exact supervisor principal ARN; reviewed AMI ID and image-manifest digest/provenance; approved private subnet/security-group identities; and an explicit spend ceiling. Terraform and AWS CLI must be made available. Do not provision until those inputs are recorded.
- Phase 2 aggregate gate: IN PROGRESS, not passed. Prompt 07 has not been started.

## 4. Decisions or specification discrepancies recorded

- D-06-02 records the owner deferral: cloud access is unavailable for now, so no production provisioning or EC2 evidence will be attempted until access is available. D-06-01 selects a private forced-command SSH guest-control channel with pinned host keys, supervisor-only ingress, stage-scoped capability, JSON stdin and live principal/resource attestation. This is an implementation choice within the source contract. It does not relax any acceptance criteria. There is no approved VM image or deployed production isolation claim.

## 5. Exact next command or numbered prompt

- Next: Auxiliary R1 — obtain and record the authorized AWS account/region, supervisor identity, reviewed AMI/image manifest, private network target and explicit budget; install/approve Terraform and AWS CLI; validate and review the plan before any authorized deployment, then collect production E2E-05/06 evidence.
