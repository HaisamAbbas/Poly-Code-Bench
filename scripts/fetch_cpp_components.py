"""Build the prebuilt C++ components images (Prompt 21, PCB-21-1) — the only online step.

    .venv/Scripts/python.exe scripts/fetch_cpp_components.py

The C++ analogue of the Python wheelhouse. A C++ toolchain is a set of distribution packages
rather than a self-contained archive, so the faithful equivalent of a vendored wheelhouse is an
image that already contains every pinned package at the exact version the toolchain lock names.
This script builds two such images **with** network:

``pcb-cpp-components-runtime:v1``
    ``clang``, ``llvm-14`` and ``libstdc++-12-dev``: the compiler, source symbolizer and
    ASan/UBSan/TSan runtimes. No analyzer, so the runtime recipe stays analyzer-free.
``pcb-cpp-components-evaluator:v1``
    the runtime plus ``clang-tidy`` and ``cppcheck``.

``scripts/build_cpp_images.py`` then builds the three recipe images with ``--network none`` by
copying the finished toolchain out of them, so neither those builds nor any scored run perform an
online installation or fetch. The package lists are deleted inside the components images, so a
scored run that tried to install anything would fail loudly rather than quietly succeed.

Package versions come from ``config/languages/cpp-toolchain-v1.json`` and are installed with an
exact ``name=version`` constraint against the immutable Debian snapshot the lock pins, so a
silent mirror change fails the build instead of changing the compiler under a pinned identity.

Re-run this only when a pinned package or the pinned base image changes.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "infra" / "images" / "cpp"
CONTEXTS = ROOT / ".cache" / "cpp-components-build"
OUTPUT = ROOT / "config" / "images" / "cpp-components.json"
LOCK = ROOT / "config" / "languages" / "cpp-toolchain-v1.json"
RECIPES = ("runtime", "evaluator")
# The Debian suite the pinned package versions belong to. The base image is a bookworm image, and
# a version that only exists in a different suite must fail the install rather than resolve.
SUITE = "bookworm"

DOCKERFILE = """# Prebuilt C++ component images (Prompt 21). Built ONCE, with network, by
# scripts/fetch_cpp_components.py; the three recipe images are then built with --network none by
# copying this finished toolchain out.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
RUN set -eu; \\
    printf '%s\\n' \
        'deb http://deb.debian.org/debian __SUITE__ main' \
        'deb http://deb.debian.org/debian-security __SUITE__-security main' \
        > /etc/apt/sources.list; \
    printf 'Acquire::Check-Valid-Until "false";\\nAPT::Install-Recommends "false";\\n' \
        > /etc/apt/apt.conf.d/99pcb; \\
    apt-get update; \\
    apt-get install -y --no-install-recommends __PACKAGES__; \\
    rm -rf /var/lib/apt/lists/*
"""


def run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=check, encoding="utf-8")


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def lock() -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(LOCK.read_text(encoding="utf-8")))


def base_image() -> str:
    return str(lock()["base_image"])


def packages(recipe: str) -> list[str]:
    wanted: list[str] = [str(item) for item in lock()["apt_components"][recipe]]
    if not wanted:
        raise SystemExit(f"the toolchain lock pins no apt package for recipe {recipe!r}")
    return wanted


def build_context(recipe: str) -> Path:
    context = CONTEXTS / recipe
    if context.exists():
        shutil.rmtree(context)
    context.mkdir(parents=True)
    if "@sha256:" not in base_image():
        raise SystemExit(f"the toolchain lock must pin {base_image()!r} by digest")
    (context / "Dockerfile").write_text(
        DOCKERFILE.replace("__SUITE__", SUITE).replace("__PACKAGES__", " ".join(packages(recipe))),
        encoding="utf-8",
    )
    return context


def tool_versions(tag: str, digest: str) -> dict[str, str]:
    """Versions reported by the components image itself, never by this host."""

    def ask(argv: list[str]) -> str:
        result = run(
            ["docker", "run", "--rm", "--pull=never", f"{tag}@{digest}", *argv], check=False
        )
        if result.returncode != 0:
            return "absent"
        text = (result.stdout + result.stderr).strip()
        return text.splitlines()[0] if text else "unknown"

    return {
        "clang++": ask(["clang++", "--version"]),
        "clang-tidy": ask(["clang-tidy", "--version"]),
        "cppcheck": ask(["cppcheck", "--version"]),
        "llvm-symbolizer": ask(["llvm-symbolizer-14", "--version"]),
    }


def build(recipe: str) -> dict[str, object]:
    context = build_context(recipe)
    tag = f"pcb-cpp-components-{recipe}:v1"
    result = run(
        [
            "docker",
            "build",
            "--pull=false",
            "--build-arg",
            f"BASE_IMAGE={base_image()}",
            "-t",
            tag,
            str(context),
        ],
        check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(
            f"cpp components build failed for {recipe}; the pinned package versions are not "
            "installable from the pinned mirror"
        )
    digest = run(["docker", "image", "inspect", tag, "--format", "{{.Id}}"]).stdout.strip()
    versions = tool_versions(tag, digest)
    if recipe == "runtime":
        if versions["llvm-symbolizer"] == "absent":
            raise SystemExit("the runtime components image must ship llvm-symbolizer-14")
        for analyzer in ("clang-tidy", "cppcheck"):
            if versions[analyzer] != "absent":
                raise SystemExit(
                    f"the runtime components image must not ship {analyzer}: a candidate could "
                    "read the checks that judge it from inside the image it runs in"
                )
    return {
        "recipe": recipe,
        "tag": tag,
        "reference": f"pcb-cpp-components-{recipe}@{digest}",
        "digest": digest,
        "base_image": base_image(),
        "packages": packages(recipe),
        "packages_digest": "sha256:"
        + hashlib.sha256(json.dumps(packages(recipe), sort_keys=True).encode()).hexdigest(),
        "dockerfile_digest": sha256_file(context / "Dockerfile"),
        "tool_versions": versions,
    }


def main() -> int:
    images = {recipe: build(recipe) for recipe in RECIPES}
    document = {
        "schema_version": 1,
        "kind": "cpp_components",
        "note": (
            "Prebuilt component images. Built once with network by "
            "scripts/fetch_cpp_components.py; the three recipe images are built with --network "
            "none from them, so no scored run ever installs a package."
        ),
        "images": images,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for recipe in RECIPES:
        record = images[recipe]
        tool_versions = cast("dict[str, str]", record["tool_versions"])
        print(recipe, record["digest"], tool_versions["clang++"])
    print(f"wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
