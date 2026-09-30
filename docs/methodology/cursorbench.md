# CursorBench methodology record

## Primary source and identity

- [How we compare model quality in Cursor](https://cursor.com/blog/cursorbench) (checked 2026-09-30). The article describes Cursor's internal benchmark; no private task-set revision or raw task data is available in this workspace.

## Verified native method

Cursor reports an offline suite based on real engineering sessions from its internal evaluation workflow and measures correctness, code quality, efficiency, and interaction behavior. The article describes task requests traced through Cursor Blame and agentic graders for short, potentially ambiguous requests, and an online/offline evaluation loop. Its May 2026 update identifies production version 3.1 and describes correctness alongside median completion tokens; it does not publish a complete score formula. The article does not provide the private task set and all operational grader assets needed for reproduction.

## PolyCodeBench treatment

PolyCodeBench may independently curate realistic repository tasks and evaluate them with explicit acceptance evidence. Such tasks are **CursorBench-inspired**, not CursorBench tasks or replications. Do not claim access to internal tasks, exact graders, or comparable scores. Publish PolyCodeBench task provenance and rubric versions instead.

## Assets, limitations, and rights

Private task data, source code context, and grader implementation are unavailable from the public methodology description. Rights for independently collected repositories, prompts, and task artifacts remain a per-task admission requirement. No Cursor private asset has been imported.
