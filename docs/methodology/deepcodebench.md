# DeepCodeBench methodology record

## Primary sources and identity

- [Qodo's DeepCodeBench methodology article](https://www.qodo.ai/blog/deepcodebench-real-world-codebase-understanding-by-qa-benchmarking/) (accessed 2026-09-30).
- [DeepCodeBench dataset card](https://huggingface.co/datasets/Qodo/deep_code_bench) (accessed 2026-09-30); referenced snapshot commit `61c6c6738c275032172f8405f65f1e7f14d443c4` is reported by the dataset source. No dataset files were downloaded.

## Verified native method

The published benchmark describes 1,144 repository question-and-answer items constructed from pull-request context across eight code repositories. Answers are evaluated for the presence of discrete ground-truth facts, using an LLM judge; fact recall is the native focus. Confirm the dataset card and paper/repository revision before any run because source assets and evaluation instructions can change.

## PolyCodeBench treatment

Preserve fact recall as a separately named native-style measure. Add citation validity, evidence grounding, and unsupported-claim measures only as distinct PolyCodeBench adaptations; do not redefine them as DeepCodeBench metrics. Pin code snapshots and judge prompt/model identity for any implemented adapter.

## Assets, limitations, and rights

The dataset card reports Apache-2.0 for the dataset artifact; that label alone does not grant rights over the underlying repositories or supersede their individual terms. Confirm the exact artifact revision, repository licenses, attribution, and permitted use before importing. No tasks were downloaded or executed during Prompt 01. Judge calibration is an open prerequisite.
