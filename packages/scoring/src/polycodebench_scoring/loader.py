"""File-boundary loaders for the scorer.

Everything that touches a filesystem lives here, so ``scorer.py`` can be a pure function of already
parsed documents. Loading is strict: the configuration files are validated into their contracts
directly, so an unknown key, a floating-point weight or an unexpected severity is an error rather
than a silently dropped field.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from polycodebench_core.canonical import parse_json_strict
from polycodebench_core.models import Scorecard
from polycodebench_plugins_api import FrozenTask, LanguageProfile
from pydantic import BaseModel

from polycodebench_scoring.errors import ScoringConfigError
from polycodebench_scoring.manifest import ValidatedEvidenceManifest
from polycodebench_scoring.ownership import EvidenceOwnership
from polycodebench_scoring.policy import FrozenScoringPolicy
from polycodebench_scoring.scorer import ScoringOutcome

# The scorer owns these two documents, so they validate straight from YAML. Everything else is a
# runtime artifact and is exchanged as strict canonical JSON.
_YAML_KINDS: dict[type[BaseModel], tuple[str, ...]] = {
    FrozenScoringPolicy: ("frozen_scoring_policy",),
    EvidenceOwnership: ("evidence_ownership",),
}


def load_scoring_policy(path: Path) -> FrozenScoringPolicy:
    return _load_yaml(path, FrozenScoringPolicy, "scoring policy")


def load_evidence_ownership(path: Path) -> EvidenceOwnership:
    return _load_yaml(path, EvidenceOwnership, "evidence ownership policy")


def load_frozen_task(path: Path) -> FrozenTask:
    return _load_json(path, FrozenTask, "frozen task")


def load_language_profile(path: Path) -> LanguageProfile:
    return _load_json(path, LanguageProfile, "language profile")


def load_evidence_manifest(path: Path) -> ValidatedEvidenceManifest:
    return _load_json(path, ValidatedEvidenceManifest, "validated evidence manifest")


def load_outcome(path: Path) -> ScoringOutcome:
    return _load_json(path, ScoringOutcome, "scoring outcome")


def load_scorecard(path: Path) -> Scorecard:
    return _load_json(path, Scorecard, "scorecard")


def archive_document(document: BaseModel) -> dict[str, Any]:
    """The faithful archive form of a document: every field, in a JSON-safe shape.

    This is deliberately *not* ``canonical_document_bytes``. The repository excludes
    ``run_id``, ``candidate_id`` and ``scorecard_id`` from canonical bytes because they are assigned
    at persistence time - but scoring needs them as inputs, so an archive that dropped them could
    not be replayed. The archive keeps everything; the digest recorded on the scorecard is the
    document's own content digest, which also covers them.
    """
    payload = document.model_dump(mode="json")
    if not isinstance(payload, dict):  # pragma: no cover - every document is a model object
        raise ScoringConfigError("only object documents can be archived")
    return payload


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _load_yaml[T: BaseModel](path: Path, model: type[T], label: str) -> T:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise ScoringConfigError(f"{path} does not contain a YAML mapping")
    payload = _unwrap_envelope(dict(document), model)
    expected = _YAML_KINDS.get(model, ())
    if expected and payload.get("kind") not in expected:
        raise ScoringConfigError(
            f"{path} declares kind {payload.get('kind')!r}; expected one of {list(expected)}"
        )
    # YAML sequences and dates are not JSON. Routing through JSON gives the strict models the
    # array and string types they require instead of coercing them quietly.
    try:
        return model.model_validate_json(json.dumps(payload, default=str))
    except ValueError as error:
        raise ScoringConfigError(f"{path} is not a valid {label}: {error}") from error


def _load_json[T: BaseModel](path: Path, model: type[T], label: str) -> T:
    value = parse_json_strict(path.read_bytes())
    if not isinstance(value, dict):
        raise ScoringConfigError(f"{path} must contain a JSON object")
    try:
        # Strict mode maps a JSON array onto a tuple field and a JSON string onto a str field; a
        # Python dict would hand it lists and dates it is supposed to reject.
        return model.model_validate_json(json.dumps(_unwrap_envelope(value, model)))
    except ValueError as error:
        raise ScoringConfigError(f"{path} is not a valid {label}: {error}") from error


def _unwrap_envelope(document: dict[str, Any], model: type[Any]) -> dict[str, Any]:
    """Accept a bare document or the canonical ``{kind, schema_version, payload}`` envelope.

    Archives are stored in the canonical envelope so their bytes hash to the digest recorded on the
    scorecard. Reading both shapes means an archived file replays directly, with no translation
    step that could quietly drop a field.
    """
    payload = document.get("payload")
    if isinstance(payload, dict) and "payload" in document:
        if document.get("kind") != _model_kind(model):
            raise ScoringConfigError(
                f"canonical envelope declares kind {document.get('kind')!r}; expected "
                f"{_model_kind(model)!r}"
            )
        return dict(payload)
    return document


def _model_kind(model: type[Any]) -> str | None:
    default = model.model_fields.get("kind")
    return default.default if default is not None and isinstance(default.default, str) else None


__all__ = [
    "ScoringConfigError",
    "Scorecard",
    "archive_document",
    "load_evidence_manifest",
    "load_evidence_ownership",
    "load_frozen_task",
    "load_language_profile",
    "load_outcome",
    "load_scorecard",
    "load_scoring_policy",
    "write_json",
]
