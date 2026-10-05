"""Build the offline C++ images and record their identities (Prompt 21, PCB-21-1).

Three recipes are built from two components images, and the recipe distinction is real rather
than cosmetic: the runtime and performance images must not contain clang-tidy or cppcheck, because
a candidate must not be able to read, from inside the image it is scored in, the exact checks that
will judge it. The performance image additionally bakes the release flags, so two candidates that
both ask for a release build get the same machine code.

`scripts/fetch_cpp_components.py` is the only step that uses a network. This script builds with
``--network none``, probes the *built* images for their real tool versions, refuses to record a
record set whose recipes are not materially different, writes ``config/images/cpp-v1.json`` and
updates the administrator allowlist.

    .venv/Scripts/python.exe scripts/build_cpp_images.py
    .venv/Scripts/python.exe scripts/build_cpp_images.py --allowlist-only
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml  # type: ignore[import-untyped,unused-ignore]

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "infra" / "images" / "cpp"
PLUGIN = ROOT / "plugins" / "languages" / "cpp" / "src" / "polycodebench_lang_cpp"
CONTEXTS = ROOT / ".cache" / "cpp-image-build"
OUTPUT = ROOT / "config" / "images" / "cpp-v1.json"
ALLOWLIST = ROOT / "config" / "plugins" / "allowlist-v1.yaml"
LOCK = ROOT / "config" / "languages" / "cpp-toolchain-v1.json"
RECIPES = ("runtime", "evaluator", "performance")
# The components images are built once by scripts/fetch_cpp_components.py; this step is offline.
COMPONENTS_FOR_RECIPE = {
    "runtime": "pcb-cpp-components-runtime:v1",
    "evaluator": "pcb-cpp-components-evaluator:v1",
    "performance": "pcb-cpp-components-runtime:v1",
}
# The sandbox provider drives every guest with `python -I -B -S`, so each C++ image carries the
# same pinned interpreter the Python recipes use (copied with its own libraries; see Dockerfile).
PYTHON_IMAGE = "python@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b"
# Tools every recipe is probed for, so a recipe that does not ship one records "absent" instead of
# silently omitting it.
ALL_TOOLS = ("clang++", "clang-tidy", "cppcheck", "llvm-symbolizer")
EXPECTED_TOOLS = {
    "runtime": ("clang++", "llvm-symbolizer"),
    "evaluator": ("clang++", "clang-tidy", "cppcheck", "llvm-symbolizer"),
    "performance": ("clang++", "llvm-symbolizer"),
}
IGNORED_DIRS = frozenset({"__pycache__", ".git"})


def base_image() -> str:
    return str(json.loads(LOCK.read_text(encoding="utf-8"))["base_image"])


def release_flags() -> str:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    return " ".join(lock["build_profiles"]["release"]["cxxflags"])


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


def build_context(recipe: str) -> Path:
    context = CONTEXTS / recipe
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
    shutil.copy(IMAGES / "Dockerfile", context / "Dockerfile")
    return context


def tool_version(recipe: str, image_digest: str, tool: str) -> str:
    """Ask the built image for a tool's version, so the record comes from the real artifact.

    A tool that is not installed returns non-zero; that is reported as ``absent`` rather than
    guessed, because the recipe distinction is the point of these images.
    """
    argv = {
        "clang++": ["clang++", "--version"],
        "clang-tidy": ["clang-tidy", "--version"],
        "cppcheck": ["cppcheck", "--version"],
        "llvm-symbolizer": ["llvm-symbolizer-14", "--version"],
    }[tool]
    result = run(
        [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--network",
            "none",
            f"pcb-cpp-{recipe}@{image_digest}",
            *argv,
        ],
        check=False,
    )
    if result.returncode != 0:
        return "absent"
    text = (result.stdout + result.stderr).strip()
    if tool == "cppcheck":
        # Cppcheck reports only major.minor (for example ``Cppcheck 2.10``); the generic
        # three-component version matcher used by clang therefore recorded a working analyzer as
        # ``unknown``. The image digest and pinned Debian package version remain in the same record.
        match = re.search(r"\bCppcheck\s+(\d+(?:\.\d+)+)", text, re.IGNORECASE)
        return match.group(1) if match else "unknown"
    match = re.search(r"\d+\.\d+\.\d+[\w.+-]*", text)
    return match.group(0) if match else "unknown"


def require_distinct(records: dict[str, dict[str, object]]) -> None:
    """Fail unless the three recipes are materially different.

    Distinct digests alone prove nothing: an earlier build produced three differently-tagged
    images with identical contents. A recipe is real only when the analyzers run in the evaluator
    image, are absent from the other two, and the digests differ.
    """
    problems: list[str] = []
    evaluator_tools = records["evaluator"]["tools"]
    assert isinstance(evaluator_tools, dict)
    for tool in ("clang-tidy", "cppcheck"):
        if evaluator_tools.get(tool) == "absent":
            problems.append(f"evaluator image cannot run {tool}")
    for recipe in ("runtime", "performance"):
        tools = records[recipe]["tools"]
        assert isinstance(tools, dict)
        for tool in ("clang-tidy", "cppcheck"):
            if tools.get(tool) != "absent":
                problems.append(f"{recipe} image unexpectedly provides {tool}")
    for recipe in RECIPES:
        tools = records[recipe]["tools"]
        assert isinstance(tools, dict)
        for tool in ALL_TOOLS:
            if tool in EXPECTED_TOOLS[recipe] and tools.get(tool) == "absent":
                problems.append(f"{recipe} image cannot run {tool}")
            if tool not in EXPECTED_TOOLS[recipe] and tools.get(tool) != "absent":
                problems.append(f"{recipe} image unexpectedly provides {tool}")
    if records["performance"]["release_flags"] != release_flags():
        problems.append("the performance image does not bake the pinned release flags")
    for recipe in ("runtime", "evaluator"):
        if records[recipe]["release_flags"]:
            problems.append(f"{recipe} image must not bake release measurement flags")
    digests = {recipe: str(record["digest"]) for recipe, record in records.items()}
    if len(set(digests.values())) != len(digests):
        problems.append(f"recipes share an image digest: {digests}")
    if problems:
        raise SystemExit("recipes are not distinct: " + "; ".join(problems))


def build(recipe: str) -> dict[str, object]:
    context = build_context(recipe)
    tag = f"pcb-cpp-{recipe}:v1"
    previous = run(["docker", "image", "inspect", tag, "--format", "{{.Id}}"], check=False)
    if previous.returncode == 0:
        old = previous.stdout.strip().removeprefix("sha256:")[:12]
        run(["docker", "tag", tag, f"pcb-cpp-{recipe}:prev-{old}"], check=False)
    result = run(
        [
            "docker",
            "build",
            "--network",
            "none",
            "--pull=false",
            "--build-arg",
            f"COMPONENTS_IMAGE={COMPONENTS_FOR_RECIPE[recipe]}",
            "--build-arg",
            f"PYTHON_IMAGE={PYTHON_IMAGE}",
            "--build-arg",
            f"RECIPE={recipe}",
            "-t",
            tag,
            str(context),
        ],
        check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(f"cpp image build failed for recipe {recipe}")
    digest = run(["docker", "image", "inspect", tag, "--format", "{{.Id}}"]).stdout.strip()
    tools = {name: tool_version(recipe, digest, name) for name in ALL_TOOLS}
    return {
        "recipe": recipe,
        "tag": tag,
        "reference": f"pcb-cpp-{recipe}@{digest}",
        "digest": digest,
        "clang": tools["clang++"],
        "tools": tools,
        "expected_tools": list(EXPECTED_TOOLS[recipe]),
        "components": recipe_components()[recipe],
        "guest_and_rules_digest": tree_digest(context / "pcb", ("guest", "rules")),
        "recipe_digest": sha256_file(IMAGES / "recipes.yaml"),
        "dockerfile_digest": sha256_file(IMAGES / "Dockerfile"),
        "release_flags": release_flags() if recipe == "performance" else "",
    }


def recorded_digests(path: Path) -> set[str]:
    """Image digests an identity file already records, so the allowlist follows the artifacts."""
    if not path.is_file():
        return set()
    document = json.loads(path.read_text(encoding="utf-8"))
    return {str(record["digest"]) for record in document["images"].values()}


def known_digests() -> dict[str, set[str]]:
    """Recorded image digests keyed by language, including extensions added later than C++."""
    found: dict[str, set[str]] = {}
    for path in sorted((ROOT / "config" / "images").glob("*-v1.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        language = normalize(str(document.get("kind", ""))).removesuffix("-images")
        images = document.get("images")
        if language and isinstance(images, dict) and images:
            found[language] = {str(record["digest"]) for record in images.values()}
    return found


def plugin_entry_points() -> dict[str, str]:
    """Read every registered language entry point from its distribution metadata."""
    found: dict[str, str] = {}
    for pyproject in sorted((ROOT / "plugins" / "languages").glob("*/pyproject.toml")):
        for line in pyproject.read_text(encoding="utf-8").splitlines():
            match = re.match(r'\s*([a-z][a-z0-9]*)\s*=\s*"([\w.]+:[A-Za-z]+)"', line)
            if match:
                found[match.group(1)] = match.group(2)
    return found


def write_allowlist(cpp_images: dict[str, dict[str, object]]) -> None:
    """Regenerate the administrator allowlist from the recorded identities themselves.

    The allowlist is the set of images a plan is allowed to run, so it is derived from every
    identity file rather than a hand-maintained subset: rebuilding C++ cannot unregister a
    language added after this script was written.
    """
    digests_by_language = known_digests()
    digests_by_language["cpp"] = {str(record["digest"]) for record in cpp_images.values()}
    entry_points = plugin_entry_points()
    missing = sorted(set(entry_points) - set(digests_by_language))
    if missing:
        raise SystemExit("refusing to omit language image identities: " + ", ".join(missing))
    document = {
        "schema_version": 1,
        "kind": "language_plugin_allowlist",
        "plugins": [
            {
                "plugin_id": plugin,
                "entry_point": entry_points[plugin],
                "api_version": 1,
                "plugin_version": "0.1.0",
                "image_digests": sorted(digests_by_language[plugin]),
            }
            for plugin in sorted(entry_points)
        ],
    }
    ALLOWLIST.parent.mkdir(parents=True, exist_ok=True)
    ALLOWLIST.write_text(
        yaml.safe_dump(document, sort_keys=False, default_flow_style=False), encoding="utf-8"
    )


def main() -> int:
    if "--allowlist-only" in sys.argv:
        write_allowlist(json.loads(OUTPUT.read_text(encoding="utf-8"))["images"])
        print(f"wrote {ALLOWLIST.relative_to(ROOT)}")
        return 0
    images = {recipe: build(recipe) for recipe in RECIPES}
    require_distinct(images)
    document = {
        "schema_version": 1,
        "kind": "cpp_images",
        "base_image": {"reference": base_image(), "digest": base_image().split("@")[-1]},
        "build": {
            "network": "none",
            "offline_install": True,
            "components_installed_at_build_time": True,
            "scored_runs_offline": True,
            "guest_interpreter": PYTHON_IMAGE,
            "toolchain_lock": "config/languages/cpp-toolchain-v1.json",
            "note": (
                "Local development build. The compiler, the analyzers and the sanitizer runtimes "
                "ship in the pinned base images; a task's recipe and its declared sanitizers come "
                "from config/languages/cpp-toolchain-v1.json, whose digest is part of every tool "
                "identity. clang-tidy and cppcheck exist only in the evaluator recipe. The "
                "performance recipe bakes the release flags, so a sanitized binary can never be "
                "timed as release code. A rebuild changes image digests, so task manifests must "
                "be resealed and admission re-run."
            ),
        },
        "rule_bundle_digest": tree_digest(PLUGIN, ("rules",)),
        "guest_digest": tree_digest(PLUGIN, ("guest",)),
        "images": images,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_allowlist(images)
    print(f"wrote {OUTPUT.relative_to(ROOT)} and {ALLOWLIST.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
