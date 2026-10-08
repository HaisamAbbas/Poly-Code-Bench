# Prompt 87 — Task fingerprints and private structural/semantic features

## Implemented functionality and changed files

- Added frozen fingerprint configuration, explicit feature/capability states, digest-bound results and private feature artifact contracts in [task_fingerprints.py](../../../packages/core/src/polycodebench_core/task_fingerprints.py). The configuration digest includes algorithm versions, shingle size/bounds and Unicode database version.
- Added deterministic component fingerprinting in [task_fingerprinting.py](../../../packages/services/src/polycodebench_services/task_fingerprinting.py): exact raw-byte digest; conservative NFC/newline-normalized text digest; and literal-preserving token shingles. It retains identifiers, case, operators, numbers, types, whitespace and comments. It does not abstract identifiers, remove comments, infer similarity scores or claim duplicates.
- Added exact/normalized/shingle private feature payloads containing hashes and their full config, never source text or token strings. Python and Java AST, semantic embeddings and entity/reasoning features return explicit blocked or unsupported states until approved extractors are available.
- Added append-only fingerprint-row persistence in [task_fingerprints.py](../../../packages/persistence/src/polycodebench_persistence/task_fingerprints.py), using the existing `fingerprint` table and requiring verified hidden artifact bindings. Added [e9b30a7c1f42_private_fingerprint_artifacts.py](../../../packages/persistence/src/polycodebench_persistence/migrations/versions/e9b30a7c1f42_private_fingerprint_artifacts.py) to reject pre-existing public/unverified/unbound rows, enforce non-null private artifact refs and reject unsafe inserts. Downgrade refuses while fingerprint history exists.
- Changed the `fingerprint.private_artifact_id` model constraint and added [test_task_fingerprints.py](../../../tests/test_task_fingerprints.py) for normalization distinctions, literal/type/operator preservation, modality/config blockers, deterministic digests, bounds and payload privacy.
- Updated acceptance, decisions, implementation and command ledgers.

## Tests/commands actually run and results

- `uv run pytest -q tests/test_task_fingerprints.py` — **16 passed**. Synthetic text and binary fixtures only; no benchmark source items, external model or parser were used.
- Ruff check on the six changed Python paths — **passed**.
- `uv run ruff format --check` on the six changed Python paths — **passed, 6 files already formatted**.
- `uv run mypy packages/core/src/polycodebench_core/task_fingerprints.py packages/services/src/polycodebench_services/task_fingerprinting.py packages/persistence/src/polycodebench_persistence/task_fingerprints.py packages/persistence/src/polycodebench_persistence/models.py packages/persistence/src/polycodebench_persistence/migrations/versions/e9b30a7c1f42_private_fingerprint_artifacts.py` — **passed, 5 files checked**.
- `PCB_MIGRATION_DATABASE_URL=postgresql+psycopg://offline:offline@localhost/polycodebench uv run alembic -c packages/persistence/alembic.ini upgrade d52a7e11b30f:e9b30a7c1f42 --sql` — **passed**, targeted PostgreSQL DDL rendered; no database execution occurred.
- `uv run alembic -c packages/persistence/alembic.ini heads` — **passed**, one head: `e9b30a7c1f42`.
- No PostgreSQL integration, private artifact upload, live index rebuild, parser/model execution, sealed commitment or public projection was exercised.

## Acceptance gates satisfied, pending and blocked

- **Partial — BX-09 / BAT-05-A:** exact, normalized and shingle methods are distinct; tests prove newline/NFC-only equivalence and differences for literals, types, negation, operators and answer options. No identifier abstraction is implemented. Live imported benchmark coverage is unavailable.
- **Partial — BX-10 / BAT-05-B:** extractor configuration and digest are pinned for exact/lexical views; unsupported binary modalities are explicit. Python/Java parser features and semantic embeddings are blocked because approved parser/model/tokenizer configuration is absent.
- **Partial — BX-11 / BAT-05-C:** no entity, answer-pattern or reasoning extractor is enabled, so these features are blocked rather than fabricated as zeros. Shingle hashes and detailed features are bound to hidden artifacts; no vector or public fingerprint index is created.
- **Partial — BAT-05-D, BWP-05, BREQ-03 and BREQ-24:** append-only rows bind method/config version to private verified artifact payloads, and the database migration enforces private storage. Database integration, artifact lifecycle, tenant separation and versioned index rebuild evidence remain pending. No calibrated confidence or duplicate decision is emitted.
- No gate is complete from fixtures. Only local deterministic computation is available in this environment.

## Decisions or specification discrepancies recorded

- **ADDENDUM-DECISION-14:** normalization only converts CRLF/CR to LF and applies NFC; every other character, whitespace, comment, case distinction, literal, identifier and constraint stays present. Exact-byte digests are not inferred from normalized or lexical matches.
- **ADDENDUM-DECISION-15:** no approved parser/model configuration or crypto key custody is present. AST, embeddings, entity extraction and sealed commitments remain blocked; no substitute model, parser or home-grown commitment scheme is introduced.
- Feature payloads are uploaded as hidden artifacts. Their config and method digest are retained alongside private hashes; public projections are outside this prompt and receive no detailed feature payloads.

## Exact next command or numbered prompt

Prompt88 / BWP-06 — implement local connector/capability contracts and bounded plans. Keep actual source fetch/query blocked until each source has approved rights, access and finite byte/request/time budgets.
