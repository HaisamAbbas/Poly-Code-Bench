"""Build the prebuilt JavaScript/TypeScript components images (Prompt 19, PCB-19-1).

    .venv/Scripts/python.exe scripts/fetch_js_components.py   # build against the committed lock
    .venv/Scripts/python.exe scripts/fetch_js_components.py --refresh-lock

**This is the only step in the entire JavaScript/TypeScript pipeline that uses a network.**
``npm ci`` has to talk to the npm registry to resolve and download tarballs, so it happens here,
once, against the committed ``infra/images/javascript/package-lock.json``. The three components
images produced here carry a fully resolved ``node_modules``; ``scripts/build_js_images.py`` then
builds the six recipe images with ``--network none`` by copying that closure out of them, and every
scored run is offline as well. Nothing else in the pipeline - not a build, not a plan, not a guest
tool - is permitted to reach a registry.

Three components images, because the recipe distinction has to be real and a candidate must not be
able to inspect the analyzers that judge it:

    pcb-js-components-base:v1       node, npm, vitest
    pcb-js-components-typescript:v1 base + typescript (tsc); a TypeScript candidate must be able
                                    to type-check what it writes inside the image it runs in
    pcb-js-components:v1            base + typescript + eslint; the analyzer image

Re-run this only when a pinned component version or the pinned lock changes. The recorded lock
digest is checked against the committed lock on every run, so a stale recorded identity can never be
paired with a different dependency closure.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "infra" / "images" / "javascript"
CONTEXTS = ROOT / ".cache" / "js-components-build"
OUTPUT = ROOT / "config" / "images" / "js-components.json"
PACKAGE_JSON = IMAGES / "package.json"
PACKAGE_LOCK = IMAGES / "package-lock.json"
BASE_DIGEST = "sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5"
BASE_IMAGE = f"node@{BASE_DIGEST}"
COMPONENTS_TAG = "pcb-js-components:v1"
COMPONENTS_BASE_TAG = "pcb-js-components-base:v1"
COMPONENTS_TYPESCRIPT_TAG = "pcb-js-components-typescript:v1"
# ESLint alone, with no TypeScript compiler. The JavaScript evaluator image is built from this one,
# so a JavaScript candidate cannot run `tsc` inside the image it solves in - the same separation the
# Rust recipes keep between an analyzer-free runtime image and an analyzer image.
COMPONENTS_LINT_TAG = "pcb-js-components-lint:v1"
# Where `npm ci` resolves the closure inside every components image, and where its binaries land.
COMPONENTS_DIR = "/opt/pcb/components"
COMPONENTS_BIN = f"{COMPONENTS_DIR}/node_modules/.bin"
# Target name in the components Dockerfile -> the tag that image is built under.
COMPONENT_TARGETS = {
    "base": COMPONENTS_BASE_TAG,
    "typescript": COMPONENTS_TYPESCRIPT_TAG,
    "lint": COMPONENTS_LINT_TAG,
    "full": COMPONENTS_TAG,
}
REGISTRY = "https://registry.npmjs.org"

DOCKERFILE = """# Prebuilt JS/TS components images (Prompt 19, PCB-19-1). Built ONCE, with \
network, by
# scripts/fetch_js_components.py -- the only step in the JS/TS pipeline that uses one. The six real
# images are then built with --network none by copying the resolved node_modules out of these.
# Four targets, because the recipe distinction has to be real: a candidate must not be able to run
# the analyzers that judge it from inside the image it solves in.
#   base       - runtime dependencies only (vitest). No eslint, no tsc.
#   typescript - base plus the TypeScript compiler, so a TypeScript candidate can type-check its own
#                solution inside the image it runs in.
#   lint       - eslint and its closure, with the TypeScript compiler removed. The JavaScript
#                evaluator image is built from this one, so a JavaScript candidate cannot run `tsc`.
#   full       - everything. The TypeScript evaluator image, which genuinely needs both tools.
# The npm cache is deleted in every target: it is not part of the pinned closure, and leaving it
# behind would let an accidental `npm install` inside a scored run succeed offline from the cache.
ARG BASE_IMAGE
FROM ${BASE_IMAGE} AS full
WORKDIR /opt/pcb/components
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund && rm -rf /root/.npm
ARG BASE_IMAGE
FROM ${BASE_IMAGE} AS base
WORKDIR /opt/pcb/components
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund && rm -rf /root/.npm
ARG BASE_IMAGE
FROM ${BASE_IMAGE} AS typescript
WORKDIR /opt/pcb/components
COPY package.json package-lock.json ./
# Runtime dependencies plus the TypeScript compiler. A TypeScript candidate must be able to \
type-check
# its own solution inside the image it solves in, so this target installs the full closure and keeps
# the compiler while dropping eslint, which belongs only in the evaluator images.
RUN npm ci --no-audit --no-fund && rm -rf /root/.npm \
    && node_modules/.bin/tsc --version \
    && rm -rf node_modules/eslint node_modules/.bin/eslint node_modules/@eslint \
node_modules/@eslint-community node_modules/@humanwhocodes node_modules/@eslint/js \
node_modules/@humanfs node_modules/@humanwho
ARG BASE_IMAGE
FROM ${BASE_IMAGE} AS lint
WORKDIR /opt/pcb/components
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund && rm -rf /root/.npm
# eslint and the TypeScript compiler are both devDependencies of the harness manifest, so this
# target installs the one pinned closure and then removes the compiler. Removing it here rather than
# hand-listing a second copy of eslint's transitive dependencies is deliberate: there is one lock,
# and the absence of the compiler is asserted, so a future dependency change cannot quietly put
# `tsc` back into the JavaScript evaluator image.
RUN rm -rf node_modules/typescript node_modules/@typescript node_modules/.bin/tsc \
    && test ! -e node_modules/typescript \
    && node_modules/.bin/eslint --version
"""


def run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=check, encoding="utf-8")


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def recorded_lock_digest() -> str | None:
    """Digest of the lock the recorded component identities were built from, if any."""
    if not OUTPUT.is_file():
        return None
    return str(json.loads(OUTPUT.read_text(encoding="utf-8"))["lock"]["digest"])


def npm_container(*argv: str, context: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run npm inside the pinned Node image.

    The context is bind-mounted rather than copied so the lock is produced by the *same* npm that
    later installs it, and so the resolved tree is visible on the host for digesting.
    """
    args = ["docker", "run", "--rm", "--pull=never"]
    if context is not None:
        args += ["-v", f"{context}:/ctx", "-w", "/ctx"]
    args += [BASE_IMAGE, *argv]
    result = run(args, check=False)
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
    return result


def sync_manifests(context: Path) -> None:
    """Put the committed manifest and lock where the bind-mounted npm runs can see them."""
    shutil.copy2(PACKAGE_JSON, context / "package.json")
    shutil.copy2(PACKAGE_LOCK, context / "package-lock.json")


def refresh_lock(context: Path) -> None:
    """Regenerate package-lock.json from package.json with `--package-lock-only`.

    This is the one command that turns the exact versions in package.json into a full resolution;
    everything afterwards installs *only* from that lock.
    """
    for name in ("package-lock.json", "node_modules"):
        path = context / name
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    shutil.copy2(PACKAGE_JSON, context / "package.json")
    result = npm_container(
        "npm",
        "install",
        "--package-lock-only",
        "--save-exact",
        "--no-audit",
        "--no-fund",
        context=context,
    )
    if result.returncode != 0 or not (context / "package-lock.json").is_file():
        raise SystemExit(
            "npm install --package-lock-only failed (this is the one step that needs network)"
        )
    shutil.copy2(context / "package-lock.json", PACKAGE_LOCK)
    print(f"wrote {PACKAGE_LOCK.relative_to(ROOT)}")


def install_closure(context: Path) -> None:
    """Resolve and download the committed lock into a real node_modules tree."""
    sync_manifests(context)
    if (context / "node_modules").exists():
        shutil.rmtree(context / "node_modules")
    result = npm_container("npm", "ci", "--no-audit", "--no-fund", context=context)
    if result.returncode != 0 or not (context / "node_modules" / "vitest").is_dir():
        raise SystemExit("npm ci failed (this is the one step that needs network)")


def lock_packages() -> dict[str, str]:
    """``name -> version`` for every package the lock resolves, root entry included."""
    document = json.loads(PACKAGE_LOCK.read_text(encoding="utf-8"))
    packages: dict[str, str] = {}
    for location, entry in document["packages"].items():
        name = entry.get("name") or location.rsplit("node_modules/", 1)[-1]
        if location == "" or not name:
            continue
        packages[name] = str(entry["version"])
    return packages


def dependency_tree_digest(packages: dict[str, str]) -> str:
    body = "\n".join(f"{name}@{packages[name]}" for name in sorted(packages))
    return "sha256:" + hashlib.sha256(body.encode()).hexdigest()


def installed_package_count() -> int:
    """Number of package directories actually present in the resolved tree on the host."""
    return sum(1 for path in (CONTEXTS / "node_modules").rglob("package.json") if path.is_file())


def advisory_report(context: Path) -> dict[str, object]:
    """Run the registry's own advisory endpoint once and record where the answer came from.

    The guest-side audit (`rules/advisories/snapshot.json`) is evaluated offline against a recorded
    snapshot; this is the fetch-time provenance of that snapshot's source. No advisory data is
    downloaded into an image by this - it is recorded in `config/images/js-components.json` only.
    """
    result = npm_container(
        "npm",
        "audit",
        "--json",
        f"--registry={REGISTRY}",
        context=context,
    )
    if result.returncode not in (0, 1):
        # npm audit exits 1 when it finds advisories, which is a result, not a failure. Anything
        # else means the advisory source could not be reached and the provenance must not be
        # invented.
        raise SystemExit("npm audit failed; advisory provenance cannot be recorded")
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise SystemExit(f"npm audit printed no JSON report: {error}") from error
    if "vulnerabilities" not in report.get("metadata", {}):
        raise SystemExit("npm audit report carried no vulnerability metadata")
    return {
        "source": "npm audit endpoint",
        "registry": REGISTRY,
        "endpoint": "/-/npm/v1/security/audits/quick",
        "database": "npm advisory database (GitHub Advisory Database feed)",
        "vulnerabilities": report["metadata"]["vulnerabilities"],
        "report_digest": "sha256:"
        + hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest(),
    }


def component_versions(tag: str, digest: str) -> dict[str, str]:
    """Versions reported by a components image itself.

    A tool that is not installed is reported as ``absent``; the recorded identity must state the
    difference rather than omit it.
    """

    def ask(*argv: str) -> str:
        result = run(
            [
                "docker",
                "run",
                "--rm",
                "--pull=never",
                "--network",
                "none",
                f"{tag}@{digest}",
                *argv,
            ],
            check=False,
        )
        if result.returncode != 0:
            return "absent"
        text = (result.stdout + result.stderr).strip()
        return text.splitlines()[-1] if text else "unknown"

    return {
        "node": ask("node", "--version"),
        "npm": ask("npm", "--version"),
        # The components images deliberately do not put the dependency binaries on PATH: only the
        # six recipe images expose them, and only the recipe that is supposed to ship a tool does.
        # Probing by absolute path is what lets a recipe state "absent" rather than inherit PATH.
        "vitest": ask(f"{COMPONENTS_BIN}/vitest", "--version"),
        "eslint": ask(f"{COMPONENTS_BIN}/eslint", "--version"),
        "tsc": ask(f"{COMPONENTS_BIN}/tsc", "--version"),
    }


def build_components(dockerfile: Path) -> dict[str, str]:
    """Build the three components images and return ``target -> image digest``."""
    digests: dict[str, str] = {}
    for target, tag in COMPONENT_TARGETS.items():
        result = run(
            [
                "docker",
                "build",
                "--pull=false",
                "--target",
                target,
                "--build-arg",
                f"BASE_IMAGE={BASE_IMAGE}",
                "-t",
                tag,
                str(CONTEXTS),
            ],
            check=False,
        )
        if result.returncode != 0:
            sys.stderr.write(result.stdout + result.stderr)
            raise SystemExit(
                f"components image {tag} failed to build (this is the one step that needs network)"
            )
        digests[target] = run(
            ["docker", "image", "inspect", tag, "--format", "{{.Id}}"]
        ).stdout.strip()
    return digests


def main() -> int:
    refresh = "--refresh-lock" in sys.argv
    unknown = {arg for arg in sys.argv[1:] if arg != "--refresh-lock"}
    if unknown:
        raise SystemExit(f"unknown argument(s): {' '.join(sorted(unknown))}")
    CONTEXTS.mkdir(parents=True, exist_ok=True)
    recorded = recorded_lock_digest()
    if refresh or not PACKAGE_LOCK.is_file():
        refresh_lock(CONTEXTS)
    elif recorded is not None and recorded != sha256_file(PACKAGE_LOCK):
        raise SystemExit(
            f"{OUTPUT.relative_to(ROOT)} was recorded against a different package-lock.json "
            f"({recorded} recorded, {sha256_file(PACKAGE_LOCK)} on disk). Refusing to record a new "
            "component identity on top of a stale one: re-run with --refresh-lock after confirming "
            "the pinned versions in infra/images/javascript/package.json are the ones you want."
        )
    install_closure(CONTEXTS)
    advisories = advisory_report(CONTEXTS)
    dockerfile = CONTEXTS / "Dockerfile"
    dockerfile.write_text(DOCKERFILE, encoding="utf-8", newline="\n")
    digests = build_components(dockerfile)
    packages = lock_packages()
    document = {
        "schema_version": 1,
        "kind": "js_components_images",
        "note": (
            "Prebuilt component images and the ONLY step in the JS/TS pipeline that uses a "
            "network. build_js_images.py builds the six recipe images from these with --network "
            "none. The base image carries no analyzers, which is what keeps the runtime and "
            "performance recipes genuinely distinct from the evaluator recipe."
        ),
        "base_image": {"reference": BASE_IMAGE, "digest": BASE_DIGEST},
        "lock": {
            "path": PACKAGE_LOCK.relative_to(ROOT).as_posix(),
            "digest": sha256_file(PACKAGE_LOCK),
            "lockfile_version": json.loads(PACKAGE_LOCK.read_text(encoding="utf-8"))[
                "lockfileVersion"
            ],
        },
        "dependency_tree": {
            "digest": dependency_tree_digest(packages),
            "package_count": len(packages),
            "installed_package_count": installed_package_count(),
        },
        "advisory_source": advisories,
        "images": {
            COMPONENT_TARGETS[target]: {
                "target": target,
                "digest": digest,
                "tools": component_versions(COMPONENT_TARGETS[target], digest),
                "dockerfile_digest": sha256_file(dockerfile),
            }
            for target, digest in digests.items()
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}; {len(packages)} locked packages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
