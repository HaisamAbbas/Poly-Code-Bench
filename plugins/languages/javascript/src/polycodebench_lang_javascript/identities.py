"""Recorded identities of the pinned JavaScript and TypeScript images (Prompt 19).

One runtime implementation serves both identities, but a JavaScript candidate and a TypeScript
candidate are scored separately, so each language records its own pinned image set. A JS/TS tool
identity names three things: the image it ran in, the ``node`` version that produced the result,
and the task's ``package-lock.json`` digest. Lock + Node + image digest together are what make two
runs comparable (Technical Spec 18.2) - the npm lock pins what a dependency resolves to exactly the
way ``Cargo.lock`` does for Rust.

A recipe that does not ship a tool records it as the literal string ``absent`` rather than
omitting the key, so the record states the recipe difference explicitly and ``tool()`` can refuse to
mint an identity for a tool that is not in the image.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal, cast

from polycodebench_core.canonical import parse_json_strict
from polycodebench_core.models import Digest
from polycodebench_plugins_api import ToolIdentity
from pydantic import BaseModel, ConfigDict, Field

PARSER_VERSION = "pcb-js-parsers-1"
GUEST_ROOT = "/opt/pcb"
# The value an image record uses for a tool its recipe does not ship.
ABSENT_TOOL = "absent"
Recipe = Literal["runtime", "evaluator", "performance"]
Language = Literal["javascript", "typescript"]
IMAGE_KINDS: dict[Language, str] = {
    "javascript": "javascript_images",
    "typescript": "typescript_images",
}
IDENTITY_ENV: dict[Language, tuple[str, ...]] = {
    "javascript": ("PCB_JS_IMAGE_IDENTITIES",),
    "typescript": ("PCB_TS_IMAGE_IDENTITIES", "PCB_JS_IMAGE_IDENTITIES"),
}


def image_identity_file(language: str) -> str:
    """Repository-relative location of one language's recorded identities."""
    return f"config/images/{language}-v1.json"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ImageRecord(_Strict):
    recipe: Recipe
    tag: str
    reference: str
    digest: Digest
    node: str
    package_manager: str
    # The pinned dependency closure of the image itself; a task's own package-lock digest rides on
    # ``ToolIdentity.lock_digest`` when a tool's result depends on the task's dependency graph.
    lock_digest: Digest
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
    kind: Literal["javascript_images", "typescript_images"]
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

    def ships(self, name: str, *, recipe: Recipe = "evaluator") -> bool:
        """Whether the recipe's image actually carries a tool (an ``absent`` record does not)."""
        return self.images[recipe].tools.get(name, ABSENT_TOOL) != ABSENT_TOOL

    def tool(
        self,
        name: str,
        *,
        recipe: Recipe = "evaluator",
        parser_version: str = PARSER_VERSION,
        lock_digest: str | None = None,
    ) -> ToolIdentity:
        """Identity of one JavaScript/TypeScript tool.

        ``lock_digest`` is the task's ``package-lock.json`` when the tool's result depends on the
        dependency graph (the offline advisory audit); it is left unset for the build/test and lint
        tools whose result is determined by Node and the image alone.

        A tool the recipe records as ``absent`` has no honest version, so no identity is minted:
        the caller asked for a tool that is not in the image it would claim to have run in.
        """
        record = self.images[recipe]
        version = record.tools.get(name, ABSENT_TOOL)
        if version == ABSENT_TOOL:
            raise ValueError(f"the {recipe} image does not ship {name!r}")
        return ToolIdentity(
            name=name,
            version=version,
            image_digest=record.digest,
            lock_digest=lock_digest or record.recipe_digest,
            rule_bundle_digest=self.rule_bundle_digest,
            advisory_snapshot_digest=None,
            parser_version=parser_version,
        )

    def guest_tool(
        self,
        name: str,
        *,
        recipe: Recipe = "evaluator",
        parser_version: str = PARSER_VERSION,
    ) -> ToolIdentity:
        """Identity of a scanner *baked into* the image rather than shipped as a package.

        The context scanner is a Python guest script, so it has no entry in a recipe's ``tools``
        table and no npm version to record. Claiming one would invent a version; refusing to mint an
        identity would make the analyzer unusable, and this scanner genuinely does run in the
        evaluator image. Its version is the scanner's own ``scanner_version`` (the value it stamps
        into every report), and the recipe's guest-and-rules digest rides on the rule bundle, so a
        change to the scanner's code or its rules still moves this identity.
        """
        record = self.images[recipe]
        return ToolIdentity(
            name=name,
            version=parser_version,
            image_digest=record.digest,
            lock_digest=record.lock_digest,
            rule_bundle_digest=record.guest_and_rules_digest,
            advisory_snapshot_digest=None,
            parser_version=parser_version,
        )


def _find(start: Path, relative: str) -> Path:
    for parent in (start, *start.parents):
        candidate = parent / relative
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(relative)


def _language(language: str) -> Language:
    """Narrow the caller's string to a known identity, or refuse it."""
    if language not in IMAGE_KINDS:
        raise ValueError(f"unknown language {language!r}")
    return cast(Language, language)


def _location(language: str, path: str | None) -> Path:
    """The explicit path, else the language's env override, else the repository record."""
    if path:
        return Path(path)
    for name in IDENTITY_ENV[_language(language)]:
        chosen = os.environ.get(name)
        if chosen:
            return Path(chosen)
    return _find(Path(__file__).resolve().parent, image_identity_file(language))


@lru_cache(maxsize=4)
def load_identities(language: str, path: str | None = None) -> ImageIdentities:
    """Load the recorded identities of one language.

    ``PCB_JS_IMAGE_IDENTITIES`` overrides the JavaScript file and ``PCB_TS_IMAGE_IDENTITIES`` the
    TypeScript one; a deployment that records one image set for both may point the TypeScript
    variable at the JavaScript file, which is why the latter is the fallback. The document's
    ``kind`` must name the requested language: loading the JavaScript record for a TypeScript task
    would give every tool identity the wrong image digest.
    """
    known = _language(language)
    location = _location(language, path)
    document = parse_json_strict(location.read_bytes())
    identities = ImageIdentities.model_validate(json.loads(json.dumps(document)))
    if identities.kind != IMAGE_KINDS[known]:
        raise ValueError(
            f"{location} records {identities.kind!r}, not the requested {IMAGE_KINDS[known]!r}"
        )
    return identities


__all__ = [
    "ABSENT_TOOL",
    "GUEST_ROOT",
    "IMAGE_KINDS",
    "PARSER_VERSION",
    "ImageIdentities",
    "ImageRecord",
    "Language",
    "Recipe",
    "image_identity_file",
    "load_identities",
]
