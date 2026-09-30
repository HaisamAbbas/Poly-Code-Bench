# Decisions and specification discrepancies

## Prompt 00 baseline

- **No architecture or scoring decision added.** Follow the Architecture v1, Technical Implementation Spec v1, and prompt pack v1 in their stated precedence. No existing implementation was found to reconcile or refactor.
- **Source identity checked:** Architecture and Technical Spec SHA-256 values match those recorded in prompt-pack section 1.1. See `source-manifest.json`. The pack has no expected self-hash to compare.
- **Missing original source:** `Pasted markdown(5).md` is referenced as the original requirements source by the Architecture and Technical Spec, but is absent from the workspace. This prevents independent comparison against that source; it does not contradict the two present, detailed specs. Locate it before claiming source provenance complete.
- **Sequencing:** Use prompt-pack phases/prompts and dependencies as written. Prompt 05 allows a minimal local admission execution slice if needed; Prompt 06 completes the sandbox work. This is an explicit permitted dependency slice, not an acceptance-gate change.
- **Repository state:** The directory is not a Git repository. No pre-existing edits or history can be assessed; no files were overwritten. Establish version control as part of Prompt 01 if this workspace is intended to be tracked.

## Open prerequisites (not design discrepancies)

Cloud account/region, OIDC identities, provider/judge endpoint credentials, explicit spend limits, source rights, human calibration data/reviewers, object-store endpoint, and publication target are not established in the inspected workspace. These are configuration or external-evidence inputs called out by the specs; do not infer them from installed tools.


## Prompt 01 decisions and discrepancies

- **D-01-01 - Workspace boundaries:** use the Technical Implementation Spec's Python ownership boundaries (`core`, `services`, `persistence`, `orchestration`, `runner`, `evaluation`, `scoring`, `publication`, `plugins`) as the package/dependency direction. Keep plugin interfaces separate from plugin implementations. Scaffolds declare ownership only; no production behavior is inferred from a package existing.
- **D-01-02 - Runtime identity pins:** pin Python 3.12.10 (spec baseline) and Node 24.21.0 (active LTS verified against the official release index on 2026-09-30); pin direct Python/npm dependencies in project manifests. Lock resolution, clean frozen dependency sync/install, and package builds passed on 2026-09-30.
- **D-01-03 - Local service scope:** Compose declares PostgreSQL and S3-compatible object storage for local development only. These are not approved scored-run images and are not the production VM execution boundary.
- **SD-01-01 - Initial registry block resolved:** the first run could not reach PyPI/npm, but the authorized Auxiliary R1 retry succeeded. `uv.lock` and `pnpm-lock.yaml` now exist; frozen installs and builds pass. Registry availability can still depend on the execution environment.
- **SD-01-02 - Identity/allowlist boundary:** `PCB_SERVICE_IDENTITY` is validated startup metadata, not cryptographic evidence of a platform identity. External endpoint/object-store/image allowlists and real service-principal checks require deployment policy inputs and are not claimed implemented by this config scaffold.
- **Method source status:** Current source descriptions preserve native/adapted boundaries. CursorBench private tasks/grader assets are unavailable; current source/data licenses and rights must be checked at task admission. No method execution or task import occurred.
- **Prompt 01 completion:** the registry and Docker engine gates were resolved during Auxiliary R1. Frozen installs, Python package builds, mypy, test/config/boundary checks, frontend typecheck/lint/build, and local service readiness passed. The pushed baseline commit remains `08a0edd4b674e8da62f6a6f5e65cdd53db1a086b`; completion and ledger updates are uncommitted. No product-contract change was made.
- **D-01-04 - Immutable local development images:** Approve only for local development `docker.io/library/postgres:17.6@sha256:00bc86618629af00d2937fdc5a5d63db3ff8450acf52f0636ec813c7f4902929` (linux/amd64 child `sha256:b86568d3e0fe1dfaeff52714f9da36f206a30e4c49131b82bf96982d78627409`) and `docker.io/chrislusf/seaweedfs:4.48@sha256:4e61d15fd35994cb1e43e1e553dff106794841fd9a99ade2fc8c8bfce4d7872d` (linux/amd64 child `sha256:aba492e2a4e4c90bff795745e8e660affa1f09e7650f5981bd7bccd1a06cd931`). Digests were resolved from Docker Registry manifests and the pulled linux/amd64 image identities were checked on 2026-09-30; the pulled image IDs and RepoDigests matched the pinned references. These exact identities are approved for local development only. SeaweedFS is confined to the optional local S3 role; this does not establish full AWS S3 compatibility or production suitability. Cryptographic signature provenance was not independently verified.
- **SD-01-03 - MinIO image no longer usable here:** the configured Docker Hub image returned `pull access denied`; Quay returned `401 Unauthorized`. MinIO's upstream release instructions now direct container users to build from source. Replace it for local development with the pinned SeaweedFS S3 endpoint rather than asserting the unavailable MinIO image was verified. References: https://github.com/minio/minio/releases and https://github.com/seaweedfs/seaweedfs/blob/master/docker/README.md.

- **D-01-05 - pnpm install policy:** keep dependency lifecycle scripts blocked by default and explicitly allow only the reviewed `unrs-resolver` native-binding postinstall. The exact pinned Next 16.3.7 packages are listed in `minimumReleaseAgeExclude` to account for their release metadata; the lockfile supply-chain policy passed for all 402 entries.

## Prompt 02 decisions and discrepancies

- **D-02-01 - Seed wire representation:** Technical Spec §3 requires unsigned 64-bit seeds to survive serialization without signed overflow, so `master_seed` is a canonical decimal string throughout the versioned contracts. A numeric `master_seed: 4096` example in Technical Spec §4 is treated as illustrative shorthand; the narrow wire-safe representation in §3 governs. No score or sampling semantics are changed.
- **D-02-02 - Contract E2E evidence boundary:** E2E-01 passed using the shared cross-runtime contract fixtures and property checks. This is contract-level verification, not an application workflow or benchmark execution result; broader immutable persistence and score replay remain pending under REQ-09.
