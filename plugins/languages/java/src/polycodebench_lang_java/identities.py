"""Recorded identities of the pinned Java images, tools and recipe (Prompt 23, PCB-23-1).

A Java tool identity names four things, and two of them are the whole reason this module exists:

* the **image** the tool ran in, which fixes the JDK, the Maven distribution and the offline
  repository seeded at build time;
* the **Maven plugin version**, because ``mvn spotbugs:check`` resolves the plugin by version and
  two versions of the same plugin produce different findings;
* the **dependency lock digest**, because a Java toolchain pins the JDK but pins *nothing* about
  what a task's POM resolves to. Two runs of the same goal against the same source can compile
  different bytecode if the dependency graph moved, so the frozen resolution is part of the
  identity (``ToolIdentity.lock_digest``);
* the **rule bundle digest**, because Checkstyle/PMD/SpotBugs each take a ruleset file from this
  repository rather than from the image.

The JIT measurement policy is *not* here because it is not runtime state: the frozen cold and
steady-state flag sets live in the image (``build.jvm_measurement`` in the identity file) and are
selected by the task's declared mode. Neither the mode nor the flag set may be derived from a
candidate, from a measurement, or from anything observed at run time (PCB-23-1 DoD).
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

PARSER_VERSION = "pcb-java-parsers-1"
IMAGE_IDENTITY_FILE = "config/images/java-v1.json"
GUEST_ROOT = "/opt/pcb"
Recipe = Literal["runtime", "evaluator", "performance"]
#: Maven plugins whose version changes their output. These are the tools a Java task's evidence
#: actually depends on; ``mvn`` itself is recorded as ``maven`` and the JDK as ``java``.
MAVEN_PLUGIN_TOOLS = ("spotbugs", "pmd", "checkstyle", "dependency", "surefire", "compiler")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class JavaBuild(_Strict):
    """The pinned, build-time facts a reader must be able to audit."""

    network: Literal["none"]
    offline_install: Literal[True]
    components_installed_at_build_time: Literal[True] = True
    scored_runs_offline: Literal[True]
    java: str
    maven: str
    offline_repository: str
    offline_repository_digest: Digest
    guest_interpreter: str
    #: The two frozen measurement modes. Keys are the declared modes; values are the exact JVM
    #: flags, chosen before any candidate exists and never varied by what a run observed.
    jvm_measurement: dict[str, tuple[str, ...]]
    note: str


class ImageRecord(_Strict):
    recipe: Recipe
    tag: str
    reference: str
    digest: Digest
    java: str
    maven: str
    # Probed for every tool in every recipe; a tool this image does not ship is recorded as
    # ``absent`` rather than omitted, so the record states the recipe difference explicitly.
    tools: dict[str, str]
    #: Per-recipe copy of a baked measurement policy. Only the performance recipe is populated;
    #: the top-level JavaBuild block must agree with that artifact's inspected values.
    jvm_measurement: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    expected_tools: tuple[str, ...] = ()
    components: tuple[str, ...]
    guest_and_rules_digest: Digest
    recipe_digest: Digest
    dockerfile_digest: Digest


class ImageIdentities(_Strict):
    schema_version: Literal[1]
    kind: Literal["java_images"]
    base_image: dict[str, str]
    build: JavaBuild
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

    def jvm_flags(self, mode: str) -> tuple[str, ...]:
        """The frozen flag set for a declared measurement mode.

        The mode is a task-declared, admission-frozen field. An unknown mode is an error rather
        than a default: silently measuring in the other mode would compare two candidates under
        different JIT policies.
        """
        flags = self.build.jvm_measurement.get(mode)
        if flags is None:
            raise ValueError(
                f"unknown Java measurement mode {mode!r}; "
                f"declared modes are {sorted(self.build.jvm_measurement)}"
            )
        return flags

    def tool(
        self,
        name: str,
        *,
        recipe: Recipe = "evaluator",
        parser_version: str = PARSER_VERSION,
        lock_digest: str | None = None,
        advisory_snapshot_digest: str | None = None,
    ) -> ToolIdentity:
        """Identity of one Java tool.

        ``lock_digest`` is the task's frozen dependency resolution when the tool's result depends
        on what the POM resolves to; it is left unset for the build/test tools whose result is
        determined by the JDK, the image and the POM alone.
        """
        record = self.images[recipe]
        version = record.tools.get(name) or record.maven
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
                if name == "dependency"
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
    """Load recorded identities; ``PCB_JAVA_IMAGE_IDENTITIES`` overrides the repository file."""
    chosen = path or os.environ.get("PCB_JAVA_IMAGE_IDENTITIES")
    location = Path(chosen) if chosen else _find(Path(__file__).resolve().parent)
    document = parse_json_strict(location.read_bytes())
    return ImageIdentities.model_validate(json.loads(json.dumps(document)))
