# DeepCodeBench methodology record

## Primary sources and identity

- [Qodo's DeepCodeBench methodology article](https://www.qodo.ai/blog/deepcodebench-real-world-codebase-understanding-by-qa-benchmarking/) (checked 2026-09-30).
- [DeepCodeBench dataset card](https://huggingface.co/datasets/Qodo/deep_code_bench) (checked 2026-09-30); referenced dataset snapshot commit `61c6c6738c275032172f8405f65f1e7f14d443c4` is reported by the source. No dataset files were downloaded.

## Verified native method

The published benchmark describes 1,144 repository question-and-answer items built from pull-request context across eight code repositories. Its article describes extracting discrete facts from ground-truth answers and using an LLM call to check their presence in predictions; fact recall is the native focus. Confirm the dataset card, selected dataset revision, and evaluation instructions before any run because source assets and terms may change.

## PolyCodeBench treatment

Preserve fact recall as a separately named native-style measure. Add citation validity, evidence grounding, and unsupported-claim measures only as distinct PolyCodeBench adaptations; do not redefine them as DeepCodeBench metrics. Pin code snapshots and judge prompt/model identity for any implemented adapter.

## Assets, limitations, and rights

The dataset card reports Apache-2.0 for the dataset artifact; that label alone does not grant rights over the underlying repositories or supersede their individual terms. Confirm the exact artifact revision, repository licenses, attribution, and permitted use before importing. No tasks were downloaded or executed during Prompt 01. Judge calibration is an open prerequisite.
