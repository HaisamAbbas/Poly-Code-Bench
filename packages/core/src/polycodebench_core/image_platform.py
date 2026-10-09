"""Host-architecture selection for pinned sandbox image identities.

Every pinned image file is architecture-specific because an image digest names one platform's
layers. The historical files (``config/images/python-v1.json``, the worker allowlists, ...) are the
``linux/amd64`` pins and keep their names. ``linux/arm64`` pins live beside them with an ``-arm64``
suffix before the extension (``python-v1-arm64.json``). Callers pass the amd64 path and receive the
path for the host architecture; on amd64 the path is returned unchanged, so amd64 behaviour is
byte-for-byte what it was.

``PCB_IMAGE_ARCH`` (``amd64`` or ``arm64``) overrides the detected machine. It exists for preparing
or admitting arm64 task packs from an amd64 workstation under emulation; it never widens what a
digest pin accepts.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import Literal

ImageArchitecture = Literal["amd64", "arm64"]
ARCH_ENVIRONMENT_VARIABLE = "PCB_IMAGE_ARCH"
DEFAULT_ARCHITECTURE: ImageArchitecture = "amd64"
_MACHINES: dict[str, ImageArchitecture] = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "x64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
    "armv8": "arm64",
    "armv8l": "arm64",
}
_DOCKER_PLATFORMS: dict[ImageArchitecture, str] = {
    "amd64": "linux/amd64",
    "arm64": "linux/arm64",
}


def normalize_architecture(value: str) -> ImageArchitecture:
    """Map a machine name, Docker architecture or ``linux/<arch>`` platform to a pin family."""
    key = value.strip().lower().removeprefix("linux/")
    if key in _MACHINES:
        return _MACHINES[key]
    raise ValueError(f"unsupported image architecture: {value!r}")


def host_image_architecture(machine: str | None = None) -> ImageArchitecture:
    """Architecture whose pinned images this host runs (``PCB_IMAGE_ARCH`` wins when set)."""
    override = os.environ.get(ARCH_ENVIRONMENT_VARIABLE, "").strip()
    if override:
        return normalize_architecture(override)
    return normalize_architecture(machine if machine is not None else platform.machine())


def docker_platform(architecture: ImageArchitecture) -> str:
    return _DOCKER_PLATFORMS[architecture]


def architecture_variant(path: Path, architecture: ImageArchitecture | None = None) -> Path:
    """Return the pin file for ``architecture`` given the canonical (amd64) pin file path."""
    chosen = architecture or host_image_architecture()
    if chosen == DEFAULT_ARCHITECTURE:
        return path
    return path.with_name(f"{path.stem}-{chosen}{path.suffix}")


def architecture_variant_name(name: str, architecture: ImageArchitecture | None = None) -> str:
    """Same as :func:`architecture_variant` for a relative POSIX path string."""
    return architecture_variant(Path(name), architecture).as_posix()
