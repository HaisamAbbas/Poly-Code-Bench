"""Build the prebuilt Rust components image (Prompt 11, PCB-11-1) — the only online step.

    .venv/Scripts/python.exe scripts/fetch_rust_components.py

This is the Rust analogue of the Python wheelhouse download. ``rustup`` unpacks a component and then
deletes its payload, so there is nothing file-level to vendor; the faithful equivalent of the
wheelhouse is therefore an image that already contains every pinned component. This script builds
that image once, **with** network. ``scripts/build_rust_images.py`` then builds the three real
images with ``--network none`` by copying the finished toolchain out of it, so neither those builds
nor any scored run perform an online installation or fetch.

Re-run this only when a pinned component or the pinned Miri toolchain changes.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "infra" / "images" / "rust"
CONTEXTS = ROOT / ".cache" / "rust-components-build"
OUTPUT = ROOT / "config" / "images" / "rust-components.json"
BASE_DIGEST = "sha256:540c902e99c384163b688bbd8b5b8520e94e7731b27f7bd0eaa56ae1960627ab"
BASE_IMAGE = f"rust@{BASE_DIGEST}"
COMPONENTS_TAG = "pcb-rust-components:v1"
STABLE_COMPONENTS = ("clippy", "rustfmt")
MIRI_TOOLCHAIN = "nightly-2026-09-30"
MIRI_COMPONENTS = ("miri", "rust-src")

DOCKERFILE = """# Prebuilt Rust component image (Prompt 11). Built ONCE, with network, by
# scripts/fetch_rust_components.py; the three real images are then built with --network none by
# copying /usr/local/rustup and the vendored Miri sysroot crates out of this image.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
RUN set -eu; \\
    rustup component add __STABLE__; \\
    rustup toolchain install __MIRI_TOOLCHAIN__ --profile minimal --component __MIRI_COMPONENTS__
# Miri interprets std from source and builds a sysroot, which resolves real crates from crates.io.
# A scored run is offline, so those crates are vendored here (the Python wheelhouse analogue) and
# the evaluator points cargo at them. An incomplete vendor directory makes the sysroot build fail
# loudly rather than silently reaching for a network during a scored run.
COPY sysroot-vendor /opt/pcb/vendor
"""


def run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=check, encoding="utf-8")


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def component_versions(digest: str) -> dict[str, str]:
    def ask(*argv: str) -> str:
        result = run(
            [
                "docker",
                "run",
                "--rm",
                "--pull=never",
                "--network",
                "none",
                f"{COMPONENTS_TAG}@{digest}",
                *argv,
            ],
            check=False,
        )
        text = (result.stdout + result.stderr).strip()
        return text.splitlines()[-1] if text else "unknown"

    return {
        "rustc": ask("rustc", "--version"),
        "cargo": ask("cargo", "--version"),
        "clippy": ask("cargo", "clippy", "--version"),
        "rustfmt": ask("cargo", "fmt", "--version"),
        "miri": ask("cargo", "+" + MIRI_TOOLCHAIN, "miri", "--version"),
    }


def vendor_sysroot_crates(context: Path) -> None:
    """Vendor the crates Miri's sysroot build resolves from crates.io.

    Miri interprets ``std`` from source and builds a sysroot, which pulls a handful of real crates.
    A scored run is offline, so they are downloaded once here and shipped in the image. This is the
    Rust counterpart of the Python wheelhouse and the only other network use in the pipeline.

    The vendor set is whatever ``cargo miri setup`` actually resolves, so it cannot drift from the
    pinned interpreter. It runs against the already-built components image, which is the only image
    that carries the pinned nightly.
    """
    target = context / "sysroot-vendor"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    # The probe manifest Miri needs in order to build its sysroot.
    manifest = (
        'printf \'[package]\\nname = "probe"\\nversion = "0.1.0"\\nedition = "2021"\\n\''
        " > Cargo.toml\n"
    )
    script = context / "vendor.sh"
    # Written with explicit \n: a CRLF here would make the container's shell fail on a stray \r.
    script.write_text(
        "set -eu\n"
        "mkdir -p /probe/src && cd /probe\n"
        + manifest
        + "printf 'pub fn f() {}\\n' > src/lib.rs\n"
        + f"cargo +{MIRI_TOOLCHAIN} miri setup >/dev/null\n"
        # Miri interprets std from the toolchain's own rust-src checkout; that workspace manifest
        # declares the crates the sysroot build resolves. Vendor from there, so the vendor set
        # cannot drift from the pinned nightly.
        + "cd /usr/local/rustup/toolchains/"
        + MIRI_TOOLCHAIN
        + "-x86_64-unknown-linux-gnu/lib/rustlib/src/rust/library\n"
        # The std workspace uses nightly-only cargo features, so vendor with the nightly cargo.
        + "cargo +"
        + MIRI_TOOLCHAIN
        + " vendor --versioned-dirs /ctx/sysroot-vendor\n",
        encoding="utf-8",
        newline="\n",
    )
    result = run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{context}:/ctx",
            COMPONENTS_TAG,
            "bash",
            "/ctx/vendor.sh",
        ],
        check=False,
    )
    if result.returncode != 0 or not any(target.iterdir()):
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(
            "could not vendor the Miri sysroot crates; the evaluator image would build its "
            "sysroot offline and fail"
        )
    print(f"vendored {sum(1 for p in target.rglob('*') if p.is_file())} sysroot crate files")


def build_components(dockerfile: Path) -> None:
    result = run(
        [
            "docker",
            "build",
            "--build-arg",
            f"BASE_IMAGE={BASE_IMAGE}",
            "-t",
            COMPONENTS_TAG,
            str(CONTEXTS),
        ],
        check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit("components image build failed (this is the one step that needs network)")


def main() -> int:
    CONTEXTS.mkdir(parents=True, exist_ok=True)
    dockerfile = CONTEXTS / "Dockerfile"
    dockerfile.write_text(
        DOCKERFILE.replace("__STABLE__", " ".join(STABLE_COMPONENTS))
        .replace("__MIRI_TOOLCHAIN__", MIRI_TOOLCHAIN)
        .replace("__MIRI_COMPONENTS__", " ".join(MIRI_COMPONENTS)),
        encoding="utf-8",
    )
    # The vendor step needs the pinned nightly, so it runs against the components image and the
    # image is then rebuilt to carry the vendored crates.
    build_components(dockerfile)
    vendor_sysroot_crates(CONTEXTS)
    build_components(dockerfile)
    digest = run(
        ["docker", "image", "inspect", COMPONENTS_TAG, "--format", "{{.Id}}"]
    ).stdout.strip()
    versions = component_versions(digest)
    document = {
        "schema_version": 1,
        "kind": "rust_components_image",
        "note": (
            "Prebuilt component image; the only step that uses a network. build_rust_images.py "
            "copies /usr/local/rustup out of it with --network none."
        ),
        "base_image": {"reference": BASE_IMAGE, "digest": BASE_DIGEST},
        "tag": COMPONENTS_TAG,
        "digest": digest,
        "stable_components": list(STABLE_COMPONENTS),
        "miri_toolchain": MIRI_TOOLCHAIN,
        "miri_components": list(MIRI_COMPONENTS),
        "tools": versions,
        "dockerfile_digest": sha256_file(dockerfile),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}; components image {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
