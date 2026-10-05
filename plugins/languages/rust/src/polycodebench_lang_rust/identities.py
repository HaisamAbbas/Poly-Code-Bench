"""Recorded identities of the pinned Rust images, tools and recipe (Prompt 11).

Miri needs a separate nightly toolchain from the stable compiler, so a Rust tool identity names
both the image it ran in and the exact toolchain that produced the result. The task's ``Cargo.lock``
digest is carried as ``lock_digest``: the lock, the toolchain and the image digest together are
what make two runs comparable (Technical Spec 18.2).
"""

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

PARSER_VERSION = "pcb-rust-parsers-1"
IMAGE_IDENTITY_FILE = "config/images/rust-v1.json"
GUEST_ROOT = "/opt/pcb"
Recipe = Literal["runtime", "evaluator", "performance"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ImageRecord(_Strict):
    recipe: Recipe
    tag: str
    reference: str
    digest: Digest
    rustc: str
    cargo: str
    # Probed for every tool in every recipe; a tool this image does not ship is recorded as
    # ``absent`` rather than omitted, so the record states the recipe difference explicitly.
    tools: dict[str, str]
    expected_tools: tuple[str, ...] = ()
    components: tuple[str, ...]
    guest_and_rules_digest: Digest
    recipe_digest: Digest
    dockerfile_digest: Digest


class ImageIdentities(_Strict):
    schema_version: Literal[1]
    kind: Literal["rust_images"]
    base_image: dict[str, str]
    build: dict[str, object]
    rule_bundle_digest: Digest
    guest_digest: Digest
    images: dict[str, ImageRecord] = Field(min_length=3)

    def record(self, recipe: Recipe) -> ImageRecord:
        return self.images[recipe]

    @property
    def runtime(self) -> ImageRecord:
        return self.images["runtime"]

    @property
    def evaluator(self) -> ImageRecord:
        return self.images["evaluator"]

    @property
    def performance(self) -> ImageRecord:
        return self.images["performance"]

    def tool(
        self,
        name: str,
        *,
        recipe: Recipe = "evaluator",
        parser_version: str = PARSER_VERSION,
        lock_digest: str | None = None,
        advisory_snapshot_digest: str | None = None,
    ) -> ToolIdentity:
        """Identity of one Rust tool.

        ``lock_digest`` is the task's Cargo.lock when the tool's result depends on the dependency
        graph (clippy, cargo audit, Miri); it is left unset for the build/test tools whose result is
        determined by the toolchain and image alone.
        """
        record = self.images[recipe]
        version = record.tools.get(name) or record.rustc
        if name == "miri":
            # Miri is the nightly interpreter, not the stable compiler that builds the task.
            version = f"{version}@{str(self.build.get('miri_toolchain', 'nightly'))}"
        return ToolIdentity(
            name=name,
            version=version,
            image_digest=record.digest,
            lock_digest=lock_digest or record.recipe_digest,
            rule_bundle_digest=self.rule_bundle_digest,
            advisory_snapshot_digest=advisory_snapshot_digest,
            advisory_snapshot_state=(
                "pinned"
                if advisory_snapshot_digest is not None
                else "absent"
                if name == "dependency-check"
                else "not_applicable"
            ),
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
    chosen = path or os.environ.get("PCB_RUST_IMAGE_IDENTITIES")
    location = Path(chosen) if chosen else _find(Path(__file__).resolve().parent)
    document = parse_json_strict(location.read_bytes())
    return ImageIdentities.model_validate(json.loads(json.dumps(document)))
