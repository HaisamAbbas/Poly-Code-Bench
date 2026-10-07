# Implementation evidence index

Audit refresh: 2026-10-07. The detailed E2E matrix is the index for scenario-level setup, assertions, tier and status: [`e2e-matrix.md`](e2e-matrix.md). Fixture, local-development, production, human-calibration and live-provider evidence are not interchangeable.

## Product and operations

| Scope | Evidence |
|---|---|
| Public API, OIDC, PostgreSQL, synthetic release data, seven public workflows | [`reports/prompt-33.md`](reports/prompt-33.md), [`evidence/prompt-33/local-functional-stack-2026-10-07.json`](evidence/prompt-33/local-functional-stack-2026-10-07.json) |
| Leaderboard, language/model profiles, responsive and keyboard checks | [`reports/prompt-30.md`](reports/prompt-30.md), [`evidence/prompt-30/`](evidence/prompt-30/) |
| Comparison, task/evidence drilldowns, methodology and correction views | [`reports/prompt-31.md`](reports/prompt-31.md), [`evidence/prompt-31/`](evidence/prompt-31/) |
| Metadata-only model submissions, ownership, review, bounded approval and status | [`reports/prompt-32.md`](reports/prompt-32.md), [`evidence/prompt-32/`](evidence/prompt-32/), [`evidence/e2e-25-role-matrix-2026-10-07.json`](evidence/e2e-25-role-matrix-2026-10-07.json) |
| Restricted public artifacts, catalog and privacy/export behavior | [`evidence/e2e-26-artifact-routes-2026-10-07.json`](evidence/e2e-26-artifact-routes-2026-10-07.json) |
| Local worker assembly and exact solve-claim filters; dispatch disabled | [`evidence/prompt-33/local-solve-worker-2026-10-06.json`](evidence/prompt-33/local-solve-worker-2026-10-06.json), [`evidence/prompt-33/worker-exact-claim-2026-10-07.json`](evidence/prompt-33/worker-exact-claim-2026-10-07.json) |
| Restore/drills and staging blockers | [`reports/phase-7.md`](reports/phase-7.md), [`evidence/prompt-33/`](evidence/prompt-33/), [`docs/operations/alibaba-trial-readiness.md`](../operations/alibaba-trial-readiness.md) |

## Evaluation, scoring and release

| Scope | Evidence |
|---|---|
| Python/Rust independent evaluator and failure cases | [`evidence/prompt-12-eval-python.json`](evidence/prompt-12-eval-python.json), [`evidence/prompt-12-eval-rust.json`](evidence/prompt-12-eval-rust.json), [`tests/test_evaluator_docker.py`](../../tests/test_evaluator_docker.py) |
| Fixture judge, vote and calibration mechanics | [`reports/prompt-14.md`](reports/prompt-14.md), [`evidence/prompt-14-calibration.json`](evidence/prompt-14-calibration.json) |
| Exact scoring and replay; scoring remains inactive pending calibration | [`reports/prompt-15.md`](reports/prompt-15.md), [`evidence/prompt-15-scoring.json`](evidence/prompt-15-scoring.json), [`evidence/prompt-33/e2e-42-local-restore.json`](evidence/prompt-33/e2e-42-local-restore.json) |
| Synthetic aggregation, release lifecycle and correction/withdrawal mechanics | [`reports/prompt-16.md`](reports/prompt-16.md), [`reports/prompt-29.md`](reports/prompt-29.md), [`reports/prompt-33.md`](reports/prompt-33.md) |
| Actual pilot accounting: planned 144, dispatched 0, completed 0 | [`reports/prompt-17.md`](reports/prompt-17.md), [`evidence/prompt-17-preflight.json`](evidence/prompt-17-preflight.json) |

## Language and task-family coverage

| Scope | Evidence |
|---|---|
| Current extension-language status and remaining TypeScript/quality/production gaps | [`reports/language-coverage.md`](reports/language-coverage.md), [`e2e-matrix.md`](e2e-matrix.md) |
| All allowlisted language image digests compared with local Docker image IDs | [`evidence/prompt-34/language-image-identity-2026-10-07.json`](evidence/prompt-34/language-image-identity-2026-10-07.json) |
| Repository repair, realistic repository tasks, self-repair, Q&A and prediction | [`reports/prompt-24.md`](reports/prompt-24.md) through [`reports/prompt-28.md`](reports/prompt-28.md), [`reports/suite-coverage.md`](reports/suite-coverage.md), per-prompt evidence under [`evidence/`](evidence/) |
| Track A internal detection, adjudication, patch and clean-control mechanics | [`reports/prompt-18.md`](reports/prompt-18.md), `evidence/prompt-18-e2e-32.json` through `evidence/prompt-18-e2e-34.json` |

## Prompt 34 refresh commands

- `uv run python docs/implementation/verify_prompt00.py` — passed; validates 14 requirements, 24 work packages, 43 E2E rows, 142 tickets, progress state and authoritative source hashes.
- `git diff --check` — passed for the audit edits.
- `python -m json.tool docs/implementation/progress.json` — passed.
- `http://127.0.0.1:8010/readyz` and `http://127.0.0.1:3001/leaderboard` — both returned HTTP 200 on the loopback-only local stack.
- A read-only Docker comparison matched all 34 allowlisted digests across eight language identities to local `v1` images. It did not build, pull or run an image.

No live model/provider call, cloud operation, production VM test, human calibration, public release or ranked benchmark result is represented by this refresh.
