"""Recorded identities of the pinned Python images, tools, locks and rule bundle."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from polycodebench_core.canonical import parse_json_strict
from polycodebench_core.models import Digest
from polycodebench_plugins_api import ToolIdentity
from pydantic import BaseModel, ConfigDict, Field

PARSER_VERSION = "pcb-python-parsers-1"
IMAGE_IDENTITY_FILE = "config/images/python-v1.json"
GUEST_ROOT = "/opt/pcb"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ImageRecord(_Strict):
    tag: str
    reference: str
    digest: Digest
    python: str
    lock_file: str
    lock_digest: Digest
    tools: dict[str, str]
    installed_package_count: int
    guest_and_rules_digest: Digest


class ImageIdentities(_Strict):
    schema_version: Literal[1]
    kind: Literal["python_images"]
    base_image: dict[str, str]
    build: dict[str, object]
    rule_bundle_digest: Digest
    guest_digest: Digest
    images: dict[str, ImageRecord] = Field(min_length=2)

    @property
    def runtime(self) -> ImageRecord:
        return self.images["runtime"]

    @property
    def evaluator(self) -> ImageRecord:
        return self.images["evaluator"]

    def tool(
        self,
        name: str,
        *,
        image_kind: Literal["runtime", "evaluator"] = "evaluator",
        parser_version: str = PARSER_VERSION,
    ) -> ToolIdentity:
        """Identity of one tool; guest scripts report the digest of the shipped guest tree."""
        image = self.images[image_kind]
        version = image.tools.get(name, "guest-" + self.guest_digest[7:19])
        return ToolIdentity(
            name=name,
            version=version,
            image_digest=image.digest,
            lock_digest=image.lock_digest,
            rule_bundle_digest=self.rule_bundle_digest,
            advisory_snapshot_digest=None,
            advisory_snapshot_state=("absent" if name == "dependency-check" else "not_applicable"),
            parser_version=parser_version,
        )


def _find(start: Path) -> Path:
    for parent in (start, *start.parents):
        candidate = parent / IMAGE_IDENTITY_FILE
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(IMAGE_IDENTITY_FILE)


@lru_cache(maxsize=4)
def load_identities(path: str | None = None) -> ImageIdentities:
    """Load the recorded identities (``PCB_IMAGE_IDENTITIES`` overrides the repository file)."""
    chosen = path or os.environ.get("PCB_IMAGE_IDENTITIES")
    location = Path(chosen) if chosen else _find(Path(__file__).resolve().parent)
    document = parse_json_strict(location.read_bytes())
    return ImageIdentities.model_validate(json.loads(json.dumps(document)))
