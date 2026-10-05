"""The C++ build harness must create its scratch root and write evidence to the workspace."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_build_harness_creates_missing_root_and_writes_outputs_outside_it(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    build_root = workspace / ".pcb-build"
    output_base = workspace / "out" / "build"
    script = (
        Path(__file__).resolve().parents[1]
        / "plugins/languages/cpp/src/polycodebench_lang_cpp/guest/pcb_cpp_build.py"
    )

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--name",
            str(output_base),
            "--deadline",
            "2",
            "--root",
            str(build_root),
            "--compiler",
            "pcb-missing-cxx-for-test",
            "--source",
            str(workspace / "src.cpp"),
            "--archive",
            str(build_root / "libpcb.a"),
        ],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 127
    assert build_root.is_dir()
    assert (workspace / "out/build.err").is_file()
    run_record = json.loads((workspace / "out/build.run.json").read_text(encoding="utf-8"))
    assert run_record["exit_code"] == 127
    assert not (build_root / "out").exists()
