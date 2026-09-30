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
- **D-01-02 - Runtime identity pins:** pin Python 3.12.10 (spec baseline) and Node 24.21.0 (active LTS verified against the official release index on 2026-09-30); pin direct Python/npm dependencies in project manifests. These are candidate compatible pins until clean lock resolution/build succeeds.
- **D-01-03 - Local service scope:** Compose declares PostgreSQL 17.6 and an explicit MinIO release tag for local development only. They are not approved scored-run images; the daemon is unavailable and no image digest/approval record is present. Never use these Compose services as the production VM execution boundary.
- **SD-01-01 - Lock generation blocked:** `uv lock` and Corepack/pnpm lock generation failed because outbound PyPI/npm traffic is refused by the execution environment. Do not fabricate lockfiles; exact dependencies remain declared but installation reproducibility is not yet verified.
- **SD-01-02 - Identity/allowlist boundary:** `PCB_SERVICE_IDENTITY` is validated startup metadata, not cryptographic evidence of a platform identity. External endpoint/object-store/image allowlists and real service-principal checks require deployment policy inputs and are not claimed implemented by this config scaffold.
- **Method source status:** Current source descriptions preserve native/adapted boundaries. CursorBench private tasks/grader assets are unavailable; current source/data licenses and rights must be checked at task admission. No method execution or task import occurred.
