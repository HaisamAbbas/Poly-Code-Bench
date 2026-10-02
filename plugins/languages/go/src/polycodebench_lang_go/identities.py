"""Recorded identities of the pinned Go images, tools and recipes (Prompt 22, PCB-22-1).

A Go tool identity names the image it ran in, the toolchain that produced the result and - for any
tool whose result depends on the module graph - the digest of the task's ``go.mod``/``go.sum`` pair.
Those three together are what make two runs comparable (Technical Spec 18.2). The race-enabled
tool is recorded separately from the ordinary test tool: a ``-race`` build is an instrumented build,
and an instrumented result must never be presented as if it came from the release measurement lane.
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

PARSER_VERSION = "pcb-go-parsers-1"
IMAGE_IDENTITY_FILE = "config/images/go-v1.json"
GUEST_ROOT = "/opt/pcb"
Recipe = Literal["runtime", "evaluator", "performance"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ImageRecord(_Strict):
    recipe: Recipe
    tag: str
    reference: str
    digest: Digest
    go: str
    # Probed for every tool in every recipe; a tool this image does not ship is recorded as
    # ``absent`` rather than omitted, so the record states the recipe difference explicitly.
    tools: dict[str, str]
    #: The instrumentation this recipe's plans are built with. The race detector ships inside the
    #: pinned Go base, so this is a *contract* about which recipe a plan may target rather than a
    #: claim about a missing file: a ``-race`` build is the same compiler with shadow memory, and
    #: its timings are an artefact of the detector.
    instrumentation: str
    #: Whether a plan targeting this recipe may carry an instrumented build at all.
    accepts_instrumented_plans: bool
    expected_tools: tuple[str, ...] = ()
    components: tuple[str, ...]
    guest_and_rules_digest: Digest
    recipe_digest: Digest
    dockerfile_digest: Digest


class ImageIdentities(_Strict):
    schema_version: Literal[1]
    kind: Literal["go_images"]
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

    def require_release_recipe(self, recipe: Recipe) -> ImageRecord:
        """Refuse a measurement that would be taken inside an instrumented build (PCB-22-2).

        A ``-race`` build recompiles the same algorithm with shadow memory and atomic
        instrumentation. It is several times slower and its memory figure is an artefact of the
        detector, so a result from it must never reach an efficiency score. The check lives here,
        where the plan is built, because a later stage that "remembers" to filter is a stage that
        can be forgotten.
        """
        record = self.record(recipe)
        if record.instrumentation != "none":
            raise ValueError(
                f"recipe {recipe!r} is built with {record.instrumentation} instrumentation and "
                "cannot produce a release-performance measurement; use the 'performance' recipe"
            )
        return record

    def require_instrumented(self, recipe: Recipe) -> ImageRecord:
        """Refuse an instrumented plan aimed at a recipe that does not host one.

        The mirror image of :meth:`require_release_recipe`: a race run must actually be able to
        observe a race, or ``clean`` would be reported for a detector that never ran.
        """
        record = self.record(recipe)
        if not record.accepts_instrumented_plans:
            raise ValueError(
                f"recipe {recipe!r} does not host instrumented plans; a -race result from it "
                "would report 'clean' for a detector that cannot run"
            )
        return record

    def tool(
        self,
        name: str,
        *,
        recipe: Recipe = "evaluator",
        parser_version: str = PARSER_VERSION,
        lock_digest: str | None = None,
    ) -> ToolIdentity:
        """Identity of one Go tool.

        ``lock_digest`` is the task's module digest when the tool's result depends on the module
        graph (staticcheck, the module audit, vet); it is left unset for the build/test tools whose
        result is determined by the toolchain and image alone.
        """
        record = self.images[recipe]
        version = record.tools.get(name) or record.go
        if name == "go-test-race":
            # The instrumented lane is a different tool: same compiler, different build. Naming it
            # separately is what stops a race result being read as a release result.
            version = f"{record.go}-race"
        return ToolIdentity(
            name=name,
            version=version,
            image_digest=record.digest,
            lock_digest=lock_digest or record.recipe_digest,
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
    """Load the recorded identities (``PCB_GO_IMAGE_IDENTITIES`` overrides the repository file)."""
    chosen = path or os.environ.get("PCB_GO_IMAGE_IDENTITIES")
    location = Path(chosen) if chosen else _find(Path(__file__).resolve().parent)
    document = parse_json_strict(location.read_bytes())
    return ImageIdentities.model_validate(json.loads(json.dumps(document)))
