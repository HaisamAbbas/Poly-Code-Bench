"""The pinned C++ toolchain: standards, compilers, build profiles and instrumentation.

C++ has no lockfile, so the pinned contract *is* this document. A task may only name a compiler, a
language standard, a build profile and a set of sanitizers that appear here; anything else is
refused rather than approximated. The canonical digest of the whole lock travels with every tool
identity, which is what makes two C++ runs comparable.
"""

from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from itertools import combinations
from pathlib import Path
from typing import ClassVar, Literal

from polycodebench_core.canonical import parse_json_strict
from polycodebench_core.models import Digest
from pydantic import BaseModel, ConfigDict, Field

TOOLCHAIN_VERSION = "cpp-toolchain-v1"
LOCK_FILE = "config/languages/cpp-toolchain-v1.json"
Sanitizer = Literal["address", "undefined", "thread"]
Recipe = Literal["runtime", "evaluator", "performance"]


class LockError(ValueError):
    """The requested recipe is not in the pinned toolchain."""


class _Strict(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, populate_by_name=True
    )


class Compiler(_Strict):
    binary: str
    package: str
    linker_driver: str


class Standard(_Strict):
    flag: str


class BuildProfile(_Strict):
    cxxflags: tuple[str, ...]
    ldflags: tuple[str, ...]
    sanitized: bool
    release: bool


class Instrumentation(_Strict):
    build_profile: str
    detector: Literal["asan", "ubsan", "tsan"]
    runtime: Literal["asan", "tsan"]
    environment: dict[str, str]


class AnalyzerInvocation(_Strict):
    binary: str
    flags: tuple[str, ...]
    checks_source: str
    output: Literal["text", "xml"]


class TestHarness(_Strict):
    header: str
    protocol: str
    case_line: str
    start_line: str
    events: tuple[str, ...]
    detail_suffix: str = ""
    outcomes: tuple[str, ...] = ()


class SanitizerReport(_Strict):
    report_schema: str = Field(alias="schema")
    detectors: tuple[str, ...]
    verdicts: tuple[str, ...]


class ToolchainLock(_Strict):
    """The whole pinned document, loaded once."""

    schema_version: Literal[1]
    kind: Literal["cpp_toolchain_lock"]
    toolchain_version: str
    status: str
    effective_for_scoring: bool
    base_image: str
    apt_components: dict[str, tuple[str, ...]]
    compilers: dict[str, Compiler]
    available_compilers: tuple[str, ...]
    standards: dict[str, Standard]
    default_standard: str
    build_profiles: dict[str, BuildProfile]
    instrumentation: dict[str, Instrumentation]
    available_instrumentation: tuple[str, ...]
    incompatible_instrumentation: tuple[tuple[str, str], ...]
    instrumentation_rule: str
    apt_components_note: str = ""
    note: str = ""
    analyzer_invocations: dict[str, AnalyzerInvocation]
    test_harness: TestHarness
    sanitizer_report: SanitizerReport

    @property
    def digest(self) -> Digest:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()

    # --------------------------------------------------------------------- lookups

    def compiler(self, name: str) -> Compiler:
        if name not in self.available_compilers:
            raise LockError(
                f"compiler {name!r} is not pinned; available: {sorted(self.available_compilers)}"
            )
        return self.compilers[name]

    def standard_flag(self, name: str) -> str:
        try:
            return self.standards[name].flag
        except KeyError:
            raise LockError(
                f"language standard {name!r} is not pinned; available: {sorted(self.standards)}"
            ) from None

    def build_profile(self, name: str) -> BuildProfile:
        try:
            return self.build_profiles[name]
        except KeyError:
            raise LockError(
                f"build profile {name!r} is not pinned; available: {sorted(self.build_profiles)}"
            ) from None

    def analyzer(self, name: str) -> AnalyzerInvocation:
        try:
            return self.analyzer_invocations[name]
        except KeyError:
            raise LockError(f"analyzer invocation {name!r} is not pinned") from None

    def sanitizer(self, name: str) -> Instrumentation:
        if name not in self.available_instrumentation:
            raise LockError(
                f"sanitizer {name!r} is not pinned; "
                f"available: {sorted(self.available_instrumentation)}"
            )
        return self.instrumentation[name]

    def detector(self, name: str) -> str:
        return self.sanitizer(name).detector

    # ---------------------------------------------------------------- compatibility

    def check_sanitizers(self, names: tuple[str, ...]) -> None:
        """Refuse an instrumentation combination the pinned toolchain cannot build.

        AddressSanitizer and ThreadSanitizer cannot share a translation unit or a process. Saying
        so here means the refusal happens while the task is being frozen, not after a candidate
        has been scored against a build that quietly dropped one of them.
        """
        unknown = [name for name in names if name not in self.available_instrumentation]
        if unknown:
            raise LockError(
                f"unknown sanitizer(s) {sorted(unknown)}; "
                f"available: {sorted(self.available_instrumentation)}"
            )
        if len(set(names)) != len(names):
            raise LockError(f"sanitizer list repeats an entry: {list(names)}")
        forbidden = {tuple(sorted(pair)) for pair in self.incompatible_instrumentation}
        for left, right in combinations(names, 2):
            if tuple(sorted((left, right))) in forbidden:
                raise LockError(
                    f"sanitizers {left!r} and {right!r} cannot be combined: "
                    f"{self.instrumentation_rule}"
                )

    def profile_for(self, sanitizers: tuple[str, ...]) -> BuildProfile:
        """The one build profile that carries exactly this instrumentation."""
        self.check_sanitizers(sanitizers)
        if not sanitizers:
            return self.build_profile("debug")
        wanted = {self.sanitizer(name).build_profile for name in sanitizers}
        if len(wanted) != 1:
            raise LockError(
                f"sanitizers {list(sanitizers)} need different build profiles: {sorted(wanted)}"
            )
        profile = self.build_profile(wanted.pop())
        if not profile.sanitized:
            raise LockError(f"the build profile for {list(sanitizers)} is not sanitized")
        return profile

    def release_profile(self) -> BuildProfile:
        profile = self.build_profile("release")
        if not profile.release or profile.sanitized:
            raise LockError("the release measurement profile must be unsanitized")
        return profile

    def sanitizer_environment(self, sanitizers: tuple[str, ...]) -> dict[str, str]:
        environment: dict[str, str] = {}
        for name in sanitizers:
            environment.update(self.sanitizer(name).environment)
        return environment


def _find(start: Path) -> Path:
    for parent in (start, *start.parents):
        candidate = parent / LOCK_FILE
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(LOCK_FILE)


@lru_cache(maxsize=4)
def load_lock(root: str | None = None) -> ToolchainLock:
    """Load the pinned lock (``PCB_CPP_TOOLCHAIN_LOCK`` overrides the repository file)."""
    override = os.environ.get("PCB_CPP_TOOLCHAIN_LOCK")
    if override:
        path = Path(override)
    elif root:
        path = Path(root)
    else:
        path = _find(Path(__file__).resolve())
    lock = ToolchainLock.model_validate(parse_json_strict(path.read_bytes()))
    if lock.toolchain_version != TOOLCHAIN_VERSION:
        raise LockError(
            f"toolchain lock version is {lock.toolchain_version!r}, expected {TOOLCHAIN_VERSION!r}"
        )
    for left, right in lock.incompatible_instrumentation:
        if left not in lock.instrumentation or right not in lock.instrumentation:
            raise LockError(f"incompatible pair names an unpinned sanitizer: {left}, {right}")
    return lock


def lock_digest(lock: ToolchainLock | None = None) -> Digest:
    return (lock or load_lock()).digest


__all__ = [
    "AnalyzerInvocation",
    "BuildProfile",
    "Compiler",
    "Instrumentation",
    "LOCK_FILE",
    "LockError",
    "Recipe",
    "Sanitizer",
    "TOOLCHAIN_VERSION",
    "TestHarness",
    "ToolchainLock",
    "load_lock",
    "lock_digest",
]
