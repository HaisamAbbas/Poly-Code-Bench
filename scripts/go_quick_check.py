"""Authoring loop for Go task packages: stage one variant and run it in the pinned image.

    python scripts/go_quick_check.py PACKAGE --variant reference
    python scripts/go_quick_check.py PACKAGE --variant quality-defective --check staticcheck,context

This is an *authoring* convenience, not evidence: it runs one command in the pinned image with no
network and prints what the tool said. Nothing it produces is an admission report, a conformance
case or a score. Use ``scripts/go_task_tool.py admit`` for anything that is claimed later.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "languages" / "go" / "src" / "polycodebench_lang_go"
sys.path.insert(0, str(PLUGIN))

from polycodebench_lang_go import GoLanguagePlugin  # noqa: E402
from polycodebench_lang_go import plans as go_plans  # noqa: E402

RUNTIME = "runtime"
EVALUATOR = "evaluator"
TOOLS = ("build", "test", "vet", "staticcheck", "gosec", "gofmt", "context", "race", "dependency")


def argv_for(tool: str, recipe: str, digest: str) -> list[str]:
    """The command the quick check runs, mirroring the plan each tool belongs to."""
    if tool == "build":
        return ["go", "build", "./..."]
    if tool == "test":
        return ["go", "test", "-count=1", "./..."]
    if tool == "vet":
        return ["go", "vet", "./..."]
    if tool == "staticcheck":
        return ["staticcheck", "-f", "text", "./..."]
    if tool == "gosec":
        return ["gosec", "-quiet", "-fmt=text", "./..."]
    if tool == "gofmt":
        return ["gofmt", "-l", "-e", "."]
    if tool == "context":
        return [
            "python",
            "-B",
            "/opt/pcb/guest/pcb_go_scan.py",
            "--root",
            "/workspace",
            "--output",
            "out/context.json",
            "/workspace/work/topwords/topwords.go",
        ]
    if tool == "race":
        return ["go", "test", "-race", "-count=1", "./..."]
    return [
        sys.executable if os.name != "nt" else "python",
        "-B",
        "/opt/pcb/guest/pcb_go_mod_audit.py",
        "--module",
        "work/go.mod",
        "--sum",
        "work/go.sum",
        "--advisories",
        "/opt/pcb/rules/advisories/snapshot.json",
        "--output",
        "out/dependency.json",
    ]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="go_quick_check")
    parser.add_argument("package", type=Path)
    parser.add_argument("--variant", default="reference")
    parser.add_argument("--check", default="build,test", help=f"comma separated from {TOOLS}")
    args = parser.parse_args(argv)
    plugin = GoLanguagePlugin()
    manifest = yaml.safe_load((args.package / "manifest.yaml").read_text(encoding="utf-8"))
    files = {
        path.relative_to(args.package).as_posix(): path.read_bytes()
        for area in ("visible", "hidden", "admission")
        for path in sorted((args.package / area).rglob("*"))
        if path.is_file()
    }
    fixture = next(
        item
        for item in manifest["fixtures"]
        if str(item["name"]) == args.variant or item["variant"] == args.variant
    )
    solution = str(fixture["solution_path"])
    allowed = list(manifest["output_contract"]["allowed_paths"])
    workspace = Path(tempfile.mkdtemp(prefix="pcb-go-quick"))
    try:
        # The candidate file is the variant under test, written last: staging `visible/repo` must
        # not overwrite it with the shipped stub, or every variant would be scored against a
        # solution that returns nothing.
        for path, data in files.items():
            if path.startswith("visible/repo/"):
                relative = path.removeprefix("visible/repo/")
                target = workspace / "work" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            elif path.startswith("hidden/") and path.endswith("_test.go"):
                target = workspace / "work" / path.removeprefix("hidden/")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        solution_bytes = files[solution]
        for path in allowed:
            target = workspace / "work" / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(solution_bytes)
        # The Go toolchain creates GOCACHE/GOMODCACHE/GOPATH itself but requires GOTMPDIR to exist,
        # and the analyzers need their cache home. Creating them here keeps this loop equivalent to
        # the sandbox path, which is the point of running it at all.
        for key in ("GOCACHE", "GOMODCACHE", "GOPATH", "GOTMPDIR", "XDG_CACHE_HOME"):
            directory = go_plans.BASE_ENV.get(key)
            if directory:
                # Absolute guest paths must be re-rooted into this workspace; a bare
                # removeprefix() would resolve them against the drive root instead.
                relative = directory.removeprefix(go_plans.WORKSPACE_ROOT).lstrip("/")
                (workspace / relative).mkdir(parents=True, exist_ok=True)
        identities = plugin.identities
        for tool in [item for item in args.check.split(",") if item]:
            recipe = EVALUATOR if tool in {"staticcheck", "gosec"} else RUNTIME
            record = identities.images[recipe]
            command = argv_for(tool, recipe, record.digest)
            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "-v",
                    f"{workspace.as_posix()}:/workspace",
                    "-w",
                    "/workspace/work" if tool not in {"gofmt"} else "/workspace",
                    # The same environment the plugin's plans declare, so the authoring loop
                    # exercises the configuration that will actually be scored. A check that ran
                    # with different cache paths would not have caught the read-only-cache build
                    # failure that made every variant report a harness error.
                    *(
                        arg
                        for key, value in sorted(go_plans.BASE_ENV.items())
                        for arg in ("--env", f"{key}={value}")
                    ),
                    "--env",
                    "CGO_ENABLED=1" if tool == "race" else "0",
                    record.reference,
                    *command,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            output = (result.stdout + result.stderr).strip()
            print(f"--- {tool}: exit {result.returncode}")
            if output:
                print(output[:2000])
        return 0
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
