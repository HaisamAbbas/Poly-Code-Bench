"""Build the pinned C images and record their identities (Prompt 20, PCB-20-1).

Four recipes are built and then *probed*: every tool a recipe claims must answer, and every tool
it must not have must be gone. The probe is the point - the difference between the runtime image
and the evaluator image is only meaningful if it is checked rather than declared.

``require_distinct`` is the second half of the same gate. Three differently tagged images that
are byte-identical in the ways that matter would make every recorded identity meaningless, so the
digests have to differ and the recipe differences have to be real.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast

import yaml  # type: ignore[import-untyped,unused-ignore]
from polycodebench_core.canonical import canonical_digest
from polycodebench_core.models import Digest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "languages" / "c"
INFRA = ROOT / "infra" / "images" / "c"
RECIPES_PATH = INFRA / "recipes.yaml"
IDENTITY_FILE = ROOT / "config" / "images" / "c-v1.json"
ALLOWLIST = ROOT / "config" / "plugins" / "allowlist-v1.yaml"
STAGE = ROOT / ".cache" / "c-image-build"
ALL_TOOLS = ("clang", "clang-tidy", "cppcheck", "valgrind", "asan-runtime")
#: The binary each probe runs. `asan-runtime` is not a binary: it is a library, resolved through the
#: compiler that would link against it, so the probe has to ask clang rather than `command -v`.
PROBE_BINARY = {
    "clang": "clang",
    "clang-tidy": "clang-tidy",
    "cppcheck": "cppcheck",
    "valgrind": "valgrind",
    "asan-runtime": "clang",
}
#: A probe that must fail. Recorded as ``absent`` only when the image itself agrees.
VERSION_ARGS = {
    "clang": ["--version"],
    "clang-tidy": ["--version"],
    "cppcheck": ["--version"],
    "valgrind": ["--version"],
    # Not a tool: the sanitizer runtime is a library, so it is probed through the compiler's own
    # resolution. An answer without a path is clang saying "I do not have it".
    "asan-runtime": ["-print-file-name=libclang_rt.asan-x86_64.so"],
}
ABSENT = "absent"


def recipes() -> dict[str, Any]:
    document = cast("dict[str, Any]", yaml.safe_load(RECIPES_PATH.read_text(encoding="utf-8")))
    return cast("dict[str, Any]", document["recipes"])


def base_digest() -> str:
    return base_image_digest()


def recipes_document_base() -> Any:
    return yaml.safe_load(RECIPES_PATH.read_text(encoding="utf-8"))["base_image"]


def base_image_digest() -> str:
    """The pinned base image's digest.

    ``RepoDigests`` is used when the image has been pulled and the local image id otherwise; both
    are stable for the image the build actually ran against, which is what the record must name.
    """
    reference = recipes_document_base()
    completed = subprocess.run(
        ["docker", "image", "inspect", reference, "--format", "{{json .RepoDigests}}"],
        capture_output=True,
        text=True,
        check=True,
    )
    try:
        digests = cast("list[str]", json.loads(completed.stdout.strip() or "[]"))
    except ValueError:
        digests = []
    for entry in digests:
        if "@sha256:" in entry:
            return "sha256:" + entry.split("@sha256:")[1]
    image_id = subprocess.run(
        ["docker", "image", "inspect", reference, "--format", "{{.Id}}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return "sha256:" + image_id.removeprefix("sha256:")


def tree_digest(root: Path, subdirs: tuple[str, ...]) -> str:
    entries: dict[str, str] = {}
    for subdir in subdirs:
        base = root / subdir
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            relative = path.relative_to(root).as_posix()
            entries[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return str(canonical_digest(entries))


def build_context(recipe: str, base: str) -> Path:
    """Stage the plugin guest + rules plus the Dockerfile for one recipe.

    The clang-tidy configuration is *derived* here from the reviewed grouped YAML rather than kept
    as a second hand-maintained file: clang-tidy accepts only a flat list, and two lists would
    eventually disagree - at which point the profile would be mapping checks the tool never ran.
    """
    from polycodebench_lang_c.recipe import clang_tidy_config_text

    context = STAGE / recipe
    if context.exists():
        subprocess.run(["docker", "rm", "-f", str(context)], capture_output=True, check=False)
        _remove(context)
    (context / "pcb").mkdir(parents=True, exist_ok=True)
    for name in ("guest", "rules"):
        source = PLUGIN / "src" / "polycodebench_lang_c" / name
        target = context / "pcb" / name
        target.mkdir(parents=True, exist_ok=True)
        for path in sorted(source.rglob("*")):
            if path.is_dir() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            target_path = target / path.relative_to(source)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(path.read_bytes())
    (context / "pcb" / "rules" / ".clang-tidy").write_text(
        clang_tidy_config_text(), encoding="utf-8", newline="\n"
    )
    (context / "Dockerfile").write_bytes((INFRA / "Dockerfile").read_bytes())
    return context


def _remove(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def probe(recipe: str, tag: str, *, attempts: int = 3) -> dict[str, str]:
    """Version of every tool in the image; ``absent`` for a tool the image does not have.

    Absence is *asked for* rather than inferred: the image runs ``command -v`` first and prints a
    sentinel when the tool is missing. Tying absence to an exec failure would be wrong - Docker
    reports a missing binary and a daemon that could not start the container with almost the same
    wording, and only the first one is evidence about the image.

    The probe is also retried, because a daemon that is momentarily busy says nothing at all about
    the image. It has to be able to report "I could not look" rather than guessing.
    """
    observed: dict[str, str] = {}
    for tool in ALL_TOOLS:
        script = (
            f"command -v {PROBE_BINARY[tool]} >/dev/null 2>&1 "
            f"&& exec {PROBE_BINARY[tool]} {' '.join(VERSION_ARGS[tool])} "
            f"|| printf 'pcb-absent\\n'"
        )
        last = ""
        for attempt in range(attempts):
            completed = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--pull=never",
                    "--network",
                    "none",
                    tag,
                    "sh",
                    "-c",
                    script,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            output = (completed.stdout or "").strip().splitlines()
            if completed.returncode == 0 and output:
                first = output[0].strip()
                # A library probe is answered by clang echoing the bare name back when it cannot
                # resolve one, so a resolved path is the evidence that it is installed. Version
                # probes have no such convention.
                resolved = first != "pcb-absent" and (tool != "asan-runtime" or "/" in first)
                observed[tool] = first if resolved else ABSENT
                break
            last = f"{completed.stdout}\n{completed.stderr}".strip()
            time.sleep(1.0 * (attempt + 1))
        else:
            raise SystemExit(f"could not probe {tool!r} in {tag}: {last}")
    return observed


def build(recipe: str, *, no_cache: bool = False) -> dict[str, Any]:
    # `FROM` needs the reference *and* the digest: a digest alone is not a resolvable image name.
    base = f"{recipes_document_base()}@{base_image_digest()}"
    tag = f"pcb-c-{recipe}:v1"
    previous = subprocess.run(
        ["docker", "image", "inspect", tag, "--format", "{{.Id}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if previous.returncode == 0:
        subprocess.run(
            ["docker", "tag", tag, f"pcb-c-{recipe}:prev-{previous.stdout.strip()[:12]}"],
            capture_output=True,
            check=False,
        )
    context = STAGE / recipe
    subprocess.run(
        [
            "docker",
            "build",
            "--network",
            "default",
            "--pull=false",
            *(["--no-cache"] if no_cache else []),
            "--build-arg",
            f"BASE_IMAGE={base}",
            "--build-arg",
            f"RECIPE={recipe}",
            "-t",
            tag,
            str(context),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    digest = subprocess.run(
        ["docker", "image", "inspect", tag, "--format", "{{index .RepoDigests 0}}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return {
        "recipe": recipe,
        "tag": tag,
        "reference": digest,
        "digest": digest.split("@")[1],
        "tools": probe(recipe, tag),
    }


def list_checks(tag: str) -> set[str]:
    """Every check the pinned clang-tidy actually ships.

    ``--list-checks`` prints one indented line per check; the leading indent is what distinguishes
    an entry from the section header.
    """
    completed = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--network",
            "none",
            tag,
            "clang-tidy",
            "--checks=*",
            "--list-checks",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(f"clang-tidy --list-checks failed in {tag}: {completed.stderr[:200]}")
    return {
        line.split()[0].rstrip("*")
        for line in completed.stdout.splitlines()
        if line.startswith((" ", "\t")) and line.split()
    }


def require_checks_exist(tag: str) -> dict[str, object]:
    """Every frozen check must exist in the image, or the analyzer lane is quietly smaller.

    This is the gate that stops a renamed or removed check from turning into a *silently* reduced
    selection: a check the rules bundle names but the analyzer does not ship would be mapped by the
    profile and never fired, which looks exactly like a clean candidate.
    """
    from polycodebench_lang_c.recipe import clang_tidy_check_ids, clang_tidy_config_text

    available = list_checks(tag)
    selected = clang_tidy_check_ids()
    unknown = sorted(set(selected) - available)
    if unknown:
        raise SystemExit(
            "rules/clang-tidy.yaml names checks this clang-tidy does not ship: "
            + ", ".join(unknown[:10])
            + (" ..." if len(unknown) > 10 else "")
        )
    # The derived config must also be a config clang-tidy accepts; a syntax error there reads as
    # "no checks enabled" and would turn the whole lane into a no-op.
    probe = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--network",
            "none",
            tag,
            "sh",
            "-c",
            "clang-tidy --config-file=/opt/pcb/rules/.clang-tidy --list-checks",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0 or "no checks enabled" in probe.stderr:
        raise SystemExit(
            "the derived clang-tidy config is not usable: "
            f"{probe.stderr.strip()[:200] or 'no checks enabled'}"
        )
    enabled = sum(
        1 for line in probe.stdout.splitlines() if line.startswith((" ", "\t")) and line.split()
    )
    return {
        "selected_checks": len(selected),
        "shipped_checks": len(available),
        "enabled_checks": enabled,
        "config_digest": str(canonical_digest({"config": clang_tidy_config_text()})),
    }


def require_suppressions_agree() -> list[str]:
    """The machine-readable suppression list must match its reasoned sidecar exactly.

    cppcheck's parser accepts only ``id:pattern`` lines, so the reasons cannot live in the file the
    tool reads. Splitting them is a hazard - a suppression could be added with no reason, or given a
    reason while doing nothing - so the pairing is checked here rather than trusted.
    """
    import yaml

    rules = PLUGIN / "src" / "polycodebench_lang_c" / "rules"
    listed = {
        line.strip()
        for line in (rules / "cppcheck-suppressions.txt").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    document = yaml.safe_load(
        (rules / "cppcheck-suppression-reasons.yaml").read_text(encoding="utf-8")
    )
    reasoned = {f"{entry['id']}:{entry['pattern']}" for entry in document["suppressions"]}
    for entry in document["suppressions"]:
        if len(str(entry["reason"]).strip()) < 20:
            raise SystemExit(f"suppression {entry['id']} has no usable reason")
    if listed != reasoned:
        raise SystemExit(
            "cppcheck suppressions and their reasons disagree: "
            f"only-listed={sorted(listed - reasoned)} only-reasoned={sorted(reasoned - listed)}"
        )
    return sorted(listed)


def require_distinct(records: dict[str, dict[str, Any]]) -> None:
    """Fail the build unless the recipes really differ in the ways the plan depends on.

    A probed version is also required to look like a version. A corrupt image layer - the residue
    of a daemon that died mid-build - makes a tool print its name and then die, which would
    otherwise be recorded as an image that ships a broken toolchain.
    """
    table = recipes()
    digests = {record["digest"] for record in records.values()}
    if len(digests) != len(records):
        raise SystemExit("every C recipe must produce a distinct image digest")
    for name, record in records.items():
        spec = table[name]
        for tool in spec["tools"]:
            version = record["tools"].get(tool)
            if version == ABSENT:
                raise SystemExit(f"recipe {name} must ship {tool}")
            if len(version) < 6 or version.lower() in {"absent", "error"}:
                raise SystemExit(f"recipe {name} probed an unusable {tool}: {version!r}")
        for tool in spec["forbidden_tools"]:
            if record["tools"].get(tool) != ABSENT:
                raise SystemExit(f"recipe {name} must not ship {tool}")
        # The sanitizer runtimes' presence is probed from the image, not read from the recipe
        # name. It is the only thing that makes an instrumented link impossible in a release
        # image, so it has to be a fact about the image rather than an intention in a
        # configuration file.
        has_runtime = record["tools"].get("asan-runtime") != ABSENT
        wants_runtime = str(spec.get("sanitizer_runtime", "absent")) != "absent"
        if has_runtime != wants_runtime:
            raise SystemExit(
                f"recipe {name} must {'ship' if wants_runtime else 'not ship'} the sanitizer "
                f"runtimes, but the probe says {'present' if has_runtime else 'absent'}"
            )
    if records["instrumented"]["digest"] == records["performance"]["digest"]:
        raise SystemExit(
            "the instrumented and performance images must differ; otherwise a sanitizer build "
            "could be reported as release performance"
        )


def build_record(
    name: str, record: dict[str, Any], guest_digest: str, rules_digest: str
) -> dict[str, Any]:
    spec = recipes()[name]
    return {
        "recipe": name,
        "tag": record["tag"],
        "reference": record["reference"],
        "digest": record["digest"],
        "cc": record["tools"]["clang"],
        "c_standard": str(spec.get("c_standard", "c17")),
        "instrumentation": str(spec["instrumentation"]),
        "optimization": str(spec["optimization"]),
        "tools": record["tools"],
        "expected_tools": tuple(spec["tools"]),
        "analyzers": tuple(t for t in spec["tools"] if t in {"clang-tidy", "cppcheck", "valgrind"}),
        "components": tuple(spec["tools"]),
        "guest_and_rules_digest": str(
            canonical_digest({"guest": guest_digest, "rules": rules_digest})
        ),
        "recipe_digest": str(
            canonical_digest({key: spec[key] for key in sorted(spec) if key != "description"})
        ),
        "dockerfile_digest": Digest(
            "sha256:" + hashlib.sha256((INFRA / "Dockerfile").read_bytes()).hexdigest()
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build-c-images")
    parser.add_argument("--recipes", default=",".join(sorted(recipes())))
    parser.add_argument("--allowlist-only", action="store_true")
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="rebuild every layer from the base image; needed after a daemon incident",
    )
    args = parser.parse_args(argv)
    if args.allowlist_only:
        return _write_allowlist()
    # Stage every context first so the recorded digests cover exactly what was copied into the
    # images - including the clang-tidy config, which is derived rather than checked in.
    names = [name.strip() for name in args.recipes.split(",") if name.strip()]
    for name in names:
        build_context(name, "")
    payload = STAGE / names[0] / "pcb"
    guest_digest = tree_digest(payload, ("guest",))
    rules_digest = tree_digest(payload, ("rules",))
    records: dict[str, dict[str, Any]] = {}
    for name in names:
        print(f"building {name}...", file=sys.stderr)
        records[name] = build(name, no_cache=args.no_cache)
    require_distinct(records)
    suppressions = require_suppressions_agree()
    checks = require_checks_exist(records["evaluator"]["tag"]) if "evaluator" in records else {}
    checks["cppcheck_suppressions"] = suppressions
    images = {
        name: build_record(name, record, guest_digest, rules_digest)
        for name, record in sorted(records.items())
    }
    document = {
        "schema_version": 1,
        "kind": "c_images",
        "base_image": {
            "reference": recipes_document_base(),
            "digest": base_image_digest(),
        },
        "build": {
            # The only networked step is the apt install in the Dockerfile; every scored run and
            # every plan executes with the network disabled.
            "network": "default_at_build_only",
            "offline_install": False,
            "scored_runs_offline": True,
            "note": "packages come from the pinned base image's apt snapshot at build time",
        },
        "rule_bundle_digest": rules_digest,
        "guest_digest": guest_digest,
        # The resolved analyzer selection, so a scorecard can name the checks that actually ran
        # rather than the ones the bundle hoped for.
        "analyzer_checks": checks,
        "images": images,
    }
    IDENTITY_FILE.parent.mkdir(parents=True, exist_ok=True)
    IDENTITY_FILE.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"wrote {IDENTITY_FILE.relative_to(ROOT)}")
    return _write_allowlist()


def _write_allowlist() -> int:
    """Register the C plugin and its four image digests in the administrator allowlist.

    The entry is rewritten from the recorded identities rather than appended blindly, so re-running
    the build after a digest change keeps exactly one entry with the current digests.
    """
    import yaml as _yaml

    document = json.loads(IDENTITY_FILE.read_text(encoding="utf-8"))
    entry = {
        "plugin_id": "c",
        "entry_point": "polycodebench_lang_c.plugin:CLanguagePlugin",
        "api_version": 1,
        "plugin_version": "0.1.0",
        "image_digests": [record["digest"] for record in document["images"].values()],
    }
    existing = _yaml.safe_load(ALLOWLIST.read_text(encoding="utf-8"))
    plugins = [p for p in existing.get("plugins", []) if p.get("plugin_id") != "c"]
    plugins.append(entry)
    existing["plugins"] = sorted(plugins, key=lambda item: str(item.get("plugin_id")))
    ALLOWLIST.write_text(
        _yaml.safe_dump(existing, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
        newline="\n",
    )
    print(f"registered the c plugin in {ALLOWLIST.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
