"""Recorded identities of the pinned C++ images, tools and recipes.

A C++ result is only comparable to another C++ result when the same compiler, the same sanitizers
and the same rule bundle produced it, so a tool identity names all of them: the image it ran in,
the exact tool version reported *by that image*, the pinned toolchain lock digest and the rule
bundle digest. ``config/languages/cpp-toolchain-v1.json`` is the dependency-lock analogue that C++
has no file format for.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from polycodebench_core.canonical import parse_json_strict
from polycodebench_core.models import Digest
from polycodebench_plugins_api import ToolIdentity
from pydantic import BaseModel, ConfigDict, Field

from polycodebench_lang_cpp.locks import Recipe, ToolchainLock, lock_digest

PARSER_VERSION = "pcb-cpp-parsers-1"
IMAGE_IDENTITY_FILE = "config/images/cpp-v1.json"
GUEST_ROOT = "/opt/pcb"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ImageRecord(_Strict):
    recipe: Recipe
    tag: str
    reference: str
    digest: Digest
    clang: str
    tools: dict[str, str]
    expected_tools: tuple[str, ...] = ()
    components: tuple[str, ...] = ()
    guest_and_rules_digest: Digest
    recipe_digest: Digest
    dockerfile_digest: Digest
    release_flags: str = ""


class ImageIdentities(_Strict):
    schema_version: Literal[1]
    kind: Literal["cpp_images"]
    base_image: dict[str, str]
    build: dict[str, object]
    guest_digest: Digest
    rule_bundle_digest: Digest
    images: dict[str, ImageRecord] = Field(min_length=3)

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
        lock: ToolchainLock | None = None,
        parser_version: str = PARSER_VERSION,
    ) -> ToolIdentity:
        record = self.images[recipe]
        # The shared identity field is a slug, while the executable is spelled ``clang++``.
        # Preserve the actual image-manifest lookup but use a stable slug in cross-language data.
        manifest_name = "clang++" if name == "clang-plus-plus" else name
        return ToolIdentity(
            name=name,
            version=record.tools.get(manifest_name) or record.clang,
            image_digest=record.digest,
            lock_digest=str(lock_digest(lock)) if lock is not None else record.recipe_digest,
            rule_bundle_digest=self.rule_bundle_digest,
            advisory_snapshot_digest=None,
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
    """Load the recorded identities (``PCB_CPP_IMAGE_IDENTITIES`` overrides the repository file)."""
    override = os.environ.get("PCB_CPP_IMAGE_IDENTITIES")
    if override:
        source = Path(override)
    elif path:
        source = Path(path)
    else:
        source = _find(Path(__file__).resolve())
    identities = ImageIdentities.model_validate(parse_json_strict(source.read_bytes()))
    missing = {"runtime", "evaluator", "performance"} - set(identities.images)
    if missing:
        raise ValueError(f"image identities are missing recipe(s): {sorted(missing)}")
    digests = {
        identities.images[recipe].digest for recipe in ("runtime", "evaluator", "performance")
    }
    if len(digests) != 3:
        raise ValueError(
            "the runtime, evaluator and performance recipes must be three distinct images"
        )
    return identities


def tool_identity_name(binary: str) -> str:
    """The name a compiler is recorded under.

    ``ToolIdentity.name`` is a slug, so the binary's ``clang++`` cannot be spelled verbatim. The
    identity records ``clang-c++`` while the version, image digest and toolchain-lock digest carry
    the rest; dropping the ``+`` changes how a name looks, never which tool it is.
    """
    return re.sub(r"[^a-z0-9._-]+", "-", binary.lower()).strip("-.")


__all__ = [
    "GUEST_ROOT",
    "IMAGE_IDENTITY_FILE",
    "ImageIdentities",
    "ImageRecord",
    "PARSER_VERSION",
    "Recipe",
    "load_identities",
    "tool_identity_name",
]
