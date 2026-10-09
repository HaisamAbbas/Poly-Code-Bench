"""Build the offline Python runtime and evaluator images and record their identities.

Prerequisite (the only online step, done once and hash-verified against the lock files):

    pip download --require-hashes --only-binary=:all: --no-deps --python-version 3.12 \
        --implementation cp --platform manylinux_2_28_x86_64 ... \
        -r infra/images/python/evaluator.lock -d .wheelhouse/evaluator

The images are then built with ``docker build --network none``. The recorded identities are
written to ``config/images/python-v1.json`` and are what plans, the plugin allowlist and task
manifests pin. Rebuilding produces new layer timestamps and therefore a new image digest; the
digest recorded here is the identity every later run uses.

``--platform linux/arm64`` builds the aarch64 images instead (``docker buildx build --load``; under
QEMU emulation on an amd64 workstation, natively on an arm64 host). It reads the aarch64 wheelhouse
``.wheelhouse/evaluator-arm64``, downloaded from the same hash-pinned lock files:

    pip download --require-hashes --only-binary=:all: --no-deps --python-version 3.12 \
        --implementation cp --abi cp312 --abi abi3 --abi none --platform manylinux_2_28_aarch64 \
        --platform manylinux_2_17_aarch64 --platform manylinux2014_aarch64 --platform any \
        -r infra/images/python/evaluator.lock -d .wheelhouse/evaluator-arm64

and writes only the ``-arm64`` siblings: ``config/images/python-v1-arm64.json``,
``config/plugins/allowlist-v1-arm64.yaml`` and the solve-worker pins
``config/worker/local-image-allowlist-arm64.json`` / ``local-small-resource-arm64.json``. An arm64
build never touches the amd64 files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "infra" / "images" / "python"
PLUGIN = ROOT / "plugins" / "languages" / "python" / "src" / "polycodebench_lang_python"
WHEELHOUSE = ROOT / ".wheelhouse" / "evaluator"
CONTEXTS = ROOT / ".cache" / "image-build"
OUTPUT = ROOT / "config" / "images" / "python-v1.json"
BASE_DIGEST = "sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b"
BASE_IMAGE = f"python@{BASE_DIGEST}"
TOOLS = {
    "runtime": ("pytest", "hypothesis"),
    "evaluator": ("pytest", "hypothesis", "ruff", "mypy", "bandit", "semgrep"),
}
DEFAULT_PLATFORM = "linux/amd64"


@dataclass(frozen=True)
class Target:
    """Everything that differs between the amd64 build and another platform's build."""

    platform: str
    base_digest: str
    wheelhouse: Path
    suffix: str

    @property
    def native(self) -> bool:
        return self.platform == DEFAULT_PLATFORM

    @property
    def base_image(self) -> str:
        return f"python@{self.base_digest}"

    def sibling(self, path: Path) -> Path:
        """``python-v1.json`` -> ``python-v1-arm64.json``; the amd64 path is returned unchanged."""
        return path.with_name(f"{path.stem}{self.suffix}{path.suffix}") if self.suffix else path


# Both base digests are per-platform manifests of the same python:3.12.14-slim (trixie) index
# sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f.
TARGETS = {
    "linux/amd64": Target("linux/amd64", BASE_DIGEST, WHEELHOUSE, ""),
    "linux/arm64": Target(
        "linux/arm64",
        "sha256:950206c37262dd86c55659797f6ee418fee30535072f65a82ed470d985f5cda5",
        ROOT / ".wheelhouse" / "evaluator-arm64",
        "-arm64",
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
            if path.is_file() and "__pycache__" not in path.parts:
                entries[path.relative_to(root).as_posix()] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
    return "sha256:" + hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()


def run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=check, encoding="utf-8")


def lock_packages(lock: Path) -> dict[str, str]:
    packages = {}
    for line in lock.read_text(encoding="utf-8").splitlines():
        if line and not line[0].isspace() and "==" in line and not line.startswith("#"):
            name, _, rest = line.partition("==")
            packages[normalize(name.strip())] = rest.split(" ")[0].strip()
    return packages


def build_context(kind: str) -> Path:
    context = CONTEXTS / f"{kind}{TARGET.suffix}"
    if context.exists():
        shutil.rmtree(context)
    (context / "wheelhouse").mkdir(parents=True)
    lock = IMAGES / f"{kind}.lock"
    shutil.copy(lock, context / lock.name)
    wanted = lock_packages(lock)
    copied = set()
    for wheel in sorted(TARGET.wheelhouse.glob("*.whl")):
        name = normalize(wheel.name.split("-")[0])
        if name in wanted:
            shutil.copy(wheel, context / "wheelhouse" / wheel.name)
            copied.add(name)
    missing = set(wanted) - copied
    if missing:
        raise SystemExit(f"wheelhouse is missing wheels for: {sorted(missing)}")
    shutil.copytree(
        PLUGIN / "guest", context / "pcb" / "guest", ignore=shutil.ignore_patterns("__pycache__")
    )
    shutil.copytree(
        PLUGIN / "rules", context / "pcb" / "rules", ignore=shutil.ignore_patterns("__pycache__")
    )
    shutil.copy(IMAGES / "Dockerfile", context / "Dockerfile")
    return context


def build_command(tag: str, lock: Path, context: Path) -> list[str]:
    """The amd64 command is unchanged; another platform goes through ``buildx --load``.

    Provenance/SBOM attestations are disabled so the loaded image is a single platform manifest
    whose digest is the image ID, exactly as for the amd64 build.
    """
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
        f"LOCKFILE={lock.name}",
        "-t",
        tag,
        str(context),
    ]


def run_platform() -> list[str]:
    return [] if TARGET.native else ["--platform", TARGET.platform]


def build(kind: str) -> dict[str, object]:
    context = build_context(kind)
    lock = IMAGES / f"{kind}.lock"
    tag = f"pcb-python-{kind}:v1{TARGET.suffix}"
    previous = run(["docker", "image", "inspect", tag, "--format", "{{.Id}}"], check=False)
    if previous.returncode == 0:
        # Keep the previous build resolvable by digest for runs that already started.
        old_id = previous.stdout.strip().removeprefix("sha256:")[:12]
        run(["docker", "tag", tag, f"pcb-python-{kind}:prev{TARGET.suffix}-{old_id}"])
    result = run(build_command(tag, lock, context), check=False)
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(f"image build failed for {kind}")
    digest = run(["docker", "image", "inspect", tag, "--format", "{{.Id}}"]).stdout.strip()
    built_for = run(
        ["docker", "image", "inspect", tag, "--format", "{{.Os}}/{{.Architecture}}"]
    ).stdout.strip()
    if built_for != TARGET.platform:
        raise SystemExit(f"{tag} was built for {built_for}, expected {TARGET.platform}")
    listing = run(
        [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            *run_platform(),
            "--network",
            "none",
            f"pcb-python-{kind}@{digest}",
            "python",
            "-m",
            "pip",
            "list",
            "--format",
            "json",
        ]
    )
    installed = {normalize(item["name"]): item["version"] for item in json.loads(listing.stdout)}
    pinned = lock_packages(lock)
    drift = {n: (v, installed.get(n)) for n, v in pinned.items() if installed.get(n) != v}
    if drift:
        raise SystemExit(f"installed packages differ from the lock: {drift}")
    python_version = run(
        [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            *run_platform(),
            f"pcb-python-{kind}@{digest}",
            "python",
            "-V",
        ]
    ).stdout.strip()
    return {
        "tag": tag,
        "reference": f"pcb-python-{kind}@{digest}",
        "digest": digest,
        "python": python_version,
        "lock_file": f"infra/images/python/{lock.name}",
        "lock_digest": sha256_file(lock),
        "tools": {name: installed[name] for name in TOOLS[kind]},
        "installed_package_count": len(installed),
        "guest_and_rules_digest": tree_digest(context / "pcb", ("guest", "rules")),
    }


ALLOWLIST = ROOT / "config" / "plugins" / "allowlist-v1.yaml"
WORKER_IMAGE_ALLOWLIST = ROOT / "config" / "worker" / "local-image-allowlist.json"
WORKER_RESOURCE_SPEC = ROOT / "config" / "worker" / "local-small-resource.json"


def write_allowlist(document: dict[str, object]) -> None:
    """Administrator allowlist: entry point + the image digests this plugin may plan against."""
    images = document["images"]
    assert isinstance(images, dict)
    digests = sorted({images[kind]["digest"] for kind in ("runtime", "evaluator")})
    lines = [
        "schema_version: 1",
        "kind: language_plugin_allowlist",
        "plugins:",
        "  - plugin_id: python",
        "    entry_point: polycodebench_lang_python.plugin:PythonLanguagePlugin",
        "    api_version: 1",
        '    plugin_version: "0.1.0"',
        "    image_digests:",
        *[f"      - {digest}" for digest in digests],
    ]
    allowlist = TARGET.sibling(ALLOWLIST)
    allowlist.parent.mkdir(parents=True, exist_ok=True)
    allowlist.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_worker_pins() -> list[Path]:
    """Solve-worker pins for a non-default platform: same base image, this platform's digest.

    The amd64 files are maintained by hand and stay untouched. The siblings are derived from them,
    so every resource limit is identical and only the image identity differs.
    """
    if TARGET.native:
        return []
    amd64_reference = next(iter(json.loads(WORKER_IMAGE_ALLOWLIST.read_text(encoding="utf-8"))))
    reference = amd64_reference.partition("@")[0] + "@" + TARGET.base_digest
    allowlist = TARGET.sibling(WORKER_IMAGE_ALLOWLIST)
    allowlist.write_text(
        json.dumps({reference: TARGET.base_digest}, indent=2) + "\n", encoding="utf-8"
    )
    resource = json.loads(WORKER_RESOURCE_SPEC.read_text(encoding="utf-8"))
    resource["image"] = reference
    resource["image_digest"] = TARGET.base_digest
    spec = TARGET.sibling(WORKER_RESOURCE_SPEC)
    spec.write_text(json.dumps(resource, indent=2) + "\n", encoding="utf-8")
    return [allowlist, spec]


def select_target(argv: list[str]) -> argparse.Namespace:
    global TARGET
    parser = argparse.ArgumentParser(prog="build_python_images")
    parser.add_argument("--platform", choices=sorted(TARGETS), default=DEFAULT_PLATFORM)
    parser.add_argument("--allowlist-only", action="store_true")
    args = parser.parse_args(argv)
    TARGET = TARGETS[args.platform]
    return args


def main(argv: list[str] | None = None) -> int:
    args = select_target(sys.argv[1:] if argv is None else argv)
    output = TARGET.sibling(OUTPUT)
    if args.allowlist_only:
        write_allowlist(json.loads(output.read_text(encoding="utf-8")))
        print(f"wrote {TARGET.sibling(ALLOWLIST).relative_to(ROOT)}")
        return 0
    if not TARGET.wheelhouse.is_dir():
        raise SystemExit("run the documented wheelhouse download first")
    build_record: dict[str, object] = {
        "network": "none",
        "offline_install": True,
        "wheelhouse_wheels": len(list(TARGET.wheelhouse.glob("*.whl"))),
        "note": (
            "Local development build; a rebuild yields a new image digest. Registry "
            "publication and production-worker pinning belong to the deployment prompts."
        ),
    }
    if not TARGET.native:
        build_record["platform"] = TARGET.platform
    document = {
        "schema_version": 1,
        "kind": "python_images",
        "base_image": {"reference": TARGET.base_image, "digest": TARGET.base_digest},
        "build": build_record,
        "rule_bundle_digest": tree_digest(PLUGIN, ("rules",)),
        "guest_digest": tree_digest(PLUGIN, ("guest",)),
        "images": {kind: build(kind) for kind in ("runtime", "evaluator")},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_allowlist(document)
    written = [output, TARGET.sibling(ALLOWLIST), *write_worker_pins()]
    print("wrote " + ", ".join(path.relative_to(ROOT).as_posix() for path in written))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
