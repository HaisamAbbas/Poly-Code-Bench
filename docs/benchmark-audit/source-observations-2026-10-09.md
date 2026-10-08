# Official benchmark metadata observations — 2026-10-09

These are read-only metadata and documentation checks for Prompt104 / BWP-22. No benchmark payload, hidden answer file, model, Docker environment, dataset script, or native evaluation harness was fetched or executed. A repository commit pins repository metadata; it does not establish payload rights or, by itself, pin a dataset snapshot.

## Pinned repository and dataset metadata

| Family | Observed metadata revision | Scope and remaining limit |
|---|---|---|
| LiveCodeBench | `LiveCodeBench/LiveCodeBench` HEAD `28fef95ea8c9f7a547c8329f2cd3d32b92c1fa24` | Repository metadata only. No release/date window, contest membership, task bytes, or rights review is frozen. |
| MMLU | `hendrycks/test` HEAD `4450500f923c49f1fb1dd3d99108a0bd9717b660` | Evaluation repository metadata only. Subject CSV membership and upstream exam provenance remain unresolved. |
| MMLU-Pro | GitHub HEAD `f418b116db00b065c2aea046518d8fcf74d39872`; official Hugging Face dataset HEAD `b189ec765aa7ed75c8acfea42df31fdae71f97be` | Dataset repository revision is pinned without reading payload files. Official README describes 10 options and over 12,000 questions; selected split, item rights and source provenance remain unresolved. [Official repository](https://github.com/TIGER-AI-Lab/MMLU-Pro) |
| GPQA | `idavidrein/gpqa` HEAD `56686c06f5e19865c153de0fdb11be3890014df7` | Public repository metadata only. The benchmark dataset is gated; no grant, credential, or dataset byte was accessed. |
| GSM8K | `openai/grade-school-math` HEAD `3101c7d5072418e28b9008a6636bde82a006892c` | Official README documents `train.jsonl` / `test.jsonl`, `question` / `answer`, and the final `####` answer marker. Only a synthetic fixture exercises the local parser; no official row was read. [Official README](https://github.com/openai/grade-school-math) |
| MATH | `hendrycks/math` HEAD `985bdc1696e88e8643f081a0ff4719da39f2ae2a` | Evaluation repository metadata only; no dataset archive, competition-source rows, or split bytes were fetched. |
| MGSM | `google-research/url-nlp` HEAD `452a21ad3dae5668c06ceeac21ff073e1e40f9be` | Repository metadata only. Language membership, translation lineage and source rows remain unverified. |
| BIG-Bench Hard | `suzgunmirac/BIG-Bench-Hard` HEAD `9ee07bd481feebf959a6b59d61ea57bdcf30964d` | Official repository describes 23 task files plus separate chain-of-thought prompt templates. No task payload was fetched; template ancestry and item rights remain unreviewed. [Official README](https://github.com/suzgunmirac/BIG-Bench-Hard) |
| ARC | Official AI2 data page resolves to `allenai/ai2_arc`, HEAD `210d026faf9955653af8916fad021475a3f00453` | Dataset repository revision metadata only. Easy/Challenge split policy is recorded, but selected membership and item rights are not approved. |
| TruthfulQA | `sylinrl/TruthfulQA` HEAD `d71c110897f5d31c5d7f309e7bc316c152f6f031` | Current official README describes a newer two-choice multiple-choice form in addition to earlier MC1/MC2 forms. These are separate variants; no source rows or checker ran. [Official README](https://github.com/sylinrl/TruthfulQA) |
| IFEval | `google-research/google-research` HEAD `72ffe35f5a24a312980e6bb811713b24f31e11f9` | Repository metadata only. The official README specifies prompt/response JSONL use; prompt, instruction IDs, checker revision, and data bytes remain unpinned. [Official README](https://github.com/google-research/google-research/tree/master/instruction_following_eval) |
| Terminal-Bench | Current Harbor repository HEAD `d28711d0da2675d0bb1d56de45ae5df6082438a3`; official README names Terminal-Bench-Core `0.1.1` | Version metadata only. Task/environment assets and split membership were not fetched, and no Docker environment or hidden asset was executed. [Official repository](https://github.com/harbor-framework/terminal-bench-1) |
| BFCL | `ShishirPatil/gorilla` HEAD `6ea57973c7a6097fd7c5915698c54c17c5b1b6c8` | Repository metadata only. Upstream documents multiple releases and tool-call categories; this catalog does not freeze one data release or run its tool environment. |
| MMMU | `MMMU-Benchmark/MMMU` HEAD `268471d0d488258990025331c7528359c324aa25` | Official repository describes image-heavy questions with heterogeneous visual assets. No image bytes, OCR, perceptual checks, or rights evidence were acquired. [Official repository](https://github.com/MMMU-Benchmark/MMMU) |

The already-recorded P84 pins for HumanEval, MBPP, EvalPlus and SWE-bench remain metadata-only. The MMLU-Pro and ARC Hugging Face commit IDs are dataset-repository revisions, not evidence of fetched or licensed source bytes.

## Explicit source/access blockers

- GAIA's official dataset page requires approval to share contact information and says the data must not be reshared outside gated or private storage. A read-only Git metadata request returned an authentication-required response. No login, approval request, payload, answers, or attachments were accessed. [Official dataset page](https://huggingface.co/datasets/gaia-benchmark/GAIA)
- HellaSwag's official GitHub repository returned a DMCA takedown response. GitHub's 2026-09-14 notice names `rowanz/hellaswag` among repositories alleged to contain unauthorized WikiHow copies. No source content or mirror was accessed; importer/conformance remains blocked pending rights resolution. [GitHub notice](https://github.com/github/dmca/blob/master/2026/09/2026-09-14-wikihow.md)
- The catalog's old `allenai/ai2_arc` GitHub repository path no longer resolves. The official [AI2 ARC data page](https://allenai.org/data/arc) redirects to the Hugging Face dataset repository whose revision is recorded above.
- A first GSM8K `git ls-remote` request received an empty server reply; an immediate read-only retry returned the same official repository HEAD recorded above.
- The official MAA page describes AIME competitions, not one canonical third-party AIME-derived dataset. No competition-year dataset is selected or imported. [MAA Invitational Competitions](https://maa.org/maa-invitational-competitions/)

## Commands and limits

Repository metadata was queried with `git ls-remote <official-repository> HEAD`; Hugging Face repository HEAD queries used the same ref-only operation. These commands enumerate refs and do not fetch Git objects or dataset files. The GAIA auth failure and HellaSwag DMCA response were retained as blockers. Web checks were limited to official benchmark, owner, or platform pages linked above.

The five-source bridge in the implementation specification currently lists only three historical source MDs, and only those three are present in the workspace. This source-list discrepancy is recorded separately; no missing document was reconstructed or substituted.
