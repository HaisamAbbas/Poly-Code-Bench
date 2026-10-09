"""Build the offline Rust images and record their identities (Prompt 11, PCB-11-1).

    .venv/Scripts/python.exe scripts/build_rust_images.py [--platform linux/arm64]

Three distinct recipes share one pinned toolchain: ``runtime`` (build/test), ``evaluator``
(clippy/rustfmt/Miri) and ``performance`` (release-profile measurement). The compiler lives in the
pinned base image, so there is no wheel to vendor: task crates are stdlib-only and each task ships
its own ``Cargo.lock``. That lock digest is part of the recorded evaluator identity, exactly as the
base digest and tool versions are.

Components are installed at build time from the pinned recipe; the build then records the versions
reported by the built image itself. Afterwards every scored run uses ``--offline --locked`` and
declares ``network: none``, so a scored run installs nothing and fetches nothing.

``--platform linux/arm64`` builds from the ``...:v1-arm64`` components images
(``fetch_rust_components.py --platform linux/arm64``) and the arm64 Python interpreter pinned in
``config/images/python-v1-arm64.json``, with ``docker buildx build --load``. The Dockerfile's
interpreter library directory and dynamic loader are rewritten for aarch64 in the build context
(the checked-in Dockerfile stays the amd64 recipe), and the outputs are the ``-arm64`` siblings
``config/images/rust-v1-arm64.json`` and ``config/plugins/allowlist-v1-arm64.yaml``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "infra" / "images" / "rust"
PLUGIN = ROOT / "plugins" / "languages" / "rust" / "src" / "polycodebench_lang_rust"
CONTEXTS = ROOT / ".cache" / "rust-image-build"
OUTPUT = ROOT / "config" / "images" / "rust-v1.json"
ALLOWLIST = ROOT / "config" / "plugins" / "allowlist-v1.yaml"
BASE_DIGEST = "sha256:540c902e99c384163b688bbd8b5b8520e94e7731b27f7bd0eaa56ae1960627ab"
BASE_IMAGE = f"rust@{BASE_DIGEST}"
# The sandbox provider drives every guest with `python -I -B -S`, so each Rust image carries the
# interpreter pinned for the Python recipes (copied with its own libraries; see the Dockerfile).
PYTHON_IMAGE = "python@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b"
RECIPES = ("runtime", "evaluator", "performance")
# Miri does not exist on the stable channel; the components image carries an explicitly pinned
# nightly for it, and that version is part of the recorded evaluator identity. This image is the
# Rust analogue of the Python wheelhouse and is built once by scripts/fetch_rust_components.py --
# the only step in the whole Rust pipeline that uses a network.
COMPONENTS_BASE_IMAGE = "pcb-rust-components-base:v1"
COMPONENTS_EVALUATOR_IMAGE = "pcb-rust-components:v1"
# Recipe -> the components image it is built from. This is what makes the recipes genuinely
# distinct: runtime/performance come from the analyzer-free base, evaluator from the analyzer image.
COMPONENTS_FOR_RECIPE = {
    "runtime": COMPONENTS_BASE_IMAGE,
    "performance": COMPONENTS_BASE_IMAGE,
    "evaluator": COMPONENTS_EVALUATOR_IMAGE,
}
MIRI_TOOLCHAIN = "nightly-2026-09-30"
# Tools a recipe is *expected* to provide. Every recipe is probed for all of them so the
# recorded identity states plainly which tools are absent rather than silently omitting them.
EXPECTED_TOOLS = {
    "runtime": ("rustc", "cargo"),
    "evaluator": ("rustc", "cargo", "clippy", "rustfmt", "miri"),
    "performance": ("rustc", "cargo"),
}
ALL_TOOLS = ("rustc", "cargo", "clippy", "rustfmt", "miri")
IGNORED_DIRS = frozenset({"__pycache__", ".git", "target"})
DEFAULT_PLATFORM = "linux/amd64"


@dataclass(frozen=True)
class Target:
    """What differs between the amd64 build and another platform's build."""

    platform: str
    base_image: str
    python_image: str
    suffix: str
    # Exact Dockerfile text -> replacement, applied to the build-context copy only.
    dockerfile_rewrites: dict[str, str] = field(default_factory=dict)

    @property
    def native(self) -> bool:
        return self.platform == DEFAULT_PLATFORM

    def tag(self, tag: str) -> str:
        return tag + self.suffix

    def sibling(self, path: Path) -> Path:
        return path.with_name(f"{path.stem}{self.suffix}{path.suffix}") if self.suffix else path


TARGETS = {
    "linux/amd64": Target("linux/amd64", BASE_IMAGE, PYTHON_IMAGE, ""),
    "linux/arm64": Target(
        "linux/arm64",
        # linux/arm64/v8 manifest of the pinned rust index above.
        "rust@sha256:200f14b0b84ac302774ef5963119f7d949fcf72bd24b365f5ddb829b254c9594",
        # linux/arm64/v8 manifest of the same python:3.12.14-slim index as PYTHON_IMAGE.
        "python@sha256:950206c37262dd86c55659797f6ee418fee30535072f65a82ed470d985f5cda5",
        "-arm64",
        {
            "/usr/lib/x86_64-linux-gnu": "/usr/lib/aarch64-linux-gnu",
            "/opt/pcb/python/lib/ld-linux-x86-64.so.2": "/opt/pcb/python/lib/ld-linux-aarch64.so.1",
        },
    ),
}
TARGET = TARGETS[DEFAULT_PLATFORM]


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def tree_digest(root: Path, subdirs: tuple[str, ...]) -> str:
    entries = {}
    for sub in subdirs:
        for path in sorted((root / sub).rglob("*")):
            if path.is_file() and not IGNORED_DIRS.intersection(path.parts):
                entries[path.relative_to(root).as_posix()] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
    return "sha256:" + hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()


def run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=check, encoding="utf-8")


def recipe_components() -> dict[str, list[str]]:
    document = yaml.safe_load((IMAGES / "recipes.yaml").read_text(encoding="utf-8"))
    return {str(kind): [str(item) for item in items] for kind, items in document.items()}


def platform_dockerfile(text: str) -> str:
    """The checked-in (amd64) Dockerfile rewritten for ``TARGET``; every rewrite must apply."""
    for old, new in TARGET.dockerfile_rewrites.items():
        if old not in text:
            raise SystemExit(f"Dockerfile no longer contains {old!r}; update the arm64 rewrites")
        text = text.replace(old, new)
    return text


def build_context(recipe: str) -> Path:
    context = CONTEXTS / f"{recipe}{TARGET.suffix}"
    if context.exists():
        shutil.rmtree(context)
    (context / "pcb").mkdir(parents=True)
    shutil.copytree(
        PLUGIN / "guest", context / "pcb" / "guest", ignore=shutil.ignore_patterns("__pycache__")
    )
    if (PLUGIN / "rules").is_dir():
        shutil.copytree(
            PLUGIN / "rules",
            context / "pcb" / "rules",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    if TARGET.native:
        shutil.copy(IMAGES / "Dockerfile", context / "Dockerfile")
    else:
        (context / "Dockerfile").write_bytes(
            platform_dockerfile((IMAGES / "Dockerfile").read_bytes().decode("utf-8")).encode(
                "utf-8"
            )
        )
    return context


def run_platform() -> list[str]:
    return [] if TARGET.native else ["--platform", TARGET.platform]


def tool_version(recipe: str, image_digest: str, tool: str) -> str:
    """Ask the built image for a tool's version, so the record comes from the real artifact.

    ``rustup`` installs proxy shims for every tool whether or not the component is present, so a
    tool that is not installed is reported as ``absent`` rather than as a shim version.
    """
    argv = {
        "rustc": ["rustc", "--version"],
        "cargo": ["cargo", "--version"],
        "clippy": ["cargo", "clippy", "--version"],
        "rustfmt": ["cargo", "fmt", "--version"],
        "miri": ["cargo", "+" + MIRI_TOOLCHAIN, "miri", "--version"],
    }[tool]
    result = run(
        [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            *run_platform(),
            "--network",
            "none",
            f"pcb-rust-{recipe}@{image_digest}",
            *argv,
        ],
        check=False,
    )
    if result.returncode != 0:
        return "absent"
    text = (result.stdout + result.stderr).strip()
    match = re.search(r"\d+\.\d+\.\d+[\w.+-]*", text)
    return match.group(0) if match else "unknown"


def require_distinct(records: dict[str, dict[str, object]]) -> None:
    """Fail the build unless the three recipes are materially different.

    The DoD asks for distinct regular/instrumented/performance recipes. Distinct *digests* are not
    enough on their own: an earlier build produced three differently-tagged images with identical
    contents. A recipe is only real if the analyzers actually run in the evaluator image and are
    actually absent from the others, and the three digests differ.
    """
    problems: list[str] = []
    evaluator_tools = records["evaluator"]["tools"]
    assert isinstance(evaluator_tools, dict)
    for tool in ("clippy", "rustfmt", "miri"):
        if evaluator_tools.get(tool) == "absent":
            problems.append(f"evaluator image cannot run {tool}")
    for recipe in ("runtime", "performance"):
        tools = records[recipe]["tools"]
        assert isinstance(tools, dict)
        for tool in ("clippy", "rustfmt", "miri"):
            if tools.get(tool) != "absent":
                problems.append(f"{recipe} image unexpectedly provides {tool}")
    digests = {recipe: str(record["digest"]) for recipe, record in records.items()}
    if len(set(digests.values())) != len(digests):
        problems.append(f"recipes share an image digest: {digests}")
    if problems:
        raise SystemExit("recipes are not distinct: " + "; ".join(problems))


def build_command(recipe: str, tag: str, context: Path) -> list[str]:
    """``docker build`` for amd64 (unchanged); ``buildx --load`` without attestations otherwise."""
    head = (
        ["docker", "build"]
        if TARGET.native
        else [
            "docker",
            "buildx",
            "build",
            "--platform",
            TARGET.platform,
            "--provenance=false",
            "--sbom=false",
            "--load",
        ]
    )
    return [
        *head,
        "--network",
        "none",
        "--pull=false",
        "--build-arg",
        f"BASE_IMAGE={TARGET.base_image}",
        "--build-arg",
        f"COMPONENTS_IMAGE={TARGET.tag(COMPONENTS_FOR_RECIPE[recipe])}",
        "--build-arg",
        f"PYTHON_IMAGE={TARGET.python_image}",
        "--build-arg",
        f"RECIPE={recipe}",
        "-t",
        tag,
        str(context),
    ]


def build(recipe: str) -> dict[str, object]:
    context = build_context(recipe)
    tag = f"pcb-rust-{recipe}:v1{TARGET.suffix}"
    previous = run(["docker", "image", "inspect", tag, "--format", "{{.Id}}"], check=False)
    if previous.returncode == 0:
        old = previous.stdout.strip().removeprefix("sha256:")[:12]
        run(["docker", "tag", tag, f"pcb-rust-{recipe}:prev{TARGET.suffix}-{old}"], check=False)
    result = run(build_command(recipe, tag, context), check=False)
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(f"rust image build failed for recipe {recipe}")
    digest = run(["docker", "image", "inspect", tag, "--format", "{{.Id}}"]).stdout.strip()
    built_for = run(
        ["docker", "image", "inspect", tag, "--format", "{{.Os}}/{{.Architecture}}"]
    ).stdout.strip()
    if built_for != TARGET.platform:
        raise SystemExit(f"{tag} was built for {built_for}, expected {TARGET.platform}")
    # Probe every tool for every recipe: a tool the recipe does not ship is recorded as
    # "absent" so the identity file states the difference instead of omitting it.
    tools = {name: tool_version(recipe, digest, name) for name in ALL_TOOLS}
    return {
        "recipe": recipe,
        "tag": tag,
        "reference": f"pcb-rust-{recipe}@{digest}",
        "digest": digest,
        "rustc": tools["rustc"],
        "cargo": tools["cargo"],
        "tools": tools,
        "expected_tools": list(EXPECTED_TOOLS[recipe]),
        "components": recipe_components()[recipe],
        "guest_and_rules_digest": tree_digest(context / "pcb", ("guest", "rules")),
        "recipe_digest": sha256_file(IMAGES / "recipes.yaml"),
        "dockerfile_digest": sha256_file(context / "Dockerfile"),
    }


def python_digests() -> set[str]:
    existing = TARGET.sibling(ROOT / "config" / "images" / "python-v1.json")
    if not existing.is_file():
        return set()
    document = json.loads(existing.read_text(encoding="utf-8"))
    return {str(record["digest"]) for record in document["images"].values()}


def write_allowlist(rust_images: dict[str, dict[str, object]]) -> None:
    rust = {str(record["digest"]) for record in rust_images.values()}
    python = python_digests()
    lines = [
        "schema_version: 1",
        "kind: language_plugin_allowlist",
        "plugins:",
        "  - plugin_id: python",
        "    entry_point: polycodebench_lang_python.plugin:PythonLanguagePlugin",
        "    api_version: 1",
        '    plugin_version: "0.1.0"',
        "    image_digests:",
        *[f"      - {digest}" for digest in sorted(python)],
        "  - plugin_id: rust",
        "    entry_point: polycodebench_lang_rust.plugin:RustLanguagePlugin",
        "    api_version: 1",
        '    plugin_version: "0.1.0"',
        "    image_digests:",
        *[f"      - {digest}" for digest in sorted(rust)],
    ]
    allowlist = TARGET.sibling(ALLOWLIST)
    allowlist.parent.mkdir(parents=True, exist_ok=True)
    allowlist.write_text("\n".join(lines) + "\n", encoding="utf-8")


def select_target(argv: list[str]) -> argparse.Namespace:
    global TARGET
    parser = argparse.ArgumentParser(prog="build_rust_images")
    parser.add_argument("--platform", choices=sorted(TARGETS), default=DEFAULT_PLATFORM)
    parser.add_argument("--allowlist-only", action="store_true")
    args = parser.parse_args(argv)
    TARGET = TARGETS[args.platform]
    return args


def main(argv: list[str] | None = None) -> int:
    args = select_target(sys.argv[1:] if argv is None else argv)
    output = TARGET.sibling(OUTPUT)
    allowlist = TARGET.sibling(ALLOWLIST)
    if args.allowlist_only:
        write_allowlist(json.loads(output.read_text(encoding="utf-8"))["images"])
        print(f"wrote {allowlist.relative_to(ROOT).as_posix()}")
        return 0
    images = {recipe: build(recipe) for recipe in RECIPES}
    require_distinct(images)
    build_record: dict[str, object] = {
        "network": "none",
        "offline_install": True,
        "components_installed_at_build_time": True,
        "scored_runs_offline": True,
        "miri_toolchain": MIRI_TOOLCHAIN,
        "guest_interpreter": TARGET.python_image,
        "note": (
            "Local development build. The compiler ships in the pinned base image; task "
            "crates are stdlib-only and each task pins its own Cargo.lock, whose digest is "
            "part of the evaluator identity. Miri requires nightly and is installed as a "
            "pinned separate toolchain in the evaluator recipe only. A rebuild changes image "
            "digests, so task "
            "manifests must be resealed and admission re-run."
        ),
    }
    if not TARGET.native:
        build_record["platform"] = TARGET.platform
    document = {
        "schema_version": 1,
        "kind": "rust_images",
        "base_image": {
            "reference": TARGET.base_image,
            "digest": TARGET.base_image.partition("@")[2],
        },
        "build": build_record,
        "rule_bundle_digest": tree_digest(PLUGIN, ("rules",)),
        "guest_digest": tree_digest(PLUGIN, ("guest",)),
        "images": images,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_allowlist(images)
    print(
        f"wrote {output.relative_to(ROOT).as_posix()} and {allowlist.relative_to(ROOT).as_posix()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
