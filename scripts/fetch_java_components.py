"""Build the offline Java components images (Prompt 23, PCB-23-1).

This is the ONLY step in the whole Java pipeline that uses a network. It is the Java analogue of
``scripts/fetch_rust_components.py``: Maven resolves a plugin graph from Maven Central once, and the
resulting repository is baked into two *components* images that every recipe is then built from
with ``--network none``.

    .venv/Scripts/python.exe scripts/fetch_java_components.py

Why a components image rather than a vendored directory, like the Python wheelhouse: a Maven
plugin is not a single jar. ``spotbugs:check`` drags in its own engine, its XML writer, ASM and a
dependency graph whose exact shape is a function of the plugin version. Pinning that graph means
resolving it once against pinned versions, and the only way to carry it into an image without
re-resolving at build time is to carry the repository itself.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTEXTS = ROOT / ".cache" / "java-image-build"
SEED = "java-seed"
OUT = ROOT / "config" / "images" / "java-components.json"

BASE_IMAGE = "maven@sha256:3a4ab3276a087bf276f79cae96b1af04f53731bec53fb2e651aca79e4b10211e"
BASE_PLATFORM = "linux/amd64"
COMPONENTS_BASE_TAG = "pcb-java-components-base:v1"
COMPONENTS_EVALUATOR_TAG = "pcb-java-components:v1"

#: Every Maven plugin and dependency the recipes pin. The runtime repository gets only the build and
#: test plugins; the analyzer plugins are what makes the evaluator recipe a different image.
BUILD_PLUGINS: tuple[tuple[str, str, str], ...] = (
    ("org.apache.maven.plugins", "maven-compiler-plugin", "3.13.0"),
    ("org.apache.maven.plugins", "maven-surefire-plugin", "3.2.5"),
    ("org.apache.maven.plugins", "maven-dependency-plugin", "3.6.1"),
    ("org.apache.maven.plugins", "maven-resources-plugin", "3.3.1"),
    ("org.apache.maven.plugins", "maven-jar-plugin", "3.4.1"),
    ("org.apache.maven.plugins", "maven-install-plugin", "3.1.2"),
    ("org.apache.maven.plugins", "maven-help-plugin", "3.4.0"),
)
ANALYZER_PLUGINS: tuple[tuple[str, str, str], ...] = (
    ("com.github.spotbugs", "spotbugs-maven-plugin", "4.8.6.0"),
    ("org.apache.maven.plugins", "maven-pmd-plugin", "3.21.2"),
    ("org.apache.maven.plugins", "maven-checkstyle-plugin", "3.3.1"),
)
DEPENDENCIES: tuple[tuple[str, str, str], ...] = (
    ("org.junit.jupiter", "junit-jupiter", "5.10.2"),
    # The resources plugin's optional transitive edge was represented by a POM but its jar was
    # absent from the minimal build-plugin seed; Maven then fails while creating the plugin realm.
    ("org.apache.maven.shared", "maven-filtering", "3.3.1"),
    # PMD's report goal resolves its Maven skin from the project repository at execution time.
    # `dependency:go-offline` does not include reporting skins in the plugin graph, so seed the
    # exact artifact explicitly or offline evaluator images fail before PMD can run.
    ("org.apache.maven.skins", "maven-default-skin", "1.3"),
)

# The components image copies a resolved repository in and does nothing else: no Maven run, no
# network. That is deliberate - anything resolved here would be resolved again by a recipe.
DOCKERFILE = f"""FROM {BASE_IMAGE}
RUN set -eu; \\
    mkdir -p /opt/pcb/m2/repository; \\
    printf '%s\\n' \\
        '<settings>' \\
        '  <localRepository>/opt/pcb/m2/repository</localRepository>' \\
        '  <interactiveMode>false</interactiveMode>' \\
        '  <usePluginRegistry>false</usePluginRegistry>' \\
        '</settings>' > /opt/pcb/maven-settings.xml
COPY repository /opt/pcb/m2/repository
LABEL org.polycodebench.components="java"
"""


def run(
    args: list[str], *, check: bool = True, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, input=input_text, capture_output=True, text=True, check=check, encoding="utf-8"
    )


def remove_tree(path: Path) -> None:
    """Delete ``path`` if present, tolerating the file locking this repository hits on Windows.

    A Maven container writes into a bind-mounted repository and Windows can still hold a handle for
    a moment after the container exits, so ``rmtree`` may fail on a not-empty directory. The build
    is about to recreate the whole tree, so a retry after the handle is released is the right
    behaviour; failing the whole seed on it would not be.
    """
    if not path.exists():
        return
    for attempt in range(5):
        try:
            shutil.rmtree(path)
            return
        except OSError:
            if attempt == 4:
                raise
            time.sleep(1.0 + attempt)


def pom(plugins: tuple[tuple[str, str, str], ...]) -> str:
    rows = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<project xmlns="http://maven.apache.org/POM/4.0.0">',
        "  <modelVersion>4.0.0</modelVersion>",
        "  <groupId>pcb</groupId>",
        "  <artifactId>java-seed</artifactId>",
        "  <version>1.0.0</version>",
        "  <packaging>jar</packaging>",
        "  <properties>",
        "    <maven.compiler.source>21</maven.compiler.source>",
        "    <maven.compiler.target>21</maven.compiler.target>",
        "    <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>",
        "  </properties>",
        "  <dependencies>",
    ]
    for group, artifact, version in DEPENDENCIES:
        rows.append(
            f"    <dependency><groupId>{group}</groupId><artifactId>{artifact}</artifactId>"
            f"<version>{version}</version><scope>test</scope></dependency>"
        )
    rows.append("  </dependencies>")
    rows.append("  <build><plugins>")
    for group, artifact, version in plugins:
        rows.append(
            f"    <plugin><groupId>{group}</groupId><artifactId>{artifact}</artifactId>"
            f"<version>{version}</version>"
        )
        if artifact == "maven-pmd-plugin":
            # PMD 6.55 (pinned by maven-pmd-plugin 3.21.2) supports Java syntax through 17.
            # The seed project compiles with JDK 21, so freeze the analyzer parser's supported
            # target explicitly instead of inheriting maven.compiler.target=21.
            rows.append(
                "      <configuration><targetJdk>17</targetJdk><linkXRef>false</linkXRef>"
                "</configuration>"
            )
        if artifact == "maven-checkstyle-plugin":
            # maven-checkstyle-plugin 3.3.1 calls `ImmutableList.copyOf(Object[])`, which Guava
            # removed. Several Guava versions end up in the seeded repository (SpotBugs and the
            # reporting stack each pull their own) and the plugin realm resolves the newest, so
            # without an explicit pin the goal dies with NoSuchMethodError before reading a file.
            # 31.0.1-jre is the newest version that both still provides that call and whose own
            # transitives are present; 33.x additionally needs checker-qual and error_prone, which
            # this seed does not fetch, so pinning it would fail offline at resolution instead.
            rows.append(
                "      <dependencies><dependency><groupId>com.google.guava</groupId>"
                "<artifactId>guava</artifactId><version>31.0.1-jre</version>"
                "</dependency></dependencies>"
            )
    rows.append("  </plugins></build>")
    rows.append("</project>")
    return "\n".join(rows) + "\n"


def tree_digest(root: Path) -> str:
    entries = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            entries[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return "sha256:" + hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()


def seed_goals(plugins: tuple[tuple[str, str, str], ...]) -> list[str]:
    """The goals to *execute* against Maven Central, for exactly this recipe's plugin set.

    Executing the goals is what resolves what merely resolving cannot: surefire picks its JUnit
    Platform provider at run time, after `dependency:go-offline` has already finished, and
    PMD/Checkstyle pull a Doxia reporting skin (`maven-default-skin`) that is not a declared
    dependency of either plugin. A repository seeded by resolution alone therefore compiles tests it
    cannot run and generates reports it cannot render.

    `test` already compiles the module, so it is the only compile step listed; naming `compile` as
    well re-enters the compiler plugin with a mojo classpath that has not finished resolving.

    The flags must match what the scored plans pass, and `targetJdk` is not `-Dpmd.targetJdk`: for a
    `default-cli` invocation that name is unbound, so PMD falls back to the running JVM (21) and
    fails with "Unsupported targetJdk value '21'". The unprefixed name is the one this goal reads.
    """
    goals = ["test"]
    available = {artifact for _group, artifact, _version in plugins}
    flags = (
        (
            "spotbugs-maven-plugin",
            "com.github.spotbugs:spotbugs-maven-plugin:4.8.6.0:check",
            (),
        ),
        (
            "maven-pmd-plugin",
            "org.apache.maven.plugins:maven-pmd-plugin:3.21.2:pmd",
            ("-DtargetJdk=17", "-Dpmd.linkXRef=false"),
        ),
        (
            "maven-checkstyle-plugin",
            "org.apache.maven.plugins:maven-checkstyle-plugin:3.3.1:checkstyle",
            (),
        ),
    )
    for artifact, goal, options in flags:
        # Only the evaluator seed declares analyzers. Asking the base seed for `spotbugs:check`
        # would resolve a plugin prefix from the network, which is what the runtime recipe must be
        # unable to do.
        if artifact in available:
            goals.extend((goal, *options))
    # Exercise the exact dependency plugin for its complete runtime graph without asking
    # `go-offline` to resolve unrelated optional plugin metadata and legacy BOMs.
    return [
        *goals,
        "org.apache.maven.plugins:maven-dependency-plugin:3.6.1:help",
        "-q",
    ]


def seed_repository(plugins: tuple[tuple[str, str, str], ...], name: str) -> Path:
    """Resolve ``plugins`` against Maven Central inside a container and keep the repository."""
    context = CONTEXTS / name
    remove_tree(context)
    project = context / SEED
    (project / "src" / "main" / "java" / "demo").mkdir(parents=True)
    (project / "src" / "test" / "java" / "demo").mkdir(parents=True)
    (project / "pom.xml").write_text(pom(plugins), encoding="utf-8")
    (project / "src" / "main" / "java" / "demo" / "Seed.java").write_text(
        "package demo;\n\npublic final class Seed {\n"
        "    private Seed() {\n    }\n\n"
        "    public static int one() {\n        return 1;\n    }\n}\n",
        encoding="utf-8",
    )
    (project / "src" / "test" / "java" / "demo" / "SeedTest.java").write_text(
        "package demo;\n\nimport static org.junit.jupiter.api.Assertions.assertEquals;\n\n"
        "import org.junit.jupiter.api.Test;\n\n"
        "class SeedTest {\n    @Test\n    void one() {\n"
        "        assertEquals(1, Seed.one());\n    }\n}\n",
        encoding="utf-8",
    )
    repository = context / "repository"
    remove_tree(repository)
    repository.mkdir(parents=True)
    # The seed runs with the network, because resolving a pinned plugin graph is the one thing this
    # pipeline is allowed to fetch. Everything downstream is `--network none`.
    #
    # The goals are *executed*, not merely resolved. `test-compile dependency:go-offline` is not
    # enough: surefire selects its JUnit Platform provider at runtime, after go-offline has already
    # finished, and PMD/Checkstyle pull a Doxia reporting skin (`maven-default-skin`) that is not a
    # declared dependency of either plugin. A repository seeded by resolution alone therefore
    # compiles tests it cannot run and generates reports it cannot render.
    #
    # Resolution happens in the container's own filesystem and is then copied out, rather than
    # being written straight into a bind mount. A long resolve spread over a bind mount fails
    # intermittently on Windows with FileNotFoundException on Maven's small bookkeeping files, so
    # a partially downloaded artifact is lost; the container filesystem is not bind-mounted, the
    # whole graph is written once, and it is copied out by a single `docker cp`.
    goals = seed_goals(plugins)
    container = f"pcb-java-seed-{name}"
    result = run(
        [
            "docker",
            "run",
            "--name",
            container,
            "--platform",
            BASE_PLATFORM,
            "-v",
            f"{project.resolve().as_posix()}:/seed:ro",
            BASE_IMAGE,
            "sh",
            "-c",
            "set -eu; cp -r /seed /work; cd /work; "
            "mvn -B --no-transfer-progress -Dmaven.repo.local=/repository " + " ".join(goals),
        ],
        check=False,
    )
    if result.returncode != 0:
        run(["docker", "rm", "-f", f"pcb-java-seed-{name}"], check=False)
        sys.stderr.write(result.stdout[-6000:])
        sys.stderr.write(result.stderr[-2000:])
        raise SystemExit(f"Maven seeding failed for {name}")
    # `docker cp container:/repository <dir>` copies the directory *into* `<dir>`, which would
    # nest it as `repository/repository`. Copy the contents instead, then assert the shape, so a
    # silently wrong layout cannot pass as a seeded repository.
    copy_out = run(
        ["docker", "cp", f"{container}:/repository/.", str(repository)],
        check=False,
    )
    run(["docker", "rm", "-f", container], check=False)

    if copy_out.returncode != 0:
        sys.stderr.write(copy_out.stdout[-2000:])
        sys.stderr.write(copy_out.stderr[-2000:])
        raise SystemExit(f"could not copy the seeded repository out of the container for {name}")
    if (repository / "repository").is_dir():
        raise SystemExit(f"seeded repository for {name} was copied one level too deep")
    if not (repository / "org" / "junit").is_dir():
        raise SystemExit(f"seeded repository for {name} is missing JUnit; the seed did not resolve")
    return repository


def commit_image(repository: Path, tag: str) -> dict[str, object]:
    """Bake a seeded repository into a components image with no network and no Maven run."""
    context = CONTEXTS / tag.replace(":", "-").replace("/", "-")
    remove_tree(context)
    context.mkdir(parents=True)
    shutil.copytree(repository, context / "repository")
    result = run(
        [
            "docker",
            "build",
            "--network",
            "none",
            "--platform",
            BASE_PLATFORM,
            "--pull=false",
            "-t",
            tag,
            str(context),
            "-f",
            "-",
        ],
        check=False,
        input_text=DOCKERFILE,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout[-4000:])
        sys.stderr.write(result.stderr[-2000:])
        raise SystemExit(f"components image build failed for {tag}")
    digest = run(["docker", "image", "inspect", tag, "--format", "{{.Id}}"]).stdout.strip()
    return {"tag": tag, "digest": digest, "repository_digest": tree_digest(repository)}


def cached_component(tag: str, recipe: str) -> dict[str, object] | None:
    """Reuse a component only when both its immutable image and repository tree still match."""
    if not OUT.is_file():
        return None
    document = json.loads(OUT.read_text(encoding="utf-8"))
    if document.get("base_image") != BASE_IMAGE:
        return None
    components = document.get("components", {})
    record = components.get(tag) if isinstance(components, dict) else None
    repository = CONTEXTS / recipe / "repository"
    if not isinstance(record, dict) or not repository.is_dir():
        return None
    actual = run(["docker", "image", "inspect", tag, "--format", "{{.Id}}"], check=False)
    platform = run(
        ["docker", "image", "inspect", tag, "--format", "{{.Os}}/{{.Architecture}}"],
        check=False,
    )
    if (
        actual.returncode != 0
        or actual.stdout.strip() != record.get("digest")
        or platform.returncode != 0
        or platform.stdout.strip() != BASE_PLATFORM
        or tree_digest(repository) != record.get("repository_digest")
    ):
        return None
    return dict(record)


def main() -> int:
    CONTEXTS.mkdir(parents=True, exist_ok=True)
    base_record = cached_component(COMPONENTS_BASE_TAG, "base")
    if base_record is None:
        print("seeding the build/test plugin repository (the only networked step)...")
        base = seed_repository(BUILD_PLUGINS, "base")
        base_record = commit_image(base, COMPONENTS_BASE_TAG)
    else:
        print(f"reusing verified {COMPONENTS_BASE_TAG}")
    print(f"  {COMPONENTS_BASE_TAG} {base_record['digest']}")
    full_record = cached_component(COMPONENTS_EVALUATOR_TAG, "evaluator")
    if full_record is None:
        print("seeding the analyzer plugin repository...")
        full = seed_repository((*BUILD_PLUGINS, *ANALYZER_PLUGINS), "evaluator")
        full_record = commit_image(full, COMPONENTS_EVALUATOR_TAG)
    else:
        print(f"reusing verified {COMPONENTS_EVALUATOR_TAG}")
    print(f"  {COMPONENTS_EVALUATOR_TAG} {full_record['digest']}")
    document = {
        "schema_version": 1,
        "kind": "java_components",
        "base_image": BASE_IMAGE,
        "components": {
            str(base_record["tag"]): base_record,
            str(full_record["tag"]): full_record,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
