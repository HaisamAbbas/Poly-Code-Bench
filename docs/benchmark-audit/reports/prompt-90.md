# Prompt 90 — Trusted match verification, review and disputes

## Implemented functionality and changed files

- Added `MatchEvidencePayloadV2` / `MatchEvidenceDocumentV2` in [benchmark_audit_documents.py](../../../packages/core/src/polycodebench_core/benchmark_audit_documents.py). V2 binds a match to the target task/components, retrieval plan/result/candidate digests, source snapshot/revision/content artifact, source lineage, source byte offsets and span digests, answer relationship, source-date evidence, rights references, relation rubric, reviewers and counterevidence. The existing v1 document schema and its canonical vectors remain unchanged.
- Added v1/v2 canonical envelope support in [canonical.py](../../../packages/core/src/polycodebench_core/canonical.py), [canonical.ts](../../../apps/contracts/src/canonical.ts) and [contracts.mjs](../../../apps/contracts/test/contracts.mjs). Both versions use the same canonical JSON ordering rules; the envelope version selects the strict evidence schema.
- Added [match_verification.py](../../../packages/core/src/polycodebench_core/match_verification.py) and [match_verification.py](../../../packages/services/src/polycodebench_services/match_verification.py). The verifier checks that the evidence binds to the frozen Prompt89 plan, selection and candidate, verifies source/component artifact digests and checks byte offsets/span digests. Exact text can use only the pinned conservative NFC/LF normalizer. The result always says source and rights trust are unverified and `accepted_evidence=false`.
- Added a frozen relation rubric for exact, near-exact, semantic, family, concept, no-substantive-match and unresolved relations. Concept-only has zero duplicate weight; semantic matches require human review; exact auto-accept remains disabled until an approved calibrated policy exists. Same-task/same-benchmark self-imports are excluded; boilerplate, mixed and unknown spans remain review-gated.
- Added immutable review opinions, conflict/dispute ledger, independent third-party adjudication, and correction records. Corrected evidence is a newer v2 document with `supersedes_id`, and a separate correction record; prior evidence remains intact.
- Added no-tools, proposal-only judge packet contracts that label source text as untrusted data. No judge was dispatched. Updated [test_match_verification.py](../../../tests/test_match_verification.py), [acceptance.md](../acceptance.md), [decisions.md](../decisions.md), [implementation-ledger.md](../implementation-ledger.md) and [commands.md](../commands.md).

## Tests/commands actually run and results

- `uv run pytest -q tests/test_match_verification.py tests/test_benchmark_audit_documents.py tests/test_retrieval.py` — **36 passed**.
- `uv run ruff check packages/core/src/polycodebench_core/canonical.py packages/core/src/polycodebench_core/benchmark_audit_documents.py packages/core/src/polycodebench_core/match_verification.py packages/services/src/polycodebench_services/match_verification.py tests/test_match_verification.py` — passed.
- `uv run ruff format --check packages/core/src/polycodebench_core/canonical.py packages/core/src/polycodebench_core/benchmark_audit_documents.py packages/core/src/polycodebench_core/match_verification.py packages/services/src/polycodebench_services/match_verification.py tests/test_match_verification.py` — passed.
- `uv run mypy --strict packages/core/src/polycodebench_core/canonical.py packages/core/src/polycodebench_core/benchmark_audit_documents.py packages/core/src/polycodebench_core/match_verification.py packages/services/src/polycodebench_services/match_verification.py tests/test_match_verification.py` — passed, no issues in 5 files.
- `corepack pnpm --filter @polycodebench/contracts test:contracts` — passed; canonical v1/v2 envelope checks and 256 property cases, with existing v1 vectors unchanged.
- Combined Prompt85–90 regression: `uv run pytest -q tests/test_match_verification.py tests/test_retrieval.py tests/test_corpus_connectors.py tests/test_benchmark_audit_catalog.py tests/test_benchmark_importers.py tests/test_task_fingerprints.py tests/test_benchmark_audit_documents.py tests/test_benchmark_audit_controls.py` — **109 passed**.
- No PostgreSQL, source, network, reviewer, or model calls were made.

## Acceptance gates satisfied, pending and blocked

- **Partial — BX-18 / BAT-08-A:** source digest/revision, component digest, retrieval candidate binding, byte spans, answer relationship and date context are required and content-checked. The bytes are supplied to a pure verifier; no trusted artifact reader, approved snapshot/rights resolver or real source revision was available.
- **Partial — BX-19 / BAT-08-B:** a versioned rubric distinguishes all seven relations, sets concept-only contribution to zero and keeps semantic/ambiguous matches in human review. No labeled held-out pairs, human reviewers or calibration were available; auto-accept is disabled.
- **Partial — BX-20 / BAT-08-C:** source spans are explicitly untrusted; judge packets forbid tools and have proposal-only authority. There is no audit-specific fresh-session dispatcher, approved model context, or persisted dispatch/exposure record writer, so no judge session was run.
- **Partial — BX-21 / BAT-08-D:** independent opinions are append-only in the contract ledger; conflict requires a third-party adjudicator; a correction creates a linked successor without mutating the predecessor. No persistence repository, audit endpoint or database integration was added.
- **BWP-08: partial.** Content integrity validation is not source authorization or rights verification. No finding can be marked accepted by the content verifier; all fixture candidates and review events are synthetic.

## Decisions or specification discrepancies recorded

- **ADDENDUM-DECISION-23:** add match-evidence schema v2 without altering v1 canonical evidence. The canonical encoding permits envelope schema versions1/2 with identical key ordering.
- **ADDENDUM-DECISION-24:** digest/span integrity does not establish source provenance or rights. The verifier hard-codes unverified trust and `accepted_evidence=false`; source authorization, rights, lineage and date authenticity need an approved reader/verifier. No calibrated auto-accept policy exists.
- **ADDENDUM-DECISION-25:** preserve every review opinion, require an independent third adjudicator for conflicts, and represent corrections as immutable successors plus a correction event. Reducers are present, but the existing foundation tables have no Prompt90 persistence writer.
- **ADDENDUM-DECISION-26:** source text remains untrusted data in no-tools, proposal-only judge packets. Existing generic judge contracts provide related untrusted-comment protections, but no audit-specific model delivery/exposure writer or approved model context is available.

## Exact next command or numbered prompt

Proceed to **Prompt91 / BWP-09** and implement the pure observed-risk policy and coverage/missingness rules over these evidence contracts. For trusted match acceptance, the exact unblock is an owner-approved immutable corpus snapshot with rights/scope manifest, a trusted artifact/source revision resolver, and an independent review/persistence path. Do not treat this contract work as a live source scan or actual human review.
