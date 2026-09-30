# Methodology source and terms register

Status describes evidence available in this repository as of 2026-09-30; it is not legal advice or an authorization to redistribute source data.

| Source | Method source | Artifact/code terms observed | Required follow-up before use |
|---|---|---|---|
| SWE-bench | [Harness](https://www.swebench.com/SWE-bench/reference/harness/), [harness API](https://www.swebench.com/SWE-bench/api/harness/), [overview](https://www.swebench.com/original.html), [multilingual](https://www.swebench.com/multilingual.html) (checked 2026-09-30) | No selected dataset/repository/patch rights cleared | Pin dataset/harness/image; inspect dataset and each repository license; establish task redistribution policy |
| LiveCodeBench | [Project/paper](https://livecodebench.github.io/), [paper PDF](https://livecodebench.github.io/pdfs/paper.pdf), [repository](https://github.com/LiveCodeBench/LiveCodeBench) (checked 2026-09-30) | Upstream code repository reports MIT; contest problem sources and artifacts require per-source review | Pin code and dataset revisions; inspect problem provenance and source-specific licenses |
| CursorBench | [Public methodology article](https://cursor.com/blog/cursorbench) (checked 2026-09-30) | Internal task suite and operational graders unavailable; no private assets or rights granted | Independently curate and clear each repository/task; label adaptations CursorBench-inspired |
| DeepCodeBench | [Method article](https://www.qodo.ai/blog/deepcodebench-real-world-codebase-understanding-by-qa-benchmarking/), [dataset card](https://huggingface.co/datasets/Qodo/deep_code_bench) (checked 2026-09-30) | Dataset card reports Apache-2.0; underlying repository code terms are separate | Pin exact dataset artifact; inspect source repos and attribution; calibrate judge before any public claim |

No external source assets were copied into this workspace. The authored Prompt 05 test fixture is separately identified and carries no benchmark score. Human calibration, source permissions, and external benchmark execution remain open gates. Record source revisions, access dates, task provenance, and terms alongside each admitted task bundle. The source article's public description does not grant access or redistribution rights for CursorBench private tasks.
