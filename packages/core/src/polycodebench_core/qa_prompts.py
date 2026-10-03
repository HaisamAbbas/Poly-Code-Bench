"""Versioned prompt policy ``pcb-qa-v1`` for repository Q&A answers (Prompt 27).

The answer a Q&A run must submit is one frozen JSON envelope carrying prose and claim-level
citations tied to the pinned base snapshot. The rendering here is deterministic; what the solver
may see is the question and its own retrieval results, and nothing about the hidden oracle.
"""

from __future__ import annotations

from polycodebench_core.qa_contracts import QaTaskInputs

PROMPT_POLICY = "pcb-qa-v1"

ANSWER_FORMAT = (
    "Reply with exactly one JSON object and no prose around it:\n"
    '{"answer": "<your prose answer>", "claims": [{"text": "<one material claim>", '
    '"citations": [{"path": "<file in the repository snapshot>", "start_line": <int>, '
    '"end_line": <int>, "base_digest": "<the pinned snapshot digest>"}]}]}\n'
    "Cite the exact spans that support each claim. Citation digests must equal the pinned "
    "base snapshot digest. Nothing may be edited: answer from what reading and search return."
)


def system_prompt() -> str:
    """The fixed system text of a Q&A run: retrieval only, structured answer, no editing."""
    return (
        "You answer one question about a pinned repository snapshot. You may list files, read "
        "files and search - nothing else; editing tools do not exist in this protocol. Every "
        "claim you make must cite exact file spans of the base snapshot. Reply only with the "
        "JSON answer envelope described below."
    )


def task_message(inputs: QaTaskInputs) -> str:
    """The user message: the pinned question, the snapshot identity and the answer format."""
    return "\n\n".join(
        (
            inputs.question.strip(),
            f"Pinned base snapshot digest: {inputs.base_digest}",
            f"Snapshot files: {len(inputs.snapshot_paths)} paths (retrieval is read/search only).",
            ANSWER_FORMAT,
        )
    )
