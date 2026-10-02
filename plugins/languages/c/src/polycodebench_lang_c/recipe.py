"""Frozen C build recipes, flag sets and the task warning policy (Prompt 20, PCB-20-1, PCB-20-3).

Two things are frozen here and nowhere else:

* the **compiler flag sets** per recipe, digested so a tool identity and a scorecard can both prove
  which flags produced a result;
* the **warning policy**, which is the answer to "a blanket ``-Werror`` must not silently invalidate
  an otherwise admitted legacy task" (Architecture 11.2).

The warning policy has three states, and the distinction is the point:

``werror``
    Warnings are errors. Only legal when the frozen baseline compiled clean under exactly this flag
    set, which the task package must record; otherwise admission rejects the task outright.

``warn``
    The full warning set is enabled and warnings are *evidence*, not build failures. Inherited
    baseline warnings are attributed to the baseline; only new ones are the candidate's.

``error``
    Only hard errors are enabled. For a legacy corpus whose diagnostics are noisy this is the honest
    default, and the analyzer lanes still see the whole warning set.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from polycodebench_core.canonical import canonical_digest

from polycodebench_lang_c.identities import GUEST_ROOT, Recipe

#: The C standard every pilot task compiles against unless its package freezes another.
DEFAULT_STANDARD = "c17"
SUPPORTED_STANDARDS = ("c11", "c17", "c23")

#: Warning sets. Kept as data so a task freezes *which* warnings it means rather than a flag count.
WARNING_SETS: dict[str, tuple[str, ...]] = {
    "none": (),
    "minimal": ("-Wall",),
    "standard": ("-Wall", "-Wextra"),
    "strict": (
        "-Wall",
        "-Wextra",
        "-Wpedantic",
        "-Wshadow",
        "-Wconversion",
        "-Wsign-conversion",
        "-Wcast-qual",
        "-Wmissing-prototypes",
        "-Wstrict-prototypes",
        "-Wold-style-definition",
        "-Wundef",
        "-Wwrite-strings",
        "-Wformat=2",
    ),
}
WARNING_SET_NAMES = tuple(WARNING_SETS)
#: Warnings that are advisory by construction and never promoted to errors.
NON_BLOCKING_WARNINGS = frozenset({"-Wunused-parameter"})

#: Sanitizer flags, by the analyzer that owns them. Address and undefined are separate lanes so a
#: memory error is never also reported as undefined behaviour and double-penalised.
#:
#: The address lane builds a non-PIE executable. Clang 14's ASan runtime cannot place its shadow
#: memory when the kernel randomizes a PIE load address with high entropy (``vm.mmap_rnd_bits=32``,
#: the WSL2 6.x default): about one run in four dies of SIGSEGV before ``main``, with no report. That
#: death says nothing about the candidate. Disabling ASLR instead would need ``personality()``, which
#: the sandbox's seccomp profile denies. A fixed load address changes where the code lives, not what
#: ASan checks (D-20-01).
SANITIZER_FLAGS: dict[str, tuple[str, ...]] = {
    "address": ("-fsanitize=address", "-fno-omit-frame-pointer", "-fno-pie"),
    "undefined": ("-fsanitize=undefined", "-fno-sanitize-recover=undefined"),
}
#: Link-only flags. ``-no-pie`` is a linker-driver flag; passed to a ``-c`` compile it would be an
#: "argument unused" warning recorded against the candidate.
SANITIZER_LINK_FLAGS: dict[str, tuple[str, ...]] = {
    "address": ("-no-pie",),
    "undefined": (),
}
SANITIZER_NAMES = tuple(SANITIZER_FLAGS)

#: Optimization per recipe, baked by the image so a timing is not whatever the manifest said.
#:
#: ``-gdwarf-4`` is in every recipe, for two reasons. Debug info adds DWARF and no code, so it cannot
#: affect a measurement; and Valgrind 3.19 cannot read the DWARF 5 that clang 14 emits by default - it
#: aborts with "unhandled dwarf2 abbrev form code" and produces *no* memcheck output at all. Freezing
#: the debug format benchmark-wide means one decoder can read every binary the evaluator builds, which
#: is what lets a memory-safety finding be charged to a source line instead of arriving unlocated.
RECIPE_OPTIMIZATION: dict[Recipe, tuple[str, ...]] = {
    "runtime": ("-O1", "-gdwarf-4"),
    "evaluator": ("-O0", "-gdwarf-4"),
    "instrumented": ("-O1", "-gdwarf-4"),
    # Measurements run at -O2 with no instrumentation and no debug helpers.
    "performance": ("-O2", "-gdwarf-4", "-DNDEBUG"),
}
#: Optimization levels an instrumented lane may use. An instrumented binary is a correctness
#: instrument, never a speed instrument.
RECIPE_INSTRUMENTATION: dict[Recipe, str] = {
    "runtime": "none",
    "evaluator": "none",
    "instrumented": "address_undefined",
    "performance": "none",
}

#: Every plan argument is a single token drawn from this alphabet. The requirement is not
#: "looks like a flag" but "cannot be reinterpreted": no whitespace, no shell metacharacter, no quote.
#: A path may appear in an argument (``-Iwork/include``), so this is a character class rather than a
#: flag grammar - what it rules out is a shell string, which is the thing that would actually be
#: dangerous.
_SAFE_TOKEN = re.compile(r"[A-Za-z0-9@%+=:,./_-]{1,200}")


@dataclass(frozen=True)
class BuildRecipe:
    """One resolved, digest-addressed flag set."""

    recipe: Recipe
    standard: str
    warning_set: str
    warnings_as_errors: bool
    sanitizer: str | None
    include_dir: str = "."
    defines: tuple[str, ...] = ()

    @property
    def optimization(self) -> tuple[str, ...]:
        return RECIPE_OPTIMIZATION[self.recipe]

    @property
    def warnings(self) -> tuple[str, ...]:
        return WARNING_SETS[self.warning_set]

    @property
    def sanitizer_flags(self) -> tuple[str, ...]:
        if self.sanitizer is None:
            return ()
        return SANITIZER_FLAGS[self.sanitizer]

    def compile_flags(self) -> tuple[str, ...]:
        """Flags for compiling one translation unit, in a fixed order so the digest is stable."""
        return (
            f"-std={self.standard}",
            *self.optimization,
            *self.warnings,
            *(("-Werror",) if self.warnings_as_errors else ()),
            *self.sanitizer_flags,
            *(f"-I{self.include_dir}",),
            *(f"-D{define}" for define in self.defines),
            "-fno-color-diagnostics",
            "-fno-caret-diagnostics",
        )

    def link_flags(self) -> tuple[str, ...]:
        """Flags for linking.

        Sanitizer flags must be repeated at link time or the runtime is never pulled in; that is a
        linker error for the built binary rather than a silent no-op, so it is stated here explicitly.
        """
        if self.sanitizer is None:
            return ("-Wl,-z,now",)
        return (*self.sanitizer_flags, *SANITIZER_LINK_FLAGS[self.sanitizer])

    def flags_digest(self) -> str:
        return str(
            canonical_digest(
                {
                    "recipe": self.recipe,
                    "standard": self.standard,
                    "optimization": list(self.optimization),
                    "warnings": list(self.warnings),
                    "warnings_as_errors": self.warnings_as_errors,
                    "sanitizer": self.sanitizer,
                    "include_dir": self.include_dir,
                    "defines": list(self.defines),
                }
            )
        )

    def validate(self) -> BuildRecipe:
        if self.standard not in SUPPORTED_STANDARDS:
            raise ValueError(
                f"unsupported C standard {self.standard!r}; this pilot supports "
                f"{list(SUPPORTED_STANDARDS)}"
            )
        if self.warning_set not in WARNING_SETS:
            raise ValueError(f"unknown warning set {self.warning_set!r}")
        if self.sanitizer is not None and self.sanitizer not in SANITIZER_FLAGS:
            raise ValueError(f"unknown sanitizer lane {self.sanitizer!r}")
        if self.warnings_as_errors and self.recipe == "performance":
            raise ValueError(
                "a measurement recipe must never promote warnings to errors; it changes the "
                "generated code that is being measured"
            )
        for flag in self.compile_flags():
            if _SAFE_TOKEN.fullmatch(flag) is None:
                raise ValueError(
                    f"argument {flag!r} is not a single safe argv token; a plan never builds a "
                    "shell string"
                )
        for flag in self.link_flags():
            if _SAFE_TOKEN.fullmatch(flag) is None:
                raise ValueError(f"linker argument {flag!r} is not a single safe argv token")
        return self


def resolve_recipe(
    *,
    recipe: Recipe,
    standard: str = DEFAULT_STANDARD,
    warning_set: str = "standard",
    warnings_as_errors: bool = False,
    sanitizer: str | None = None,
    include_dir: str = ".",
    defines: tuple[str, ...] = (),
) -> BuildRecipe:
    """Resolve a recipe, refusing the combinations that would make evidence uninterpretable."""
    resolved = BuildRecipe(
        recipe=recipe,
        standard=standard,
        warning_set=warning_set,
        warnings_as_errors=warnings_as_errors,
        sanitizer=sanitizer,
        include_dir=include_dir,
        defines=tuple(sorted(defines)),
    ).validate()
    instrumented = RECIPE_INSTRUMENTATION[recipe]
    if resolved.sanitizer is not None and instrumented == "none":
        raise ValueError(
            f"recipe {recipe!r} has no instrumentation runtime; a sanitizer lane must run in the "
            "'instrumented' image"
        )
    if resolved.sanitizer is None and instrumented != "none":
        raise ValueError(
            f"recipe {recipe!r} is built with {instrumented}; running it without the sanitizer "
            "flags would report an instrumented binary as a release build"
        )
    return resolved


def recipe_document() -> dict[str, object]:
    """The frozen recipe table, digested into every tool identity.

    The derived clang-tidy config is part of this table, not an incidental build product: the check
    selection *is* the analyzer's definition, so a change to it has to change every tool identity and
    every sealed task.
    """
    return {
        "schema_version": 1,
        "kind": "c_build_recipes",
        "default_standard": DEFAULT_STANDARD,
        "supported_standards": list(SUPPORTED_STANDARDS),
        "warning_sets": {name: list(flags) for name, flags in WARNING_SETS.items()},
        "sanitizer_flags": {name: list(flags) for name, flags in SANITIZER_FLAGS.items()},
        "sanitizer_link_flags": {
            name: list(flags) for name, flags in SANITIZER_LINK_FLAGS.items()
        },
        "non_blocking_warnings": sorted(NON_BLOCKING_WARNINGS),
        "clang_tidy_config": clang_tidy_config_text(),
        "cppcheck_arguments": list(cppcheck_arguments()),
        "recipes": {
            recipe: {
                "optimization": list(RECIPE_OPTIMIZATION[recipe]),
                "instrumentation": RECIPE_INSTRUMENTATION[recipe],
            }
            for recipe in RECIPE_OPTIMIZATION
        },
    }


#: clang-tidy check selection, read from the digest-pinned rules bundle.
CLANG_TIDY_GROUPS = ("bugprone", "cert", "clang-analyzer", "performance", "portability")
CPPCHECK_CATEGORIES = ("warning", "style", "performance", "portability")
#: The rules bundle is named without a leading dot so it is packed with the wheel, and every tool is
#: pointed at it explicitly rather than relying on a tool finding it in a working directory.
RULES = f"{GUEST_ROOT}/rules"
#: The reviewed source of truth is ``rules/clang-tidy.yaml``, which groups checks by family so a
#: reviewer can see what each family contributes. clang-tidy itself only accepts a flat ``Checks:``
#: list, so the image build *derives* its config file from that YAML. Deriving rather than keeping two
#: hand-maintained files is the point: a reviewer edits one file and cannot leave the tool running a
#: different check set from the one the profile maps.
CLANG_TIDY_CONFIG = f"{RULES}/.clang-tidy"
CPPCHECK_SUPPRESSIONS = f"{RULES}/cppcheck-suppressions.txt"
HARNESS_HEADER = f"{RULES}/pcb_ctest.h"
HARNESS_SOURCE = f"{RULES}/pcb_ctest.c"


def clang_tidy_check_ids() -> tuple[str, ...]:
    """Every selected check id, in the deterministic order the derived config lists them.

    A name is qualified by its family the way clang-tidy spells it (``clang-analyzer-core.DivideZero``,
    ``cert-err34-c``). A name that already carries its family is left alone, because a few checks
    legitimately belong to a family other than the group that files them.
    """
    groups = clang_tidy_checks()
    qualified: set[str] = set()
    for group in CLANG_TIDY_GROUPS:
        for name in groups.get(group, ()):
            qualified.add(name if name.startswith(f"{group}-") else f"{group}-{name}")
    return tuple(sorted(qualified))


def clang_tidy_config_text() -> str:
    """The ``.clang-tidy`` file the image ships, rendered from the reviewed grouped YAML.

    ``-*`` first, then bare names: in clang-tidy's grammar a bare name *enables* a check and a
    ``-``-prefixed one disables it, so listing the selection after the reset is what makes the frozen
    rules bundle the whole truth. Any check the YAML does not name stays off even when the analyzer's
    default set would enable it.
    """
    checks = ", ".join(["-*", *clang_tidy_check_ids()])
    return (
        "# Generated from rules/clang-tidy.yaml by scripts/build_c_images.py. Do not edit.\n"
        "---\n"
        f"Checks: '{checks}'\n"
        # clang-tidy never promotes a finding to an error: findings are counted, never fatal, and a
        # scan that cannot compile is reported as missing rather than as a clean result.
        "WarningsAsErrors: ''\n"
        # The frozen header and harness are not candidate code, so their own style is out of scope.
        f"HeaderFilterRegex: '^{GUEST_ROOT}/rules/'\n"
        "FormatStyle: none\n"
    )


def analyzer_check_ids(*, prefix: str) -> tuple[str, ...]:
    """Stable check-id stems so capabilities can be declared before a scan has run."""
    return tuple(
        sorted(
            {
                f"{prefix}.{group}.{name}"
                for group in CLANG_TIDY_GROUPS
                for name in clang_tidy_checks().get(group, ())
            }
        )
    )


@lru_cache(maxsize=1)
def clang_tidy_checks() -> dict[str, tuple[str, ...]]:
    """Checks grouped by family, as frozen in ``rules/clang-tidy.yaml``."""
    import yaml  # type: ignore[import-untyped]

    path = Path(__file__).resolve().parent / "rules" / "clang-tidy.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    groups: dict[str, tuple[str, ...]] = {
        group: tuple(str(item) for item in document.get(group, ()))
        for group in CLANG_TIDY_GROUPS
    }
    if not any(groups.values()):
        raise ValueError(
            "rules/clang-tidy.yaml selects no checks; a scoring lane that selected nothing would "
            "read as clean"
        )
    return groups


def clang_tidy_arguments() -> tuple[str, ...]:
    """The frozen clang-tidy argument vector (typed array, explicit config file)."""
    return (
        "--quiet",
        f"--config-file={CLANG_TIDY_CONFIG}",
        "--format-style=none",
        "--use-color=false",
    )


def cppcheck_arguments() -> tuple[str, ...]:
    """The frozen cppcheck argument vector (no shell string, no ``--enable=all``).

    ``--quiet`` is deliberately *not* used. Quiet suppresses cppcheck's ``Checking <file>...`` progress
    lines, and those lines are the only evidence that cppcheck actually read a file: on input it cannot
    parse, cppcheck 2.10 prints nothing at all and exits 0, which would make a scan of uncompilable code
    look identical to a clean one. The parser reads the progress lines and reports a scan that never
    covered a file as missing (Technical Spec 12.3).
    """
    return (
        "--inline-suppr",
        f"--enable={','.join(CPPCHECK_CATEGORIES)}",
        f"--std={DEFAULT_STANDARD}",
        f"--suppressions-list={CPPCHECK_SUPPRESSIONS}",
        "--template={file}:{line}:{column}: {severity}: {message} [{id}]",
        "-D__STDC_VERSION__=201710L",
    )


def warning_policy_allows_error_blocking(warning_set: str, warning: str) -> bool:
    """Whether a single warning may fail a build under ``-Werror``.

    A warning the policy marked advisory stays visible in the evidence and never silently invalidates
    an admitted task.
    """
    return f"-W{warning}" not in NON_BLOCKING_WARNINGS and warning_set != "none"


WarningPolicyMode = Literal["werror", "warn", "error"]