# Plugin implementation ownership

The stable extension-facing Python types belong to `packages/plugins-api` and depend on core contracts only. Concrete implementations belong in the capability-specific folders below and must not reach into web UI or publication internals. These folders are ownership boundaries only; no language, suite, model, sandbox, or analyzer adapter is implemented by Prompt 01.

- `languages/`: compiler/toolchain, build and diagnostic adapters.
- `suites/`: native benchmark importers and explicitly labeled adaptations.
- `models/`: provider capability and transport adapters.
- `sandboxes/`: sandbox-provider integrations.
- `analyzers/`: static-analysis tool adapters and normalized finding parsers.
