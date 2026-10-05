"""Build the prebuilt Go component images (Prompt 22, PCB-22-1) — the only online step.

    .venv/Scripts/python.exe scripts/fetch_go_components.py

This is the Go analogue of the Python wheelhouse download. Go installs third-party analyzers from
module versions, and a module install leaves a large compiled binary behind, so the faithful
equivalent of a hash-verified wheelhouse is an image that already contains every pinned analyzer at
a pinned version. This script builds that image once, **with** network.
``scripts/build_go_images.py`` then builds the three real images with ``--network none`` by copying
the finished binaries out of it, so neither those builds nor any scored run perform an online
installation or fetch.

Re-run this only when a pinned analyzer version changes.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "infra" / "images" / "go"
CONTEXTS = ROOT / ".cache" / "go-components-build"
OUTPUT = ROOT / "config" / "images" / "go-components.json"
# Pinned toolchain. The tag exists for humans; the digest is what the build actually uses.
BASE_DIGEST = "sha256:abe4f87f354c4f6d7ee3fb11b241c6b6c24a32ca50a2ebcc30493a2168e14048"
BASE_IMAGE = f"golang@{BASE_DIGEST}"
COMPONENTS_TAG = "pcb-go-components:v1"
COMPONENTS_BASE_TAG = "pcb-go-components-base:v1"
# <install path>: <module version>. Both are part of the recorded evaluator identity.
ANALYZERS = {
    "staticcheck": "honnef.co/go/tools/cmd/staticcheck@v0.8.1",
    "gosec": "github.com/securego/gosec/v2/cmd/gosec@v2.29.0",
}

DOCKERFILE = """# Prebuilt Go analyzer image (Prompt 22). Built ONCE, with network, by
# scripts/fetch_go_components.py; the three real images are then built with --network none by
# copying these binaries out of this image.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ENV GOTOOLCHAIN=local \\
    GOENV=off \\
    GOBIN=/opt/pcb/tools \\
    GOPATH=/opt/pcb/cache/go-path \\
    GOCACHE=/opt/pcb/cache/go-build
RUN set -eu; \\
    mkdir -p /opt/pcb/tools /opt/pcb/cache/go-path /opt/pcb/cache/go-build; \\
__INSTALLS__
"""

BASE_DOCKERFILE = """# Prebuilt *base* Go component image (Prompt 22): the pinned toolchain exactly as it ships.
# Deliberately no staticcheck and no gosec, so the runtime and performance recipes cannot run an
# analyzer and a candidate cannot inspect one.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
"""


def run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=check, encoding="utf-8")


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def tool_version(tag: str, digest: str, argv: list[str]) -> str:
    """A version reported by a built image itself, so the record comes from the real artifact."""
    if argv == ["staticcheck", "-version"]:
        result = run(
            [
                "docker",
                "run",
                "--rm",
                "--pull=never",
                "--network",
                "none",
                f"{tag}@{digest}",
                "/opt/pcb/tools/staticcheck",
                "-version",
            ],
            check=False,
        )
        if result.returncode != 0:
            return "absent"
        match = re.search(r"\bstaticcheck\s+(\d+\.\d+\.\d+)", result.stdout)
        return match.group(1) if match else "unknown"
    if argv == ["gosec", "--version"]:
        result = run(
            [
                "docker",
                "run",
                "--rm",
                "--pull=never",
                "--network",
                "none",
                f"{tag}@{digest}",
                "go",
                "version",
                "-m",
                "/opt/pcb/tools/gosec",
            ],
            check=False,
        )
        if result.returncode != 0:
            return "absent"
        match = re.search(
            r"(?m)^\s*mod\s+github\.com/securego/gosec/v2\s+(v\S+)",
            result.stdout + result.stderr,
        )
        return match.group(1) if match else "unknown"
    result = run(
        ["docker", "run", "--rm", "--pull=never", "--network", "none", f"{tag}@{digest}", *argv],
        check=False,
    )
    if result.returncode != 0:
        return "absent"
    text = (result.stdout + result.stderr).strip()
    return text.splitlines()[-1].strip() if text else "unknown"


def build(tag: str, context: Path, *, network: str) -> str:
    result = run(
        [
            "docker",
            "build",
            "--network",
            network,
            "--pull=false",
            "--build-arg",
            f"BASE_IMAGE={BASE_IMAGE}",
            "-t",
            tag,
            str(context),
        ],
        check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(f"components image build failed for {tag}")
    return run(["docker", "image", "inspect", tag, "--format", "{{.Id}}"]).stdout.strip()


def main() -> int:
    CONTEXTS.mkdir(parents=True, exist_ok=True)
    specs = list(ANALYZERS.values())
    installs = "\n".join(
        f"    go install {spec}" + (" && \\" if index + 1 < len(specs) else "; \\")
        for index, spec in enumerate(specs)
    )
    dockerfile = CONTEXTS / "Dockerfile"
    dockerfile.write_text(
        DOCKERFILE.replace("__INSTALLS__", installs), encoding="utf-8", newline="\n"
    )
    base_context = CONTEXTS / "base"
    if base_context.exists():
        shutil.rmtree(base_context)
    base_context.mkdir(parents=True)
    (base_context / "Dockerfile").write_text(BASE_DOCKERFILE, encoding="utf-8", newline="\n")
    digest = build(COMPONENTS_TAG, CONTEXTS, network="default")
    base_digest = build(COMPONENTS_BASE_TAG, base_context, network="none")
    document = {
        "schema_version": 1,
        "kind": "go_components_images",
        "note": (
            "Prebuilt component images; the only step that uses a network. build_go_images.py "
            "builds the three recipes from these with --network none. The base image carries no "
            "analyzers, which is what keeps the runtime and performance recipes genuinely "
            "distinct from the evaluator recipe."
        ),
        "base_image": {"reference": BASE_IMAGE, "digest": BASE_DIGEST},
        "evaluator": {
            "tag": COMPONENTS_TAG,
            "digest": digest,
            "analyzers": {name: spec for name, spec in ANALYZERS.items()},
            "tools": {
                "go": tool_version(COMPONENTS_TAG, digest, ["go", "version"]),
                "staticcheck": tool_version(COMPONENTS_TAG, digest, ["staticcheck", "-version"]),
                "gosec": tool_version(COMPONENTS_TAG, digest, ["gosec", "--version"]),
            },
        },
        "base": {
            "tag": COMPONENTS_BASE_TAG,
            "digest": base_digest,
            "tools": {"go": tool_version(COMPONENTS_BASE_TAG, base_digest, ["go", "version"])},
            "dockerfile_digest": sha256_file(base_context / "Dockerfile"),
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}; components image {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
