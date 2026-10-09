"""Run executable admission for every protected Python pilot package (Prompt 10).

    .venv/Scripts/python.exe scripts/python_admit_all.py [--protected DIR] [--reports DIR]

Each package is admitted independently; a failure is recorded and the loop continues so one
broken fixture cannot hide the rest. Exit status is nonzero unless every package passed.

The defaults are the amd64 pilot pack. For the arm64 pack, point ``--protected`` at
``.protected/taskpacks/python-pilot-arm64`` and ``--reports`` at a separate directory; the
plugin reads the arm64 image pins on an arm64 host (or with ``PCB_IMAGE_ARCH=arm64``).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import python_task_tool as tool  # noqa: E402

PROTECTED = tool.ROOT / ".protected" / "taskpacks" / "python-pilot"
REPORTS = tool.ROOT / ".protected" / "reports"


async def run(protected: Path = PROTECTED, reports: Path = REPORTS) -> int:
    reports.mkdir(parents=True, exist_ok=True)
    packages = sorted(p for p in protected.iterdir() if (p / "manifest.yaml").is_file())
    failures: list[str] = []
    for package in packages:
        code = await tool.admit(package, reports / f"{package.name}.json")
        print(f"{package.name}: {'PASSED' if code == 0 else 'FAILED'}", flush=True)
        if code != 0:
            failures.append(package.name)
    print(f"\n{len(packages) - len(failures)}/{len(packages)} packages passed executable admission")
    if failures:
        print("failed: " + ", ".join(failures))
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python_admit_all")
    parser.add_argument("--protected", type=Path, default=PROTECTED)
    parser.add_argument("--reports", type=Path, default=REPORTS)
    args = parser.parse_args(argv)
    return asyncio.run(run(args.protected.resolve(), args.reports.resolve()))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
