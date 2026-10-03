"""Recorded identities of the pinned C images, tools and recipes (Prompt 20, PCB-20-1).

C has one hazard that Rust does not: instrumentation and optimization are the *same compiler*.
``-fsanitize=address`` and ``-O2`` are argument vectors, so nothing about a binary says which it
was. This package therefore makes the distinction structural rather than conventional:

* four recipes are built from two components images, and each image record states its
  ``instrumentation`` and whether it ships analyzers;
* a tool identity carries the recipe's ``flags_digest``, so a result is comparable only against the
  same flag set;
* :func:`ImageIdentities.require_release_recipe` refuses any plan that would measure inside an
  instrumented image - sanitizer and Valgrind timings can never reach an efficiency score.

Sanitizer runtimes ship with the compiler, so "absent" is not a file-level property here the way it
is for a Rust component. The separation is therefore contractual: which recipe a plan may use, and
what that recipe records about its own flags.
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

PARSER_VERSION = "pcb-c-parsers-1"
IMAGE_IDENTITY_FILE = "config/images/c-v1.json"
GUEST_ROOT = "/opt/pcb"

#: A recipe is one of four *material* environments, not a tag on one environment.
Recipe = Literal["runtime", "evaluator", "instrumented", "performance"]
Instrumentation = Literal["none", "address", "undefined", "address_undefined"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ImageRecord(_Strict):
    recipe: Recipe
    tag: str
    reference: str
    digest: Digest
    #: What the image's compiler is; every plan names the exact version it measured against.
    cc: str
    #: The C standard this image's recipes target by default.
    c_standard: str
    #: Whether a result from this image was produced under instrumentation. Never ``none`` for the
    #: instrumented recipe, and always ``none`` for runtime and performance.
    instrumentation: Instrumentation
    #: Optimization level baked into the image, so a timing is fixed by the image not by a manifest.
    optimization: str
    #: Probed for every tool; a tool this image does not ship is recorded as ``absent`` rather than
    #: omitted, so the record states the recipe difference explicitly.
    tools: dict[str, str]
    expected_tools: tuple[str, ...] = ()
    analyzers: tuple[str, ...] = ()
    components: tuple[str, ...]
    guest_and_rules_digest: Digest
    recipe_digest: Digest
    dockerfile_digest: Digest


class ImageIdentities(_Strict):
    schema_version: Literal[1]
    kind: Literal["c_images"]
    base_image: dict[str, str]
    build: dict[str, object]
    rule_bundle_digest: Digest
    guest_digest: Digest
    #: Resolved analyzer selection as the image itself reports it. Recorded so a scorecard can
    #: checks that actually ran; empty when the identities were written before this was probed.
    analyzer_checks: dict[str, object] = Field(default_factory=dict)
    images: dict[str, ImageRecord] = Field(min_length=4)

    def record(self, recipe: Recipe) -> ImageRecord:
        try:
            return self.images[recipe]
        except KeyError as error:  # pragma: no cover - guarded by the model
            raise KeyError(f"no recorded C image for recipe {recipe!r}") from error

    @property
    def runtime(self) -> ImageRecord:
        return self.record("runtime")

    @property
    def evaluator(self) -> ImageRecord:
        return self.record("evaluator")

    @property
    def instrumented(self) -> ImageRecord:
        return self.record("instrumented")

    @property
    def performance(self) -> ImageRecord:
        return self.record("performance")

    def require_release_recipe(self, recipe: Recipe) -> ImageRecord:
        """Refuse a measurement or acceptance run that would use an instrumented image.

        Sanitizer and Valgrind runtimes inflate wall-clock time by an order of magnitude and change
        memory layout. Letting one reach a timing is not a scoring bug that can be caught later; it
        has to be impossible to build the plan at all (Technical Spec 18.2, Architecture 8.5).
        """
        record = self.record(recipe)
        if record.instrumentation != "none":
            raise ValueError(
                f"recipe {recipe!r} is built with {record.instrumentation} instrumentation "
                "and cannot produce a release-performance measurement; use the "
                "'performance' recipe"
            )
        return record

    def tool(
        self,
        name: str,
        *,
        recipe: Recipe = "evaluator",
        parser_version: str = PARSER_VERSION,
        flags_digest: str | None = None,
    ) -> ToolIdentity:
        """Identity of one C tool, bound to the image *and* the flag set it ran with."""
        record = self.record(recipe)
        return ToolIdentity(
            name=name,
            version=record.tools.get(name) or record.cc,
            image_digest=record.digest,
            # For C the "lock" of a tool is its flag set: two runs of clang-tidy differ by their
            # checks even when the binaries are identical.
            lock_digest=flags_digest or record.recipe_digest,
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
    """Load the recorded identities (``PCB_C_IMAGE_IDENTITIES`` overrides the repository file)."""
    chosen = path or os.environ.get("PCB_C_IMAGE_IDENTITIES")
    location = Path(chosen) if chosen else _find(Path(__file__).resolve().parent)
    document = parse_json_strict(location.read_bytes())
    return ImageIdentities.model_validate(json.loads(json.dumps(document)))
