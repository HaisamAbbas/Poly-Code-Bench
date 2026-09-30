"""Versioned prompt policy ``pcb-solve-v1``: identical instructions for every model.

Anything a model is told comes from here or from the visible task bundle. No hidden path, grader
detail or model-specific wording is ever inserted.
"""

from __future__ import annotations

import json

from polycodebench_core.models import TaskOutputContract
from polycodebench_core.solve_contracts import EffectiveProtocol

PROMPT_POLICY = "pcb-solve-v1"


def system_prompt(effective: EffectiveProtocol) -> str:
    protocol = effective.protocol
    if protocol.mode == "single_shot":
        return (
            "You are solving one software-engineering task in a single response. You have no "
            "tools and will receive no feedback. Follow the required response format exactly; "
            "your response is scored as written and nothing is repaired or reformatted for you."
        )
    return (
        "You are solving one software-engineering task inside an isolated workspace using the "
        "provided tools. Tool calls in one response run one after another in the order you "
        "give them. The sandbox has no network. Processes you start are stopped when each "
        "command ends, so run servers and their clients inside one command. When you are done, "
        "reply without any tool call; that reply ends the session. Your work is scored as it "
        "stands when the session ends. An invalid tool call still uses part of your tool-call "
        "budget."
    )


def output_format(contract: TaskOutputContract, rule: str | None) -> str:
    kind = contract.submission_kind
    if rule == "single_fenced_block":
        return (
            f"Reply with exactly one fenced code block containing the complete contents of "
            f"{contract.allowed_paths[0]}. More than one fenced block makes the answer invalid."
        )
    examples = {
        "files": '{"files": [{"path": "<relative path>", "content": "<complete file text>"}]}',
        "patch": '{"patch": "<unified diff>"}',
        "text": '{"answer": "<text>"}',
        "typed_json": '{"answer": <JSON value>}',
        "structured_findings": (
            '{"findings": [{"local_id": "f1", "path": "<relative path>", "start_line": 1, '
            '"end_line": 1, "symbol": null, "root_cause": "<why>", "evidence": "<how to '
            'reproduce>", "severity": "low|medium|high|critical", "confidence": null}], '
            '"patch": "<optional unified diff>"}'
        ),
    }
    return (
        "Reply with one JSON object and nothing else, using exactly this shape: " + examples[kind]
    )


def task_message(
    *,
    effective: EffectiveProtocol,
    instructions: str,
    contract: TaskOutputContract,
    required_outputs: list[str],
    protected_paths: list[str],
    public_test_groups: list[str],
) -> str:
    protocol = effective.protocol
    parts = ["# Task", instructions.strip(), "", "# Output contract"]
    parts.append(
        json.dumps(
            {
                "submission_kind": contract.submission_kind,
                "allowed_paths": contract.allowed_paths,
                "required_outputs": required_outputs,
                "maximum_files": contract.maximum_files,
                "maximum_file_bytes": contract.maximum_file_bytes,
                "maximum_artifact_bytes": contract.maximum_artifact_bytes,
                "findings_limit": contract.findings_limit,
            },
            sort_keys=True,
            indent=2,
        )
    )
    if protocol.mode == "single_shot":
        parts += ["", "# Response format", output_format(contract, protocol.single_shot_extraction)]
    else:
        parts += ["", "# Workspace"]
        parts.append(
            "The workspace root is the current directory. Protected files cannot be modified: "
            + (", ".join(protected_paths) if protected_paths else "none")
            + "."
        )
        if public_test_groups:
            parts.append("Public test groups: " + ", ".join(public_test_groups) + ".")
        if contract.submission_kind in {"files", "patch"}:
            parts.append(
                "When you finish, the files under the allowed paths are submitted as they "
                "stand in the workspace."
                if contract.submission_kind == "files"
                else "When you finish, your changes under the allowed paths are submitted as a "
                "unified diff against the original workspace."
            )
        else:
            parts += ["", "# Final reply format", output_format(contract, "json_envelope")]
        parts.append("Available tools: " + ", ".join(effective.tools) + ".")
    return "\n".join(parts)
