"""Build the offline Python runtime and evaluator images and record their identities.

Prerequisite (the only online step, done once and hash-verified against the lock files):

    pip download --require-hashes --only-binary=:all: --no-deps --python-version 3.12 \
        --implementation cp --platform manylinux_2_28_x86_64 ... \
        -r infra/images/python/evaluator.lock -d .wheelhouse/evaluator

The images are then built with ``docker build --network none``. The recorded identities are
written to ``config/images/python-v1.json`` and are what plans, the plugin allowlist and task
manifests pin. Rebuilding produces new layer timestamps and therefore a new image digest; the
digest recorded here is the identity every later run uses.
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
    context = CONTEXTS / kind
    if context.exists():
        shutil.rmtree(context)
    (context / "wheelhouse").mkdir(parents=True)
    lock = IMAGES / f"{kind}.lock"
    shutil.copy(lock, context / lock.name)
    wanted = lock_packages(lock)
    copied = set()
    for wheel in sorted(WHEELHOUSE.glob("*.whl")):
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


def build(kind: str) -> dict[str, object]:
    context = build_context(kind)
    lock = IMAGES / f"{kind}.lock"
    tag = f"pcb-python-{kind}:v1"
    previous = run(["docker", "image", "inspect", tag, "--format", "{{.Id}}"], check=False)
    if previous.returncode == 0:
        # Keep the previous build resolvable by digest for runs that already started.
        old_id = previous.stdout.strip().removeprefix("sha256:")[:12]
        run(["docker", "tag", tag, f"pcb-python-{kind}:prev-{old_id}"])
    result = run(
        [
            "docker",
            "build",
            "--network",
            "none",
            "--pull=false",
            "--build-arg",
            f"BASE_IMAGE={BASE_IMAGE}",
            "--build-arg",
            f"LOCKFILE={lock.name}",
            "-t",
            tag,
            str(context),
        ],
        check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(f"image build failed for {kind}")
    digest = run(["docker", "image", "inspect", tag, "--format", "{{.Id}}"]).stdout.strip()
    listing = run(
        [
            "docker",
            "run",
            "--rm",
            "--pull=never",
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
        ["docker", "run", "--rm", "--pull=never", f"pcb-python-{kind}@{digest}", "python", "-V"]
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
    ALLOWLIST.parent.mkdir(parents=True, exist_ok=True)
    ALLOWLIST.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    if "--allowlist-only" in sys.argv:
        write_allowlist(json.loads(OUTPUT.read_text(encoding="utf-8")))
        print(f"wrote {ALLOWLIST.relative_to(ROOT)}")
        return 0
    if not WHEELHOUSE.is_dir():
        raise SystemExit("run the documented wheelhouse download first")
    document = {
        "schema_version": 1,
        "kind": "python_images",
        "base_image": {"reference": BASE_IMAGE, "digest": BASE_DIGEST},
        "build": {
            "network": "none",
            "offline_install": True,
            "wheelhouse_wheels": len(list(WHEELHOUSE.glob("*.whl"))),
            "note": (
                "Local development build; a rebuild yields a new image digest. Registry "
                "publication and production-worker pinning belong to the deployment prompts."
            ),
        },
        "rule_bundle_digest": tree_digest(PLUGIN, ("rules",)),
        "guest_digest": tree_digest(PLUGIN, ("guest",)),
        "images": {kind: build(kind) for kind in ("runtime", "evaluator")},
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_allowlist(document)
    print(f"wrote {OUTPUT.relative_to(ROOT)} and {ALLOWLIST.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
