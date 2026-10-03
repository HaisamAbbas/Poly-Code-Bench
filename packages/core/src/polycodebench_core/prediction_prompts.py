"""Versioned prompt policy ``pcb-prediction-v1`` for answer-only prediction (Prompt 28).

An output-prediction run submits one frozen JSON envelope carrying the predicted output. What the
solver may see is the target source, its input and the scenario statement - and nothing else. The
protocol carries no execution tool (``config/protocols/prediction-v1.yaml``), so the model cannot
run the target to find the answer; the restriction is structural rather than an instruction the
model could talk its way around.

The rendering here is deterministic. Nothing in it mentions the hidden oracle: the model sees the
task, not the answer it is graded against.
"""

from __future__ import annotations

PROMPT_POLICY = "pcb-prediction-v1"

ANSWER_FORMAT = (
    "Reply with exactly one JSON object and no prose around it:\n"
    '{"prediction": "<the exact output you predict>"}\n'
    "Predict only what the task asks for. You cannot execute anything: no command, test or patch "
    "tool exists in this protocol. State the output exactly as the task's normalization rules "
    "describe it."
)


def system_prompt() -> str:
    """The fixed system text of a prediction run: read only, predict only, no execution."""
    return (
        "You predict the output of supplied code from reading it alone. No command, test or patch "
        "tool exists in this protocol, so the code cannot be run; reason from the source and the "
        "scenario. Reply only with the JSON prediction envelope described below."
    )


BEGIN_SOURCE = "----- BEGIN TARGET SOURCE -----"
END_SOURCE = "----- END SOURCE -----"
BEGIN_STDIN = "----- BEGIN STANDARD INPUT -----"
END_STDIN = "----- END STANDARD INPUT -----"


def task_message(
    *,
    problem: str,
    target_paths: tuple[str, ...],
    target_sources: str,
    stdin: str | None,
    scenario: str | None,
    normalization_rules: str,
) -> str:
    """The user message: the problem, the target source, the input and the normalization rules."""
    parts = [problem.strip()]
    parts.append(f"Target modules: {', '.join(target_paths)}")
    parts.append(f"{BEGIN_SOURCE}\n{target_sources.strip()}\n{END_SOURCE}")
    if stdin is not None:
        parts.append(f"{BEGIN_STDIN}\n{stdin}\n{END_STDIN}")
    if scenario is not None:
        parts.append(f"Scenario: {scenario.strip()}")
    parts.append("Normalization rules (frozen): " + normalization_rules)
    parts.append(ANSWER_FORMAT)
    return "\n\n".join(parts)


def normalization_statement(mode: str, rules: str) -> str:
    """One line naming the task's declared normalization, for evidence and reports."""
    return f"normalization mode {mode}: {rules}"