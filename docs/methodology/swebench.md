# SWE-bench methodology record

## Primary sources and identity

- [SWE-bench evaluation harness reference](https://www.swebench.com/SWE-bench/reference/harness/) and [harness API](https://www.swebench.com/SWE-bench/api/harness/) (checked 2026-09-30); source revision is not pinned by the public reference.
- [SWE-bench original overview](https://www.swebench.com/original.html) (accessed 2026-09-30).
- [SWE-bench multilingual benchmark](https://www.swebench.com/multilingual.html) (accessed 2026-09-30).

## Verified native method

The official harness applies a proposed repository patch in a Docker task environment and maps test outcomes into fail-to-pass (resolution) and pass-to-pass (maintenance) measures. Its resolution status uses both measures; tests absent from the task's expected test lists are not silently turned into a new metric. Multilingual variants and their native datasets keep their own task identities and evaluation assumptions. These statements summarize the cited harness documentation; the exact harness/data revision still must be pinned for an imported release.

## PolyCodeBench treatment

Import supported official data through a versioned adapter and preserve the source's native fail-to-pass/pass-to-pass measures. Any added quality dimensions are separately named PolyCodeBench measures. Locally authored tasks that follow similar repository-patch patterns are labeled **SWE-bench-inspired** and are not mixed into official SWE-bench scores. Do not feed hidden grading failures to a solve session.

## Assets, limitations, and rights

The exact evaluation depends on source repositories, commits, task patches, test commands, and benchmark-specific execution assets. Their availability and licenses must be checked per dataset release and repository snapshot. No task data or image was imported in Prompt 01; this record does not establish rights to redistribute upstream repositories, patches, or derived test data. Image identity and harness revision remain to be pinned during task admission.
