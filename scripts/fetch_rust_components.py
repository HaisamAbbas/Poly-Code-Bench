"""Build the prebuilt Rust components image (Prompt 11, PCB-11-1) — the only online step.

    .venv/Scripts/python.exe scripts/fetch_rust_components.py [--platform linux/arm64]

This is the Rust analogue of the Python wheelhouse download. ``rustup`` unpacks a component and then
deletes its payload, so there is nothing file-level to vendor; the faithful equivalent of the
wheelhouse is therefore an image that already contains every pinned component. This script builds
that image once, **with** network. ``scripts/build_rust_images.py`` then builds the three real
images with ``--network none`` by copying the finished toolchain out of it, so neither those builds
nor any scored run perform an online installation or fetch.

``--platform linux/arm64`` builds the aarch64 components images (``docker buildx build --load``)
from the arm64 manifest of the same pinned ``rust`` index, tags them ``...:v1-arm64`` and writes
``config/images/rust-components-arm64.json``; the amd64 record is never touched. ``rustup`` verifies
every component download against the SHA-256 in the signed channel manifest; for arm64 the record
additionally lists, per installed component, the ``xz_hash`` that the toolchain's own channel
manifest pinned, plus the digest of each channel manifest.

Re-run this only when a pinned component or the pinned Miri toolchain changes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "infra" / "images" / "rust"
CONTEXTS = ROOT / ".cache" / "rust-components-build"
OUTPUT = ROOT / "config" / "images" / "rust-components.json"
BASE_DIGEST = "sha256:540c902e99c384163b688bbd8b5b8520e94e7731b27f7bd0eaa56ae1960627ab"
BASE_IMAGE = f"rust@{BASE_DIGEST}"
COMPONENTS_TAG = "pcb-rust-components:v1"
COMPONENTS_BASE_TAG = "pcb-rust-components-base:v1"
STABLE_TOOLCHAIN = "1.83.0"
STABLE_COMPONENTS = ("clippy", "rustfmt")
MIRI_TOOLCHAIN = "nightly-2026-09-30"
MIRI_COMPONENTS = ("miri", "rust-src")
DEFAULT_PLATFORM = "linux/amd64"


@dataclass(frozen=True)
class Target:
    """What differs between the amd64 components build and another platform's."""

    platform: str
    base_digest: str
    triple: str
    suffix: str

    @property
    def native(self) -> bool:
        return self.platform == DEFAULT_PLATFORM

    @property
    def base_image(self) -> str:
        return f"rust@{self.base_digest}"

    @property
    def contexts(self) -> Path:
        return CONTEXTS if self.native else CONTEXTS.with_name(CONTEXTS.name + self.suffix)

    def tag(self, tag: str) -> str:
        return tag + self.suffix

    def sibling(self, path: Path) -> Path:
        return path.with_name(f"{path.stem}{self.suffix}{path.suffix}") if self.suffix else path


# The amd64 build keeps pinning the multi-platform index (historical behaviour). The arm64 build
# pins the linux/arm64/v8 manifest of that same index, so it cannot silently resolve elsewhere.
TARGETS = {
    "linux/amd64": Target("linux/amd64", BASE_DIGEST, "x86_64-unknown-linux-gnu", ""),
    "linux/arm64": Target(
        "linux/arm64",
        "sha256:200f14b0b84ac302774ef5963119f7d949fcf72bd24b365f5ddb829b254c9594",
        "aarch64-unknown-linux-gnu",
        "-arm64",
    ),
}
TARGET = TARGETS[DEFAULT_PLATFORM]

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


def run_platform() -> list[str]:
    return [] if TARGET.native else ["--platform", TARGET.platform]


def build_head() -> list[str]:
    """``docker build`` for amd64 (unchanged); ``buildx --load`` without attestations otherwise."""
    if TARGET.native:
        return ["docker", "build"]
    return [
        "docker",
        "buildx",
        "build",
        "--platform",
        TARGET.platform,
        "--provenance=false",
        "--sbom=false",
        "--load",
    ]


def component_versions(tag: str, digest: str) -> dict[str, str]:
    """Versions reported by a components image itself.

    `rustup` installs proxy shims for every tool into `~/.cargo/bin` whether or not the
    component exists, so a version is only reported when the command actually runs.
    """

    def ask(*argv: str) -> str:
        result = run(
            [
                "docker",
                "run",
                "--rm",
                "--pull=never",
                *run_platform(),
                "--network",
                "none",
                f"{tag.partition(':')[0]}@{digest}",
                *argv,
            ],
            check=False,
        )
        if result.returncode != 0:
            return "absent"
        text = (result.stdout + result.stderr).strip()
        return text.splitlines()[-1] if text else "unknown"

    return {
        "rustc": ask("rustc", "--version"),
        "cargo": ask("cargo", "--version"),
        "clippy": ask("cargo", "clippy", "--version"),
        "rustfmt": ask("cargo", "fmt", "--version"),
        "miri": ask("cargo", "+" + MIRI_TOOLCHAIN, "miri", "--version"),
    }


def component_checksums(tag: str) -> dict[str, object]:
    """Per-toolchain channel-manifest digest and the pinned ``xz_hash`` of each installed component.

    ``rustup`` refuses a component whose download does not match the manifest hash, so these are the
    hashes the installed payloads were verified against.
    """
    record: dict[str, object] = {}
    for toolchain in (STABLE_TOOLCHAIN, MIRI_TOOLCHAIN):
        root = f"/usr/local/rustup/toolchains/{toolchain}-{TARGET.triple}/lib/rustlib"
        manifest = run(
            [
                "docker",
                "run",
                "--rm",
                "--pull=never",
                *run_platform(),
                "--network",
                "none",
                tag,
                "cat",
                f"{root}/multirust-channel-manifest.toml",
            ]
        ).stdout
        installed = run(
            [
                "docker",
                "run",
                "--rm",
                "--pull=never",
                *run_platform(),
                "--network",
                "none",
                tag,
                "cat",
                f"{root}/components",
            ]
        ).stdout.split()
        document = tomllib.loads(manifest)
        hashes: dict[str, str] = {}
        suffix = f"-{TARGET.triple}"
        for component in installed:
            # `rust-src` is target-independent (`*`); everything else is per host triple.
            package, target = (
                (component.removesuffix(suffix), TARGET.triple)
                if component.endswith(suffix)
                else (component, "*")
            )
            pinned = document["pkg"].get(package, {}).get("target", {}).get(target, {})
            if not (pinned.get("available") and pinned.get("xz_hash")):
                raise SystemExit(f"{toolchain}: no pinned hash for installed component {component}")
            hashes[component] = "sha256:" + pinned["xz_hash"]
        record[toolchain] = {
            "channel_manifest_digest": "sha256:" + hashlib.sha256(manifest.encode()).hexdigest(),
            "manifest_date": document.get("date"),
            "components": hashes,
        }
    return record


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
        + f"-{TARGET.triple}/lib/rustlib/src/rust/library\n"
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
            *run_platform(),
            "-v",
            f"{context}:/ctx",
            TARGET.tag(COMPONENTS_TAG),
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
            *build_head(),
            "--build-arg",
            f"BASE_IMAGE={TARGET.base_image}",
            "-t",
            TARGET.tag(COMPONENTS_TAG),
            str(dockerfile.parent),
        ],
        check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit("components image build failed (this is the one step that needs network)")


BASE_DOCKERFILE = """# Prebuilt *base* Rust component image (Prompt 11): the pinned toolchain as
# it ships, plus the vendored offline crates. Deliberately no clippy, no rustfmt and no
# nightly, so the runtime and performance recipes cannot run an analyzer.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
COPY sysroot-vendor /opt/pcb/vendor
"""


def build_base_components(context: Path, vendor: Path) -> str:
    """Build the analyzer-free components image and return its digest."""
    base_context = context / "base"
    if base_context.exists():
        shutil.rmtree(base_context)
    (base_context / "sysroot-vendor").mkdir(parents=True)
    for path in vendor.rglob("*"):
        if path.is_file():
            target = base_context / "sysroot-vendor" / path.relative_to(vendor)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    (base_context / "Dockerfile").write_text(BASE_DOCKERFILE, encoding="utf-8", newline="\n")
    result = run(
        [
            *build_head(),
            "--network",
            "none",
            "--pull=false",
            "--build-arg",
            f"BASE_IMAGE={TARGET.base_image}",
            "-t",
            TARGET.tag(COMPONENTS_BASE_TAG),
            str(base_context),
        ],
        check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit("base components image build failed")
    return run(
        ["docker", "image", "inspect", TARGET.tag(COMPONENTS_BASE_TAG), "--format", "{{.Id}}"]
    ).stdout.strip()


def select_target(argv: list[str]) -> argparse.Namespace:
    global TARGET
    parser = argparse.ArgumentParser(prog="fetch_rust_components")
    parser.add_argument("--platform", choices=sorted(TARGETS), default=DEFAULT_PLATFORM)
    args = parser.parse_args(argv)
    TARGET = TARGETS[args.platform]
    return args


def main(argv: list[str] | None = None) -> int:
    select_target(sys.argv[1:] if argv is None else argv)
    contexts = TARGET.contexts
    contexts.mkdir(parents=True, exist_ok=True)
    dockerfile = contexts / "Dockerfile"
    dockerfile.write_text(
        DOCKERFILE.replace("__STABLE__", " ".join(STABLE_COMPONENTS))
        .replace("__MIRI_TOOLCHAIN__", MIRI_TOOLCHAIN)
        .replace("__MIRI_COMPONENTS__", " ".join(MIRI_COMPONENTS)),
        encoding="utf-8",
    )
    # The vendor step needs the pinned nightly, so it runs against the components image and the
    # image is then rebuilt to carry the vendored crates.
    if not (contexts / "sysroot-vendor").is_dir():
        (contexts / "sysroot-vendor").mkdir()
    build_components(dockerfile)
    vendor_sysroot_crates(contexts)
    build_components(dockerfile)
    tag = TARGET.tag(COMPONENTS_TAG)
    digest = run(["docker", "image", "inspect", tag, "--format", "{{.Id}}"]).stdout.strip()
    built_for = run(
        ["docker", "image", "inspect", tag, "--format", "{{.Os}}/{{.Architecture}}"]
    ).stdout.strip()
    if built_for != TARGET.platform:
        raise SystemExit(f"{tag} was built for {built_for}, expected {TARGET.platform}")
    base_digest = build_base_components(contexts, contexts / "sysroot-vendor")
    versions = component_versions(tag, digest)
    base_versions = component_versions(TARGET.tag(COMPONENTS_BASE_TAG), base_digest)
    evaluator: dict[str, object] = {
        "tag": tag,
        "digest": digest,
        "stable_components": list(STABLE_COMPONENTS),
        "miri_toolchain": MIRI_TOOLCHAIN,
        "miri_components": list(MIRI_COMPONENTS),
        "tools": versions,
        "dockerfile_digest": sha256_file(dockerfile),
    }
    document: dict[str, object] = {
        "schema_version": 1,
        "kind": "rust_components_images",
        "note": (
            "Prebuilt component images; the only step that uses a network. build_rust_images.py "
            "builds the three recipes from these with --network none. The base image carries no "
            "analyzers, which is what keeps the runtime and performance recipes genuinely "
            "distinct from the evaluator recipe."
        ),
        "base_image": {"reference": TARGET.base_image, "digest": TARGET.base_digest},
        "evaluator": evaluator,
        "base": {
            "tag": TARGET.tag(COMPONENTS_BASE_TAG),
            "digest": base_digest,
            "tools": base_versions,
            "dockerfile_digest": sha256_file(contexts / "base" / "Dockerfile"),
        },
    }
    if not TARGET.native:
        document["platform"] = TARGET.platform
        document["base_image"] = {
            "reference": TARGET.base_image,
            "digest": TARGET.base_digest,
            "index_digest": BASE_DIGEST,
        }
        evaluator["host_triple"] = TARGET.triple
        evaluator["verified_component_hashes"] = component_checksums(
            f"{tag.partition(':')[0]}@{digest}"
        )
    output = TARGET.sibling(OUTPUT)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {output.relative_to(ROOT).as_posix()}; components image {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
