"""Run executable admission for every protected Python pilot package (Prompt 10).

    .venv/Scripts/python.exe scripts/python_admit_all.py

Each package is admitted independently; a failure is recorded and the loop continues so one
broken fixture cannot hide the rest. Exit status is nonzero unless every package passed.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import python_task_tool as tool  # noqa: E402

PROTECTED = tool.ROOT / ".protected" / "taskpacks" / "python-pilot"
REPORTS = tool.ROOT / ".protected" / "reports"


async def run() -> int:
    REPORTS.mkdir(parents=True, exist_ok=True)
    packages = sorted(p for p in PROTECTED.iterdir() if (p / "manifest.yaml").is_file())
    failures: list[str] = []
    for package in packages:
        code = await tool.admit(package, REPORTS / f"{package.name}.json")
        print(f"{package.name}: {'PASSED' if code == 0 else 'FAILED'}", flush=True)
        if code != 0:
            failures.append(package.name)
    print(f"\n{len(packages) - len(failures)}/{len(packages)} packages passed executable admission")
    if failures:
        print("failed: " + ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
