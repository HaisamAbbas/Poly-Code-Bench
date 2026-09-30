# CursorBench methodology record

## Primary source and identity

- [CursorBench: evaluating coding agents on real-world tasks](https://cursor.com/blog/cursorbench) (accessed 2026-09-30). The page describes Cursor's internal benchmark; no private task-set revision or raw task data is available in this workspace.

## Verified native method

Cursor reports tasks drawn from real engineering sessions and evaluation across correctness, code quality, efficiency, and interaction behavior. The described process uses task requests derived from Cursor Blame and agentic graders for short, potentially ambiguous requests. The public article says the current production version is CursorBench 3.1 (updated May 2026) and discusses correctness alongside median completion tokens; it does not specify a public complete score formula. The article does not provide the private task set and all operational grader assets needed for reproduction.

## PolyCodeBench treatment

PolyCodeBench may independently curate realistic repository tasks and evaluate them with explicit acceptance evidence. Such tasks are **CursorBench-inspired**, not CursorBench tasks or replications. Do not claim access to internal tasks, exact graders, or comparable scores. Publish PolyCodeBench task provenance and rubric versions instead.

## Assets, limitations, and rights

Private task data, source code context, and grader implementation are unavailable from the public methodology description. Rights for independently collected repositories, prompts, and task artifacts remain a per-task admission requirement. No Cursor private asset has been imported.
