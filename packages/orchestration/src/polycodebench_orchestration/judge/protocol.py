"""The judge request protocol: one turn, no tools, a structured response, and a refusal if that
ever changes.

The judge reuses the frozen ``single-shot-v1`` protocol definition, checked here: if a future edit
ever gave the judge a tool or a tool-call budget, this module refuses to build the request rather
than trusting the configuration.
"""

from __future__ import annotations

from pathlib import Path

from polycodebench_core.judge_contracts import JudgeError
from polycodebench_core.models import ProtocolDefinition
from polycodebench_services.solve_protocols import load_protocol_directory

JUDGE_PROTOCOL_ID = "single-shot-v1"
PROTOCOL_DIRECTORY = Path("config") / "protocols"


def default_protocol_directory() -> Path:
    """``config/protocols`` from the working directory, or from the workspace root."""
    if PROTOCOL_DIRECTORY.is_dir():
        return PROTOCOL_DIRECTORY
    for parent in Path(__file__).resolve().parents:
        candidate = parent / PROTOCOL_DIRECTORY
        if candidate.is_dir():
            return candidate
    raise JudgeError(f"{PROTOCOL_DIRECTORY} is not installed")


def load_judge_protocol(directory: Path | None = None) -> ProtocolDefinition:
    """Load the judge protocol and refuse any definition that exposes an execution tool."""
    location = directory or default_protocol_directory()
    protocols = load_protocol_directory(location)
    try:
        solve_protocol = protocols[JUDGE_PROTOCOL_ID]
    except KeyError:
        raise JudgeError(f"{JUDGE_PROTOCOL_ID} is not installed in {location}") from None
    protocol = solve_protocol.to_definition()
    if protocol.allowed_tools or protocol.maximum_tool_calls:
        raise JudgeError("the judge protocol must expose no tools")
    if protocol.mode != "single_shot" or protocol.maximum_turns != 1:
        raise JudgeError("the judge protocol must be one turn with no tools")
    return protocol
