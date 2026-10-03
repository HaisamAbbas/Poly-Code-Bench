"""Versioned prompt policy ``pcb-repair-v1`` for self-repair rounds (Prompt 26).

Every repair round is one single-shot model call whose message is a pure function of the frozen
task statement, the round's budget text and the *public* feedback that drove the round. The same
inputs always render the same bytes, so a restart replays an identical request and the ledger's
stored-response rule (Technical Spec 7.5) can consume it exactly once.

Hidden material can never enter these strings: feedback arrives as ``RepairFeedback``, whose
constructors refuse non-public cases, and the rendered text is what the model is told about
results at all.
"""

from __future__ import annotations

from polycodebench_core.repair_contracts import RepairFeedback, RepairLimits
from polycodebench_core.solve_contracts import SolveError

PROMPT_POLICY = "pcb-repair-v1"

_SYSTEM = (
    "You are repairing one file in a small repository. You receive the task, your previous "
    "attempt's public test results, and nothing else about hidden evaluation. Reply with exactly "
    "one JSON object {\"files\": [{\"path\": str, \"content\": str}, ...]} containing the complete "
    "content of every file you change. No prose outside the JSON object."
)


def system_prompt() -> str:
    """The fixed system text of every repair round."""
    return _SYSTEM


def round_task_message(
    task_statement: str,
    *,
    round_index: int,
    limits: RepairLimits,
    feedback: RepairFeedback | None,
) -> str:
    """The user message of one repair round. Deterministic; public feedback only."""
    if round_index == 0 and feedback is not None:
        raise SolveError("the initial round has no feedback section")
    if round_index > 0 and feedback is None:
        raise SolveError("a repair round renders the feedback that drove it")
    header = (
        f"Repair round {round_index} of at most {limits.maximum_repair_rounds} "
        f"(model calls left after this one: at most "
        f"{max(0, limits.maximum_model_calls - round_index - 1)})."
    )
    sections = [task_statement.strip(), header]
    if feedback is not None:
        sections.append(feedback.render())
    sections.append(
        "Submit the complete corrected file contents as one JSON object as instructed. "
        + "Base your repair only on the task and the public test feedback above."
    )
    return "\n\n".join(sections)
