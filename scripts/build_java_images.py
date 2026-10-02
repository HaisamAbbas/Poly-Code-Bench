"""Build the offline Java images and record their identities (Prompt 23, PCB-23-1).

    .venv/Scripts/python.exe scripts/build_java_images.py --check
    .venv/Scripts/python.exe scripts/fetch_java_components.py
    .venv/Scripts/python.exe scripts/build_java_images.py
    .venv/Scripts/python.exe scripts/build_java_images.py --allowlist-only

Mirrors ``scripts/build_rust_images.py``: three recipes are built with ``--network none`` from the
components images ``scripts/fetch_java_components.py`` produced, every tool is probed *from the
built image* so the recorded version is the real artifact, and the recipes are required to be
materially different before anything is written.

The frozen JVM measurement policy is recorded here as data. It is read out of the built performance
image rather than restated in this script, so the identity file cannot claim a mode the image does
not actually carry.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "infra" / "images" / "java"
PLUGIN = ROOT / "plugins" / "languages" / "java" / "src" / "polycodebench_lang_java"
CONTEXTS = ROOT / ".cache" / "java-image-build"
OUTPUT = ROOT / "config" / "images" / "java-v1.json"
COMPONENTS = ROOT / "config" / "images" / "java-components.json"
ALLOWLIST = ROOT / "config" / "plugins" / "allowlist-v1.yaml"
RECIPES = ("runtime", "evaluator", "performance")
COMPONENTS_BASE_IMAGE = "pcb-java-components-base:v1"
COMPONENTS_EVALUATOR_IMAGE = "pcb-java-components:v1"
JAVA_BASE_IMAGE = "maven@sha256:3a4ab3276a087bf276f79cae96b1af04f53731bec53fb2e651aca79e4b10211e"
JAVA_BASE_PLATFORM = "linux/amd64"
PYTHON_IMAGE = "python@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b"
# Recipe -> the components image it is built from. This is what makes the recipes genuinely
# distinct: runtime/performance come from the analyzer-free base, evaluator from the analyzer image.
COMPONENTS_FOR_RECIPE = {
    "runtime": COMPONENTS_BASE_IMAGE,
    "evaluator": COMPONENTS_EVALUATOR_IMAGE,
    "performance": COMPONENTS_BASE_IMAGE,
}
#: Tools a recipe is *expected* to provide. Every recipe is probed for all of them so the recorded
#: identity states plainly which analyzers are absent rather than silently omitting them.
EXPECTED_TOOLS = {
    "runtime": ("java", "javac", "maven", "junit", "compiler", "surefire", "dependency"),
    "evaluator": (
        "java",
        "javac",
        "maven",
        "junit",
        "compiler",
        "surefire",
        "dependency",
        "spotbugs",
        "pmd",
        "checkstyle",
    ),
    "performance": ("java", "javac", "maven", "junit", "compiler", "surefire", "dependency"),
}
#: Probed for every recipe. `absent` is a first-class answer, not a missing key.
ALL_TOOLS = (
    "java",
    "javac",
    "maven",
    "junit",
    "compiler",
    "surefire",
    "dependency",
    "spotbugs",
    "pmd",
    "checkstyle",
)
ANALYZERS = ("spotbugs", "pmd", "checkstyle")
#: Maven goals used to ask the image which plugin version it resolves offline.
PLUGIN_GOALS = {
    "compiler": "org.apache.maven.plugins:maven-compiler-plugin:3.13.0:help",
    "surefire": "org.apache.maven.plugins:maven-surefire-plugin:3.2.5:help",
    "dependency": "org.apache.maven.plugins:maven-dependency-plugin:3.6.1:help",
    "spotbugs": "com.github.spotbugs:spotbugs-maven-plugin:4.8.6.0:help",
    "pmd": "org.apache.maven.plugins:maven-pmd-plugin:3.21.2:help",
    "checkstyle": "org.apache.maven.plugins:maven-checkstyle-plugin:3.3.1:help",
}
IGNORED_DIRS = frozenset({"__pycache__", ".git", "target", ".ruff_cache"})


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parsed_version(text: str) -> str:
    """Extract and normalize a version from either Maven plugin output or `java -version`."""
    match = re.search(r"(?<![\w.])(\d+(?:\.\d+)+(?:[-+][\w.]+)?)", text)
    if match:
        return normalize(match.group(1))
    match = re.search(r"\d[\w.\-+]*", text)
    return normalize(match.group(0)) if match else "unknown"


def check_configuration() -> None:
    """Validate pinned recipes and the immutable cold/steady JVM policy without Docker."""
    recipes = yaml.safe_load((IMAGES / "recipes.yaml").read_text(encoding="utf-8"))
    if not isinstance(recipes, dict) or set(recipes) != set(RECIPES):
        raise SystemExit("Java recipes must declare runtime, evaluator and performance exactly")
    dockerfile = (IMAGES / "Dockerfile").read_text(encoding="utf-8")
    recipe_text = (IMAGES / "recipes.yaml").read_text(encoding="utf-8")
    pinned_plugins = (
        "maven-compiler-plugin:3.13.0",
        "maven-surefire-plugin:3.2.5",
        "maven-dependency-plugin:3.6.1",
        "spotbugs-maven-plugin:4.8.6.0",
        "maven-pmd-plugin:3.21.2",
        "maven-checkstyle-plugin:3.3.1",
    )
    for plugin in pinned_plugins:
        if plugin not in dockerfile + recipe_text:
            raise SystemExit(f"Java offline recipe is missing pinned analyzer/tool {plugin}")
    modes = {}
    for line in dockerfile.splitlines():
        for mode in ("COLD", "STEADY_STATE"):
            key = f"JAVA_MEASUREMENT_{mode}="
            if key in line:
                modes[mode.lower()] = line.split(key, 1)[1].split("'", 1)[0].split()
    cold, steady = modes.get("cold", []), modes.get("steady_state", [])
    if set(modes) != {"cold", "steady_state"} or "-Xint" not in cold:
        raise SystemExit("the frozen JVM cold-measurement policy is incomplete")
    if "-Xint" in steady or "-XX:ActiveProcessorCount=1" not in steady:
        raise SystemExit("the frozen JVM steady-state policy must be distinct and single-core")
    print("Java pinned recipes and fixed JVM measurement policy: PASS")


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def tree_digest(root: Path, subdirs: tuple[str, ...] = ()) -> str:
    targets = [root / sub for sub in subdirs] if subdirs else [root]
    entries = {}
    for target in targets:
        if not target.is_dir():
            continue
        for path in sorted(target.rglob("*")):
            if not path.is_file() or IGNORED_DIRS & set(path.parts):
                continue
            entries[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return "sha256:" + hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()


def run(
    args: list[str], *, check: bool = True, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, input=input_text, capture_output=True, text=True, check=check, encoding="utf-8"
    )


def recipe_components() -> dict[str, list[str]]:
    document = yaml.safe_load((IMAGES / "recipes.yaml").read_text(encoding="utf-8"))
    return {str(kind): [str(item) for item in items] for kind, items in document.items()}


def components_identity() -> dict[str, dict[str, object]]:
    if not COMPONENTS.is_file():
        raise SystemExit(
            f"{COMPONENTS.relative_to(ROOT)} is absent; run scripts/fetch_java_components.py first"
        )
    document = json.loads(COMPONENTS.read_text(encoding="utf-8"))
    if document.get("base_image") != JAVA_BASE_IMAGE:
        raise SystemExit(
            "Java components were not built from the pinned JDK/Maven base image "
            f"{JAVA_BASE_IMAGE}; run scripts/fetch_java_components.py"
        )
    components = document.get("components", {})
    missing = [tag for tag in COMPONENTS_FOR_RECIPE.values() if tag not in components]
    if missing:
        raise SystemExit(f"components images are not built: {', '.join(sorted(set(missing)))}")
    identities = {str(tag): dict(record) for tag, record in components.items()}
    for tag, record in identities.items():
        actual = run(["docker", "image", "inspect", tag, "--format", "{{.Id}}"], check=False)
        platform = run(
            ["docker", "image", "inspect", tag, "--format", "{{.Os}}/{{.Architecture}}"],
            check=False,
        )
        expected_digest = str(record.get("digest", ""))
        if actual.returncode != 0 or actual.stdout.strip() != expected_digest:
            found = actual.stdout.strip() if actual.returncode == 0 else "missing"
            raise SystemExit(
                f"component tag {tag} resolves to {found}, but the manifest records "
                f"{expected_digest}; run scripts/fetch_java_components.py"
            )
        if platform.returncode != 0 or platform.stdout.strip() != JAVA_BASE_PLATFORM:
            found_platform = platform.stdout.strip() if platform.returncode == 0 else "unknown"
            raise SystemExit(
                f"component tag {tag} platform is {found_platform}, expected {JAVA_BASE_PLATFORM}"
            )
    return identities


def build_context(recipe: str) -> Path:
    context = CONTEXTS / f"recipe-{recipe}"
    if context.exists():
        shutil.rmtree(context)
    (context / "pcb").mkdir(parents=True)
    shutil.copytree(
        PLUGIN / "guest", context / "pcb" / "guest", ignore=shutil.ignore_patterns(*IGNORED_DIRS)
    )
    shutil.copytree(IMAGES / "selfcheck", context / "selfcheck")
    if (PLUGIN / "rules").is_dir():
        shutil.copytree(
            PLUGIN / "rules",
            context / "pcb" / "rules",
            ignore=shutil.ignore_patterns(*IGNORED_DIRS),
        )
    shutil.copy(IMAGES / "Dockerfile", context / "Dockerfile")
    return context


def tool_version(recipe: str, image_digest: str, tool: str) -> str:
    """Ask the built image for a tool's version, so the record comes from the real artifact.

    A probe that fails is reported as ``absent``. That distinction is the whole point: an evaluator
    image that cannot resolve SpotBugs offline and a runtime image that deliberately does not carry
    it are both "absent", and only the recorded expected_tools says which is which.
    """
    argv: tuple[str, ...]
    if tool in PLUGIN_GOALS:
        # Not `-q`: quiet mode suppresses the banner that names the plugin version, which is the
        # very thing this probe reads, so every analyzer version would record as "unknown".
        argv = (
            "mvn",
            "-o",
            "-B",
            "--no-transfer-progress",
            PLUGIN_GOALS[tool],
        )
    elif tool == "junit":
        # `ls` of the version directory prints a bare version, not an artifact filename.
        argv = ("sh", "-c", "ls /opt/pcb/m2/repository/org/junit/jupiter/junit-jupiter")
    elif tool == "java":
        argv = ("java", "-version")
    elif tool == "javac":
        argv = ("javac", "-version")
    else:
        argv = ("mvn", "--version")
    result = run(
        [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--network",
            "none",
            f"pcb-java-{recipe}@{image_digest}",
            *argv,
        ],
        check=False,
    )
    if result.returncode != 0:
        return "absent"
    text = (result.stdout + result.stderr).strip()
    if tool == "junit":
        match = re.search(r"junit-jupiter-([\w.\-]+)", text) or re.search(
            r"\b(\d[\w.\-+]*)\s*$", text
        )
        return normalize(match.group(1)) if match else "unknown"
    return parsed_version(text)


def measurement_modes(image_digest: str) -> dict[str, list[str]]:
    """The frozen flag sets, read out of the built performance image.

    Read from the artifact rather than restated here: an identity file that claimed a mode the
    image did not carry would make the DoD unverifiable, which is the failure this prevents.
    """
    result = run(
        [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--network",
            "none",
            "--entrypoint",
            "sh",
            f"pcb-java-performance@{image_digest}",
            "-c",
            "cat /opt/pcb/maven/jvm-measurement",
        ],
        check=False,
    )
    modes: dict[str, list[str]] = {}
    for line in result.stdout.splitlines():
        if not line.startswith("JAVA_MEASUREMENT_") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        mode = normalize(name.removeprefix("JAVA_MEASUREMENT_"))
        modes[mode] = value.split()
    return modes


def require_distinct(records: dict[str, dict[str, object]]) -> None:
    """Fail the build unless the three recipes are materially different.

    Distinct *digests* are not enough on their own: an earlier build produced three differently
    tagged images with identical contents. A recipe is only real if the analyzers actually run in
    the evaluator image, are actually absent from the others, and the three digests differ.
    """
    problems: list[str] = []
    evaluator_tools = records["evaluator"]["tools"]
    assert isinstance(evaluator_tools, dict)
    for tool in ANALYZERS:
        if evaluator_tools.get(tool) == "absent":
            problems.append(f"evaluator image cannot run {tool}")
    for recipe in ("runtime", "performance"):
        tools = records[recipe]["tools"]
        assert isinstance(tools, dict)
        for tool in ANALYZERS:
            if tools.get(tool) != "absent":
                problems.append(f"{recipe} image unexpectedly provides {tool}")
    digests = {recipe: str(record["digest"]) for recipe, record in records.items()}
    if len(set(digests.values())) != len(digests):
        problems.append(f"recipes share an image digest: {digests}")
    modes = records["performance"].get("jvm_measurement")
    if not isinstance(modes, dict) or not {"cold", "steady-state"} <= set(modes):
        problems.append(f"performance image does not carry both frozen modes: {modes}")
    else:
        for recipe in ("runtime", "evaluator"):
            if records[recipe].get("jvm_measurement"):
                problems.append(f"{recipe} image must not carry a measurement policy")
    if problems:
        raise SystemExit("recipes are not distinct: " + "; ".join(problems))


def build(recipe: str) -> dict[str, object]:
    context = build_context(recipe)
    tag = f"pcb-java-{recipe}:v1"
    previous = run(["docker", "image", "inspect", tag, "--format", "{{.Id}}"], check=False)
    if previous.returncode == 0:
        old = previous.stdout.strip().removeprefix("sha256:")[:12]
        run(["docker", "tag", tag, f"pcb-java-{recipe}:prev-{old}"], check=False)
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
        sys.stderr.write(result.stdout[-6000:])
        sys.stderr.write(result.stderr[-2000:])
        raise SystemExit(f"java image build failed for recipe {recipe}")
    digest = run(["docker", "image", "inspect", tag, "--format", "{{.Id}}"]).stdout.strip()
    tools = {name: tool_version(recipe, digest, name) for name in ALL_TOOLS}
    record: dict[str, object] = {
        "recipe": recipe,
        "tag": tag,
        "reference": f"pcb-java-{recipe}@{digest}",
        "digest": digest,
        "java": tools["java"],
        "maven": tools["maven"],
        "tools": tools,
        "expected_tools": list(EXPECTED_TOOLS[recipe]),
        "components": recipe_components()[recipe],
        "guest_and_rules_digest": tree_digest(context / "pcb", ("guest", "rules")),
        "recipe_digest": sha256_file(IMAGES / "recipes.yaml"),
        "dockerfile_digest": sha256_file(IMAGES / "Dockerfile"),
    }
    # The frozen modes are recorded once, at `build.jvm_measurement`, which is where the strict
    # model reads them. They are not a per-image field: a mode belongs to the performance image and
    # the other two must not carry one, and duplicating it per image would assert the opposite.
    record["jvm_measurement"] = measurement_modes(digest) if recipe == "performance" else {}
    return record


def known_digests() -> dict[str, set[str]]:
    """Every other language's recorded digests, keyed by plugin id.

    The recorded image files are the authority. Rebuilding entries from them rather than copying
    whatever the previous allowlist held is what stops a stale entry (notably one with an empty
    digest list) from being preserved indefinitely by whichever language rebuilt last.
    """
    found: dict[str, set[str]] = {}
    for path in sorted((ROOT / "config" / "images").glob("*-v1.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        # `normalize` turns the recorded `<language>_images` underscore into a hyphen, so the
        # suffix to strip after normalizing is `-images`. Stripping `_images` matched nothing and
        # silently emitted empty digest sets.
        language = normalize(str(document.get("kind", ""))).removesuffix("-images")
        images = document.get("images")
        if language and isinstance(images, dict) and images:
            found[language] = {str(record["digest"]) for record in images.values()}
    return found


def plugin_entry_points() -> dict[str, str]:
    """Every language plugin's declared entry point, read from its own distribution.

    Read from ``pyproject.toml`` rather than from a table written in one build script. A table
    here would make the allowlist a race: whichever language rebuilt last would emit only the
    languages it knew about and silently unregister the rest. The declarations already exist and
    are the authority, so this only reads them.
    """
    found: dict[str, str] = {}
    for pyproject in sorted((ROOT / "plugins" / "languages").glob("*/pyproject.toml")):
        for line in pyproject.read_text(encoding="utf-8").splitlines():
            match = re.match(r'\s*([a-z][a-z0-9]*)\s*=\s*"([\w.]+:[A-Za-z]+)"', line)
            if match:
                found[match.group(1)] = match.group(2)
    return found


def write_allowlist(java_images: dict[str, dict[str, object]]) -> None:
    """Rewrite the allowlist from the recorded image files.

    Every entry is rebuilt from `config/images/<language>-v1.json` rather than merged from whatever
    the previous file held. Preserving prior entries verbatim is what let an entry with an empty
    digest list survive indefinitely: whichever language rebuilt last copied the broken entry
    forward, and the plugin stayed registered with no usable image digest. A language whose image
    file exists is therefore always re-emitted, and one without recorded digests is refused rather
    than written as a silently empty list.
    """
    java = {str(record["digest"]) for record in java_images.values()}
    digests_by_language = known_digests()
    digests_by_language["java"] = java
    # Every registered language's entry point is read from its own distribution metadata rather
    # than from a list written here. A hard-coded map is a race between language builds: whichever
    # script ran last rewrote the allowlist and dropped every language it did not know about.
    entry_points = plugin_entry_points()
    missing = sorted(set(entry_points) - set(digests_by_language))
    if missing:
        raise SystemExit(
            "refusing to write an allowlist with no recorded image digests for: "
            + ", ".join(missing)
            + "; build those images first"
        )
    # Sort for a stable, reviewable file; a reordering alone must never look like a change of
    # registration.
    lines = [
        "schema_version: 1",
        "kind: language_plugin_allowlist",
        "plugins:",
    ]
    for plugin_id in sorted(entry_points):
        lines.append(f"  - plugin_id: {plugin_id}")
        lines.append(f"    entry_point: {entry_points[plugin_id]}")
        lines.append("    api_version: 1")
        lines.append('    plugin_version: "0.1.0"')
        lines.append("    image_digests:")
        lines.extend(f"      - {digest}" for digest in sorted(digests_by_language[plugin_id]))
    ALLOWLIST.parent.mkdir(parents=True, exist_ok=True)
    ALLOWLIST.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    if "--check" in sys.argv[1:]:
        check_configuration()
        return 0
    if "--allowlist-only" in sys.argv:
        write_allowlist(json.loads(OUTPUT.read_text(encoding="utf-8"))["images"])
        print(f"wrote {ALLOWLIST.relative_to(ROOT)}")
        return 0
    components = components_identity()
    images = {recipe: build(recipe) for recipe in RECIPES}
    require_distinct(images)
    runtime = images["runtime"]
    performance = images["performance"]
    measured_modes = performance.get("jvm_measurement")
    if not isinstance(measured_modes, dict):
        raise SystemExit("the performance image did not report its frozen JVM measurement modes")
    jvm_measurement: dict[str, list[str]] = {}
    for mode, flags in measured_modes.items():
        if not isinstance(mode, str) or not isinstance(flags, (tuple, list)):
            raise SystemExit("the performance image reported malformed JVM measurement flags")
        jvm_measurement[mode] = [str(flag) for flag in flags]
    document = {
        "schema_version": 1,
        "kind": "java_images",
        "base_image": {
            "reference": json.loads(COMPONENTS.read_text(encoding="utf-8"))["base_image"],
            "digest": str(components[COMPONENTS_BASE_IMAGE]["digest"]),
        },
        "build": {
            "network": "none",
            "offline_install": True,
            "components_installed_at_build_time": True,
            "scored_runs_offline": True,
            "java": runtime["java"],
            "maven": runtime["maven"],
            "offline_repository": "/opt/pcb/m2/repository",
            "offline_repository_digest": str(
                components[COMPONENTS_EVALUATOR_IMAGE]["repository_digest"]
            ),
            "guest_interpreter": PYTHON_IMAGE,
            # The frozen modes, as data. A reviewer reads this and sees the mode -> flags mapping
            # was fixed before any candidate existed; nothing may substitute one mode for another
            # based on what a run observed.
            "jvm_measurement": dict(sorted(jvm_measurement.items())),
            "note": (
                "Local development build. The JDK and Maven ship in pinned components images whose "
                "offline repository was seeded once by scripts/fetch_java_components.py, the only "
                "networked step. Every recipe is built with --network none; every plan runs Maven "
                "with --offline; a task whose POM needs an artifact the image lacks fails loudly "
                "instead of resolving something other than the frozen deps.lock.json. Each "
                "task freezes its own resolution at admission and that digest is part of the "
                "evaluator identity. The two measurement modes are constants of the performance "
                "image. A rebuild changes image digests, so task manifests must be resealed and "
                "admission re-run."
            ),
        },
        "rule_bundle_digest": tree_digest(PLUGIN, ("rules",)),
        "guest_digest": tree_digest(PLUGIN, ("guest",)),
        # The per-image `jvm_measurement` is how `require_distinct` proved that only the
        # performance image carries a policy; the strict identity model has no such field, so the
        # evidence is dropped here now that it has been checked.
        "images": {
            recipe: {key: value for key, value in record.items() if key != "jvm_measurement"}
            for recipe, record in images.items()
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_allowlist(images)
    print(f"wrote {OUTPUT.relative_to(ROOT)} and {ALLOWLIST.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
