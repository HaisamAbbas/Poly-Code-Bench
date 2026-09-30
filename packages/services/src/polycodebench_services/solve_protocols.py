"""Load and validate frozen solve protocols from ``config/protocols``."""

from __future__ import annotations

from pathlib import Path

import yaml  # type: ignore[import-untyped]
from polycodebench_core.canonical import canonical_document_digest
from polycodebench_core.solve_contracts import SolveProtocol


def load_solve_protocol(path: Path) -> SolveProtocol:
    """Parse one protocol file strictly; unknown fields and wrong types are rejected."""
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{path.name}: protocol file must be a mapping")
    return SolveProtocol.model_validate(document, strict=False)


def load_protocol_directory(directory: Path) -> dict[str, SolveProtocol]:
    """Every protocol in a directory keyed by id; duplicate ids are an error."""
    protocols: dict[str, SolveProtocol] = {}
    for path in sorted(directory.glob("*.yaml")):
        protocol = load_solve_protocol(path)
        if protocol.protocol_id in protocols:
            raise ValueError(f"duplicate protocol id {protocol.protocol_id}")
        protocols[protocol.protocol_id] = protocol
    return protocols


def protocol_digest(protocol: SolveProtocol) -> str:
    return canonical_document_digest(protocol)
