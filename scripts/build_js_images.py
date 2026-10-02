"""Build the offline JavaScript + TypeScript images and record their identities (Prompt 19).

    .venv/Scripts/python.exe scripts/build_js_images.py
    .venv/Scripts/python.exe scripts/build_js_images.py --allowlist-only

Six images, two languages x three recipes. ``runtime`` (build/test), ``evaluator`` (eslint, and
tsc for TypeScript) and ``performance`` (runtime plus measurement settings baked by the Dockerfile)
are built from three pinned components images, so the recipe distinction is real rather than a
relabelled copy: a candidate solving or being timed in an image cannot run, or read, the analyzers
that will judge it.

npm needs a registry to resolve anything, so the dependency closure is resolved exactly once by
``scripts/fetch_js_components.py`` -- the only step in the JS/TS pipeline that uses a network -- and
lives in the components images. This script copies that finished closure into each image and builds
with ``--network none``, so neither these builds nor any scored run install or fetch anything. The
committed ``package-lock.json`` is the pinned closure; its digest is part of every recorded
identity.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "infra" / "images" / "javascript"
PLUGIN = ROOT / "plugins" / "languages" / "javascript" / "src" / "polycodebench_lang_javascript"
CONTEXTS = ROOT / ".cache" / "js-image-build"
COMPONENTS = ROOT / "config" / "images" / "js-components.json"
ALLOWLIST = ROOT / "config" / "plugins" / "allowlist-v1.yaml"
BASE_DIGEST = "sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5"
BASE_IMAGE = f"node@{BASE_DIGEST}"
# The sandbox provider drives every guest with `python -I -B -S`, so each JS/TS image carries the
# interpreter pinned for the Python recipes (copied with its own libraries; see the Dockerfile).
PYTHON_IMAGE = "python@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b"
LANGUAGES = ("javascript", "typescript")
RECIPES = ("runtime", "evaluator", "performance")
IMAGE_KINDS = {"javascript": "javascript_images", "typescript": "typescript_images"}
ENTRY_POINTS = {
    "javascript": "polycodebench_lang_javascript.plugin:JavaScriptLanguagePlugin",
    "typescript": "polycodebench_lang_javascript.plugin:TypeScriptLanguagePlugin",
}
COMPONENTS_BASE_TAG = "pcb-js-components-base:v1"
COMPONENTS_TYPESCRIPT_TAG = "pcb-js-components-typescript:v1"
COMPONENTS_LINT_TAG = "pcb-js-components-lint:v1"
COMPONENTS_EVALUATOR_TAG = "pcb-js-components:v1"
# (language, recipe) -> the components image it is built from. This is what makes the recipes
# genuinely distinct, and what keeps the two languages apart: a JavaScript candidate must not be
# able to run `tsc` in the image that will judge it, so the JavaScript evaluator is built from the
# lint-only components image while the TypeScript evaluator gets eslint *and* tsc.
COMPONENTS_FOR_RECIPE = {
    ("javascript", "runtime"): COMPONENTS_BASE_TAG,
    ("javascript", "performance"): COMPONENTS_BASE_TAG,
    ("javascript", "evaluator"): COMPONENTS_LINT_TAG,
    ("typescript", "runtime"): COMPONENTS_TYPESCRIPT_TAG,
    ("typescript", "performance"): COMPONENTS_TYPESCRIPT_TAG,
    ("typescript", "evaluator"): COMPONENTS_EVALUATOR_TAG,
}
# The Dockerfile carries the measurement flags; the build only names the profile it wants, so the
# values live in the image rather than in whatever the task manifest happens to say.
MEASUREMENT_STAGE = {
    "runtime": "measured-runtime",
    "evaluator": "measured-runtime",
    "performance": "measured-performance",
}
# Tools a recipe is *expected* to provide. Every recipe is probed for all of them so the recorded
# identity states plainly which tools are absent rather than silently omitting them.
EXPECTED_TOOLS = {
    ("javascript", "runtime"): ("node", "npm", "vitest"),
    ("javascript", "evaluator"): ("node", "npm", "vitest", "eslint"),
    ("javascript", "performance"): ("node", "npm", "vitest"),
    ("typescript", "runtime"): ("node", "npm", "vitest", "tsc"),
    ("typescript", "evaluator"): ("node", "npm", "vitest", "eslint", "tsc"),
    ("typescript", "performance"): ("node", "npm", "vitest", "tsc"),
}
ALL_TOOLS = ("node", "npm", "vitest", "eslint", "tsc")
TOOL_ARGV = {
    "node": ["node", "--version"],
    "npm": ["npm", "--version"],
    "vitest": ["vitest", "--version"],
    "eslint": ["eslint", "--version"],
    "tsc": ["tsc", "--version"],
}
IGNORED_DIRS = frozenset({"__pycache__", ".git", "node_modules"})


def output_file(language: str) -> Path:
    return ROOT / "config" / "images" / f"{language}-v1.json"


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


def recipe_components() -> dict[tuple[str, str], list[str]]:
    """The explicit per-(language, recipe) component list declared in recipes.yaml."""
    document = yaml.safe_load((IMAGES / "recipes.yaml").read_text(encoding="utf-8"))
    languages = document["languages"]
    return {
        (language, recipe): [str(item) for item in languages[language][recipe]]
        for language in LANGUAGES
        for recipe in RECIPES
    }


def components_record() -> dict[str, object]:
    """The recorded components identities; this script refuses to guess them."""
    if not COMPONENTS.is_file():
        raise SystemExit(
            f"{COMPONENTS.relative_to(ROOT)} is missing; run scripts/fetch_js_components.py first"
        )
    document = json.loads(COMPONENTS.read_text(encoding="utf-8"))
    for tag in (COMPONENTS_BASE_TAG, COMPONENTS_TYPESCRIPT_TAG, COMPONENTS_EVALUATOR_TAG):
        if tag not in document["images"]:
            raise SystemExit(f"{COMPONENTS.relative_to(ROOT)} records no {tag}")
    return document


def require_components_pinned(record: dict[str, object]) -> None:
    """Fail unless the local components images are exactly the ones that were recorded.

    The recorded digests are what the identity files claim the closure was built from; a tag that
    has drifted locally would make that claim false, so it is a build error rather than a warning.
    """
    for tag, entry in record["images"].items():
        local = run(["docker", "image", "inspect", tag, "--format", "{{.Id}}"], check=False)
        if local.returncode != 0:
            raise SystemExit(f"components image {tag} is not present locally; run the fetch script")
        if local.stdout.strip() != entry["digest"]:
            raise SystemExit(
                f"components image {tag} is {local.stdout.strip()} locally but "
                f"{entry['digest']} was recorded; re-run scripts/fetch_js_components.py"
            )


def build_context(language: str, recipe: str) -> Path:
    context = CONTEXTS / language / recipe
    if context.exists():
        shutil.rmtree(context)
    (context / "pcb").mkdir(parents=True)
    for sub in ("guest", "rules"):
        source = PLUGIN / sub
        if source.is_dir():
            shutil.copytree(
                source,
                context / "pcb" / sub,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
    shutil.copy(IMAGES / "Dockerfile", context / "Dockerfile")
    return context


def tool_version(tag: str, image_digest: str, tool: str) -> str:
    """Ask the built image for a tool's version, so the record comes from the real artifact.

    A tool that is not installed makes the command fail, and is recorded as ``absent`` rather than
    omitted, so the identity states the recipe difference explicitly.
    """
    result = run(
        [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--network",
            "none",
            f"{tag}@{image_digest}",
            *TOOL_ARGV[tool],
        ],
        check=False,
    )
    if result.returncode != 0:
        return "absent"
    text = (result.stdout + result.stderr).strip()
    match = re.search(r"\d+\.\d+\.\d+[\w.+-]*", text)
    return match.group(0) if match else "unknown"


def require_distinct(records: dict[tuple[str, str], dict[str, object]]) -> None:
    """Fail the build unless the six images are materially different.

    Distinct *digests* are not enough on their own: an earlier Rust build produced three
    differently-tagged images with identical contents. Each recipe is only real if the analyzers
    actually run in the evaluator image and are actually absent from the others, if tsc is present
    exactly in the TypeScript images, and if all six digests differ.
    """
    problems: list[str] = []
    for language in LANGUAGES:
        for recipe in RECIPES:
            tools = records[(language, recipe)]["tools"]
            assert isinstance(tools, dict)
            if recipe == "evaluator":
                for tool in ("eslint", "tsc"):
                    if tool not in EXPECTED_TOOLS[(language, recipe)]:
                        continue
                    if tools.get(tool) == "absent":
                        problems.append(f"{language} evaluator image cannot run {tool}")
            else:
                if tools.get("eslint") != "absent":
                    problems.append(f"{language} {recipe} image unexpectedly provides eslint")
            for tool in EXPECTED_TOOLS[(language, recipe)]:
                if tools.get(tool) == "absent":
                    problems.append(f"{language} {recipe} image is missing {tool}")
        typescript_tool = "tsc"
        for recipe in RECIPES:
            present = records[(language, recipe)]["tools"]
            assert isinstance(present, dict)
            if (language == "typescript") != (present.get(typescript_tool) != "absent"):
                problems.append(
                    f"{language} {recipe} image tsc is {present.get(typescript_tool)}, "
                    "which is the wrong language's answer"
                )
    digests = {key: str(record["digest"]) for key, record in records.items()}
    if len(set(digests.values())) != len(digests):
        problems.append(f"images share an image digest: {digests}")
    if problems:
        raise SystemExit("images are not distinct: " + "; ".join(problems))


def build(
    language: str, recipe: str, components: dict[str, object], components_tag: str
) -> dict[str, object]:
    context = build_context(language, recipe)
    tag = f"pcb-js-{language}-{recipe}:v1"
    previous = run(["docker", "image", "inspect", tag, "--format", "{{.Id}}"], check=False)
    if previous.returncode == 0:
        old = previous.stdout.strip().removeprefix("sha256:")[:12]
        run(["docker", "tag", tag, f"pcb-js-{language}-{recipe}:prev-{old}"], check=False)
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
            f"COMPONENTS_IMAGE={components_tag}",
            "--build-arg",
            f"PYTHON_IMAGE={PYTHON_IMAGE}",
            "--build-arg",
            f"MEASUREMENT_STAGE={MEASUREMENT_STAGE[recipe]}",
            "--build-arg",
            f"RECIPE={recipe}",
            "--build-arg",
            f"LANGUAGE={language}",
            "-t",
            tag,
            str(context),
        ],
        check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(f"{language} image build failed for recipe {recipe}")
    digest = run(["docker", "image", "inspect", tag, "--format", "{{.Id}}"]).stdout.strip()
    # Probe every tool for every recipe: a tool the recipe does not ship is recorded as
    # "absent" so the identity file states the difference instead of omitting it.
    tools = {name: tool_version(tag, digest, name) for name in ALL_TOOLS}
    npm = tools["npm"]
    return {
        "recipe": recipe,
        "tag": tag,
        "reference": f"{tag}@{digest}",
        "digest": digest,
        "node": tools["node"],
        "package_manager": f"npm@{npm}" if npm != "absent" else npm,
        # The pinned dependency closure of the image itself. A task's own package-lock digest rides
        # on ToolIdentity.lock_digest when a tool's result depends on the task's dependencies.
        "lock_digest": str(components["lock"]["digest"]),
        "tools": tools,
        "expected_tools": list(EXPECTED_TOOLS[(language, recipe)]),
        "components": recipe_components()[(language, recipe)],
        "guest_and_rules_digest": tree_digest(context / "pcb", ("guest", "rules")),
        "recipe_digest": sha256_file(IMAGES / "recipes.yaml"),
        "dockerfile_digest": sha256_file(IMAGES / "Dockerfile"),
    }


def document_for(
    language: str,
    images: dict[str, dict[str, object]],
    components: dict[str, object],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": IMAGE_KINDS[language],
        "base_image": {"reference": BASE_IMAGE, "digest": BASE_DIGEST},
        "build": {
            "network": "none",
            "offline_install": True,
            "components_installed_at_build_time": True,
            "scored_runs_offline": True,
            "guest_interpreter": PYTHON_IMAGE,
            "components_image": COMPONENTS_FOR_RECIPE[(language, "evaluator")],
            "components_images": sorted(
                {COMPONENTS_FOR_RECIPE[(language, recipe)] for recipe in RECIPES}
            ),
            "components_digest": sha256_file(COMPONENTS),
            "components_lock_digest": components["lock"]["digest"],
            "dependency_tree_digest": components["dependency_tree"]["digest"],
            "advisory_source": components["advisory_source"],
            "note": (
                "Local development build. The dependency closure is resolved once by "
                "scripts/fetch_js_components.py -- the only step in the JS/TS pipeline that uses a "
                "network -- against the committed "
                "infra/images/javascript/package-lock.json, and is "
                "carried into each image from a pinned components image. Nothing is installed at "
                "build time here: every image is built with --network none and npm is configured "
                "with offline=true and an unreachable registry, so an install inside a scored run "
                "fails loudly instead of downloading something the identity does not know about. "
                "The evaluator images carry eslint (and tsc for TypeScript only); the runtime "
                "and performance images carry neither, so a candidate cannot inspect the "
                "analyzers that judge it. Measurement settings live in the Dockerfile's "
                "performance profile, not "
                "in the task manifest. A rebuild changes image digests, so task manifests must be "
                "resealed and admission re-run."
            ),
        },
        "rule_bundle_digest": tree_digest(PLUGIN, ("rules",)),
        "guest_digest": tree_digest(PLUGIN, ("guest",)),
        "images": images,
    }


def render_entry(language: str, images: dict[str, dict[str, object]]) -> list[str]:
    lines = [
        f"  - plugin_id: {language}",
        f"    entry_point: {ENTRY_POINTS[language]}",
        "    api_version: 1",
        '    plugin_version: "0.1.0"',
        "    image_digests:",
    ]
    digests = sorted(str(record["digest"]) for record in images.values())
    lines += [f"      - {digest}" for digest in digests]
    return lines


def refresh_allowlist(images_by_language: dict[str, dict[str, dict[str, object]]]) -> None:
    """Replace only the javascript/typescript entries, leaving every other entry byte-identical.

    The allowlist is shared: ``scripts/build_rust_images.py`` regenerates it wholesale, and doing
    that from here would delete the other language's entries. So this splices the two JS/TS blocks
    into the existing text instead of re-emitting the file.
    """
    text = ALLOWLIST.read_text(encoding="utf-8")
    lines = text.splitlines()
    first = next(
        (index for index, line in enumerate(lines) if line.startswith("  - plugin_id: ")), None
    )
    if first is None:
        raise SystemExit(f"{ALLOWLIST.relative_to(ROOT)} has no plugin entries to update")
    header, entries, current = lines[:first], [], None
    for line in lines[first:]:
        if line.startswith("  - plugin_id: "):
            if current is not None:
                entries.append(current)
            current = [line]
        else:
            current.append(line)  # type: ignore[union-attr]
    if current is not None:
        entries.append(current)
    kept = []
    for entry in entries:
        plugin_id = entry[0].split(":", 1)[1].strip()
        if plugin_id in images_by_language:
            continue
        kept.append(entry)
    kept.extend(render_entry(language, images) for language, images in images_by_language.items())
    body = "\n".join(header + [line for entry in kept for line in entry])
    ALLOWLIST.write_text(body + "\n", "utf-8")


def main() -> int:
    if [arg for arg in sys.argv[1:] if arg != "--allowlist-only"]:
        raise SystemExit("usage: build_js_images.py [--allowlist-only]")
    if not (PLUGIN / "plugin.py").is_file():
        raise SystemExit(
            "JS/TS image/allowlist build refused: the shared executable plugin module is absent; "
            "do not publish language capability labels until both adapters are integrated"
        )
    recorded: dict[str, dict[str, dict[str, object]]] = {}
    for language in LANGUAGES:
        path = output_file(language)
        document = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        recorded[language] = document.get("images", {})
    if "--allowlist-only" in sys.argv:
        if not all(recorded.values()):
            raise SystemExit(
                "refusing to rewrite the allowlist: the recorded image identities are missing, "
                "so there is no image digest an administrator could approve"
            )
        refresh_allowlist(recorded)
        print(f"wrote {ALLOWLIST.relative_to(ROOT)}")
        return 0
    components = components_record()
    require_components_pinned(components)
    images = {
        (language, recipe): build(
            language, recipe, components, COMPONENTS_FOR_RECIPE[(language, recipe)]
        )
        for language in LANGUAGES
        for recipe in RECIPES
    }
    require_distinct(images)
    for language in LANGUAGES:
        document = document_for(
            language, {recipe: images[(language, recipe)] for recipe in RECIPES}, components
        )
        output_file(language).write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        recorded[language] = document["images"]
    refresh_allowlist(recorded)
    written = ", ".join(str(output_file(name).relative_to(ROOT)) for name in LANGUAGES)
    print(f"wrote {written}")
    print(f"wrote {ALLOWLIST.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
