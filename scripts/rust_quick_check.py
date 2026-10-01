"""Fast authoring check for one Rust task variant (not admission, not evidence).

    .venv/Scripts/python.exe scripts/rust_quick_check.py PACKAGE VARIANT [--clippy] [--miri]

``VARIANT`` is ``reference`` or the name of a directory under ``admission/``. The script copies the
visible crate scaffold, the variant's ``src/`` and every hidden test file into a scratch directory
and runs ``cargo test`` (optionally Clippy with the plugin's selected lints, or Miri) in the pinned
image with the network disabled. It exists so an author can iterate in seconds; the verdicts that
count are the ones produced by ``rust_task_tool.py admit`` through the supervisor.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from polycodebench_lang_rust import RustLanguagePlugin
from polycodebench_lang_rust.plans import MIRI_TOOLCHAIN, selected_lints


def stage(package: Path, variant: str, target: Path) -> None:
    shutil.copytree(package / "visible" / "repo", target, dirs_exist_ok=True)
    source = package / ("hidden/reference" if variant == "reference" else f"admission/{variant}")
    if (target / "src").exists():
        shutil.rmtree(target / "src")
    shutil.copytree(source / "src", target / "src")
    tests = package / "hidden" / "tests"
    if tests.is_dir():
        shutil.copytree(tests, target / "tests", dirs_exist_ok=True)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="rust_quick_check")
    parser.add_argument("package", type=Path)
    parser.add_argument("variant")
    parser.add_argument("--clippy", action="store_true")
    parser.add_argument("--miri", action="store_true")
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args(argv)
    ids = RustLanguagePlugin().identities
    scratch = Path(tempfile.mkdtemp(prefix="rustcheck-"))
    try:
        stage(args.package.resolve(), args.variant, scratch)
        if args.clippy:
            image = ids.evaluator.reference
            flags = " ".join(f"-W {lint}" for lint in selected_lints())
            command = f"cargo clippy --offline --locked -- -A clippy::all {flags}"
        elif args.miri:
            image = ids.evaluator.reference
            command = (
                f"MIRI_SYSROOT=/opt/pcb/cache/miri cargo +{MIRI_TOOLCHAIN} miri test "
                "--offline --locked"
            )
        else:
            image = ids.runtime.reference
            command = "cargo test --offline --locked --no-fail-fast -- --test-threads=1"
        completed = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--memory",
                "2g",
                "-v",
                f"{scratch}:/w",
                "-w",
                "/w",
                "-e",
                "CARGO_TARGET_DIR=/tmp/target",
                image,
                "timeout",
                str(args.timeout),
                "sh",
                "-c",
                command,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        text = (completed.stdout + completed.stderr)[-6000:]
        # Miri prints non-ASCII quotes; a Windows console would otherwise fail to encode them.
        sys.stdout.buffer.write(text.encode("utf-8", errors="replace"))
        sys.stdout.flush()
        print(f"[exit {completed.returncode}]")
        return completed.returncode
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
