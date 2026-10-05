"""Build the offline Go images and record their identities (Prompt 22, PCB-22-1).

    .venv/Scripts/python.exe scripts/build_go_images.py

Three distinct recipes share one pinned toolchain: ``runtime`` (build/test/vet/race),
``evaluator`` (staticcheck and gosec) and ``performance`` (release-profile measurement). The Go
toolchain lives in the pinned base image, so there is nothing to vendor for stdlib-only task
modules; each task ships its own ``go.mod``/``go.sum``, and that pair's digest is part of the
recorded evaluator identity exactly as the base digest and tool versions are.

Analyzers are installed at build time from the pinned components recipe; the build then records the
versions reported by the built image itself. Afterwards every scored run declares ``network: none``
and sets ``GOPROXY=off``/``GOTOOLCHAIN=local``, so a scored run installs nothing and fetches
nothing - and an unpinned module graph fails loudly instead of resolving from the network.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import yaml

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "infra" / "images" / "go"
ALLOWLIST = ROOT / "config" / "plugins" / "allowlist-v1.yaml"
PLUGIN = ROOT / "plugins" / "languages" / "go" / "src" / "polycodebench_lang_go"
CONTEXTS = ROOT / ".cache" / "go-image-build"
OUTPUT = ROOT / "config" / "images" / "go-v1.json"
BASE_DIGEST = "sha256:abe4f87f354c4f6d7ee3fb11b241c6b6c24a32ca50a2ebcc30493a2168e14048"
BASE_IMAGE = f"golang@{BASE_DIGEST}"
# The sandbox provider drives every guest with `python -I -B -S`, so each Go image carries the
# interpreter pinned for the Python recipes (copied with its own libraries; see the Dockerfile).
PYTHON_IMAGE = "python@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b"
RECIPES = ("runtime", "evaluator", "performance")
COMPONENTS_BASE_IMAGE = "pcb-go-components-base:v1"
COMPONENTS_EVALUATOR_IMAGE = "pcb-go-components:v1"
# Recipe -> the components image it is built from. This is what makes the recipes genuinely
# distinct: runtime/performance come from the analyzer-free base, evaluator from the analyzer image.
COMPONENTS_FOR_RECIPE = {
    "runtime": COMPONENTS_BASE_IMAGE,
    "performance": COMPONENTS_BASE_IMAGE,
    "evaluator": COMPONENTS_EVALUATOR_IMAGE,
}
# Tools a recipe is *expected* to provide. Every recipe is probed for all of them so the recorded
# identity states plainly which tools are absent rather than silently omitting them.
EXPECTED_TOOLS = {
    "runtime": ("go-build", "go-test", "gofmt", "go-vet", "go-test-race"),
    "evaluator": (
        "go-build",
        "go-test",
        "gofmt",
        "go-vet",
        "go-test-race",
        "staticcheck",
        "gosec",
    ),
    "performance": ("go-build", "go-test", "gofmt", "go-vet"),
}
ALL_TOOLS = ("go-build", "go-test", "gofmt", "go-vet", "go-test-race", "staticcheck", "gosec")
IGNORED_DIRS = frozenset({"__pycache__", ".git", "target"})


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


def _recipe_document() -> dict[str, Any]:
    return cast(
        dict[str, Any], yaml.safe_load((IMAGES / "recipes.yaml").read_text(encoding="utf-8"))
    )


def recipe_components() -> dict[str, list[str]]:
    return {
        str(kind): [str(item) for item in items]
        for kind, items in _recipe_document().items()
        if isinstance(items, list) and kind != "instrumented_recipes"
    }


def declared_instrumentation() -> dict[str, str]:
    """The instrumentation each recipe *declares* for the plans that target it.

    The race detector ships inside the pinned `golang` base, so every image can run `-race`.
    Excluding instrumentation from the measurement lane is therefore a contract about which
    recipe a plan may use, recorded here and enforced by ``ImageIdentities.require_release_recipe``
    - the same arrangement the C sanitizer runtimes use. Probing the image for a missing feature
    would only prove that the compiler is broken.
    """
    declared = _recipe_document().get("instrumentation")
    if not isinstance(declared, dict) or set(declared) != set(RECIPES):
        raise SystemExit(
            "recipes.yaml must declare `instrumentation` for every recipe: " + ", ".join(RECIPES)
        )
    return {str(name): str(value) for name, value in declared.items()}


def instrumented_recipes() -> list[str]:
    """Recipes a plan may target with an instrumented build (the runtime lane runs -race)."""
    declared = _recipe_document().get("instrumented_recipes")
    if not isinstance(declared, list) or not declared:
        raise SystemExit("recipes.yaml must declare `instrumented_recipes`")
    return sorted(str(name) for name in declared)


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

    A tool this recipe does not ship is recorded as ``absent`` rather than omitted, so the identity
    file states the recipe difference instead of leaving a reader to infer it.

    The race detector is the one case that is probed by *doing* rather than by printing a version:
    a tiny module is compiled and run under ``-race``, which only succeeds where the detector is
    installed, the C toolchain is present and cgo works. An image that cannot run it is recorded
    as ``absent`` instead of being assumed to have it.
    """
    if tool == "go-test-race":
        return "supported" if _race_works(recipe, image_digest) else "absent"
    argv = {
        "go-build": ["go", "version"],
        "go-test": ["go", "version"],
        "gofmt": ["gofmt", "-h"],
        "go-vet": ["go", "tool", "vet", "-V=full"],
        "staticcheck": ["staticcheck", "-version"],
        # gosec's CLI reports `Version: dev` for module installs. Go embeds the exact module
        # version in the executable's build info, which is the version bound to this image.
        "gosec": ["go", "version", "-m", "/opt/pcb/bin/gosec"],
    }[tool]
    result = run(
        [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--network",
            "none",
            f"pcb-go-{recipe}@{image_digest}",
            *argv,
        ],
        check=False,
    )
    if result.returncode != 0 and tool != "gofmt":
        return "absent"
    text = (result.stdout + result.stderr).strip()
    if not text:
        return "unknown"
    if tool == "gosec":
        match = re.search(r"(?m)^\s*mod\s+github\.com/securego/gosec/v2\s+(v\S+)", text)
        return match.group(1) if match else "unknown"
    if tool == "gofmt":
        # gofmt has no version flag: the image digest plus the recorded base digest is its
        # identity, and its presence is proven by the flag being accepted.
        return f"bundled-{BASE_DIGEST[7:19]}"
    match = re.search(r"\d+\.\d+\.\d+[\w.+-]*", text)
    return match.group(0) if match else text.splitlines()[0].strip()[:60]


def _race_works(recipe: str, image_digest: str) -> bool:
    """Whether this image can actually run an instrumented test."""
    manifest = "printf 'module probe\\n\\ngo 1.26\\n' > go.mod"
    source = "printf 'package probe\\n\\nfunc One() int { return 1 }\\n' > probe.go"
    test = (
        "printf 'package probe\\n\\nimport \"testing\"\\n\\nfunc TestOne(t *testing.T) {}\\n'"
        " > probe_test.go"
    )
    script = "go test -race -count=1 ./... >/dev/null"
    result = run(
        [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--network",
            "none",
            "--env",
            "CGO_ENABLED=1",
            f"pcb-go-{recipe}@{image_digest}",
            "sh",
            "-c",
            f"set -eu; mkdir -p /probe; cd /probe; {manifest}; {source}; {test}; {script}",
        ],
        check=False,
    )
    return result.returncode == 0


def require_distinct(records: dict[str, dict[str, Any]]) -> None:
    """Fail the build unless the three recipes are materially different.

    Distinct *digests* are not enough on their own: an earlier Rust build produced three
    differently-tagged images with identical contents. A recipe is only real if the analyzers
    actually run in the evaluator image, are actually absent from the others, and the three digests
    differ.
    """
    problems: list[str] = []
    evaluator_tools = records["evaluator"]["tools"]
    assert isinstance(evaluator_tools, dict)
    expected_gosec = next(
        (
            component.partition(":")[2]
            for component in recipe_components()["evaluator"]
            if component.startswith("gosec:")
        ),
        None,
    )
    if not expected_gosec or evaluator_tools.get("gosec") != expected_gosec:
        problems.append(
            f"evaluator gosec identity {evaluator_tools.get('gosec')!r} does not match "
            f"the pinned module {expected_gosec!r}"
        )
    for recipe in RECIPES:
        tools = records[recipe]["tools"]
        assert isinstance(tools, dict)
        if "go-vet" in EXPECTED_TOOLS[recipe] and tools.get("go-vet") in {
            None,
            "absent",
            "unknown",
        }:
            problems.append(f"{recipe} image does not identify its bundled go vet tool")
    for tool in ("staticcheck", "gosec"):
        if evaluator_tools.get(tool) == "absent":
            problems.append(f"evaluator image cannot run {tool}")
    for recipe in ("runtime", "performance"):
        tools = records[recipe]["tools"]
        assert isinstance(tools, dict)
        for tool in ("staticcheck", "gosec"):
            if tools.get(tool) != "absent":
                problems.append(f"{recipe} image unexpectedly provides {tool}")
    # Instrumentation is a property of the *plan*, not of the image: the race detector ships inside
    # the pinned Go base, so a performance image that can run `-race` is correct, not a defect.
    # What must hold is that the measurement recipe declares no instrumentation and that only a
    # recipe declared instrumentable may host a race plan. The plan-time guard is
    # ``ImageIdentities.require_release_recipe``; this is the build-side half of the same contract.
    declared = declared_instrumentation()
    if declared["performance"] != "none":
        problems.append(
            f"the performance recipe declares {declared['performance']!r} instrumentation, so a "
            "measurement could be taken from an instrumented build"
        )
    allowed = set(instrumented_recipes())
    if allowed & {"performance"}:
        problems.append(f"instrumented_recipes must not include the measurement lane: {allowed}")
    for recipe in sorted(allowed):
        if records[recipe]["tools"].get("go-test-race") != "supported":
            problems.append(
                f"recipe {recipe!r} is declared instrumentable but cannot actually run -race"
            )
    digests = {recipe: str(record["digest"]) for recipe, record in records.items()}
    if len(set(digests.values())) != len(digests):
        problems.append(f"recipes share an image digest: {digests}")
    if problems:
        raise SystemExit("recipes are not distinct: " + "; ".join(problems))


def build(recipe: str) -> dict[str, object]:
    context = build_context(recipe)
    tag = f"pcb-go-{recipe}:v1"
    previous = run(["docker", "image", "inspect", tag, "--format", "{{.Id}}"], check=False)
    if previous.returncode == 0:
        old = previous.stdout.strip().removeprefix("sha256:")[:12]
        run(["docker", "tag", tag, f"pcb-go-{recipe}:prev-{old}"], check=False)
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
        raise SystemExit(f"go image build failed for recipe {recipe}")
    digest = run(["docker", "image", "inspect", tag, "--format", "{{.Id}}"]).stdout.strip()
    # Probe every tool for every recipe: a tool the recipe does not ship is recorded as
    # "absent" so the identity file states the difference instead of omitting it.
    tools = {name: tool_version(recipe, digest, name) for name in ALL_TOOLS}
    return {
        "recipe": recipe,
        "tag": tag,
        "reference": f"pcb-go-{recipe}@{digest}",
        "digest": digest,
        "go": tools["go-build"],
        "instrumentation": declared_instrumentation()[recipe],
        "accepts_instrumented_plans": recipe in set(instrumented_recipes()),
        "tools": tools,
        "expected_tools": list(EXPECTED_TOOLS[recipe]),
        "components": recipe_components()[recipe],
        "guest_and_rules_digest": tree_digest(context / "pcb", ("guest", "rules")),
        "recipe_digest": sha256_file(IMAGES / "recipes.yaml"),
        "dockerfile_digest": sha256_file(IMAGES / "Dockerfile"),
    }


def write_allowlist(go_images: dict[str, dict[str, object]]) -> None:
    """Refresh the Go entry's approved digests, leaving every other language untouched.

    ``assert_plan_allowed`` rejects any plan naming an image that is not in this file, so a rebuild
    that updates only ``go-v1.json`` leaves every Go plan unrunnable. The document is parsed and
    re-emitted rather than patched with a regex: an earlier regex-based edit silently deleted the
    C and C++ entries, which is the failure mode this avoids.
    """
    document = yaml.safe_load(ALLOWLIST.read_text(encoding="utf-8"))
    entry = next((p for p in document["plugins"] if p["plugin_id"] == "go"), None)
    if entry is None:
        raise SystemExit(f"no Go entry in {ALLOWLIST}; add it before building Go images")
    entry["image_digests"] = sorted(str(record["digest"]) for record in go_images.values())
    ALLOWLIST.write_text(
        yaml.safe_dump(document, sort_keys=False, default_flow_style=False), encoding="utf-8"
    )


def main() -> int:
    images = {recipe: build(recipe) for recipe in RECIPES}
    require_distinct(images)
    document = {
        "schema_version": 1,
        "kind": "go_images",
        "base_image": {"reference": BASE_IMAGE, "digest": BASE_DIGEST},
        "build": {
            "network": "none",
            "offline_install": True,
            "components_installed_at_build_time": True,
            "scored_runs_offline": True,
            "goproxy": "off",
            "gotoolchain": "local",
            "guest_interpreter": PYTHON_IMAGE,
            "note": (
                "Local development build. The toolchain ships in the pinned base image; task "
                "modules are stdlib-only and each task pins its own go.mod/go.sum, whose digest "
                "is part of the evaluator identity. staticcheck and gosec are installed at pinned "
                "module versions in the evaluator recipe only. GOTOOLCHAIN=local is what keeps a "
                "task from downloading a different compiler mid-run. A rebuild changes image "
                "digests, so task manifests must be resealed and admission re-run."
            ),
        },
        "rule_bundle_digest": tree_digest(PLUGIN, ("rules",)),
        "guest_digest": tree_digest(PLUGIN, ("guest",)),
        "images": images,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_allowlist(images)
    print(f"wrote {ALLOWLIST.relative_to(ROOT)}")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
