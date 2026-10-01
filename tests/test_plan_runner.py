"""Plan runner: trusted input materialisation (offline) and sandbox execution semantics (Docker)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from polycodebench_core.canonical import sha256_bytes
from polycodebench_evaluation.plan_runner import (
    PlanInputError,
    PlanRunner,
    digest_files,
    materialize_inputs,
)
from polycodebench_lang_python import PythonLanguagePlugin
from polycodebench_plugins_api import (
    ExecutionPlan,
    ExitSemantics,
    PlanInput,
    PlanOutput,
    ResourcePolicy,
)
from polycodebench_runner.provider import LocalDockerSandboxProvider

ROOT = Path(__file__).resolve().parents[1]
plugin = PythonLanguagePlugin()
IDS = plugin.identities


def make_plan(argv: tuple[str, ...], *, timeout: int = 20, inputs=(), outputs=(), kind="runtime"):  # type: ignore[no-untyped-def]
    image = IDS.images[kind]
    return ExecutionPlan(
        plan_id="runner.demo",
        image=image.reference,
        image_digest=image.digest,
        argv=argv,
        inputs=tuple(inputs),
        outputs=tuple(outputs),
        resources=ResourcePolicy(
            cpu_millis=1000,
            memory_bytes=256 * 1024**2,
            pids_limit=64,
            disk_bytes=64 * 1024**2,
            timeout_seconds=timeout,
            max_output_bytes=65536,
        ),
        exit_semantics=ExitSemantics(success=(0,), findings=(1,), error=(2,)),
        tool=IDS.tool("pytest", image_kind=kind),
        parser_id="demo",
    )


def test_inputs_come_only_from_their_trusted_role() -> None:
    plan = make_plan(
        ("python", "-V"),
        inputs=[
            PlanInput(path="work/solution.py", role="candidate"),
            PlanInput(path="tests/test_hidden.py", role="overlay"),
        ],
    )
    sources = {
        "candidate": {"solution.py": b"x = 1\n"},
        "overlay": {"tests/test_hidden.py": b"t\n"},
    }
    files = materialize_inputs(plan, sources)
    assert files == {"work/solution.py": b"x = 1\n", "tests/test_hidden.py": b"t\n"}
    # a candidate that ships a file named like a hidden test cannot stand in for the overlay
    with pytest.raises(PlanInputError):
        materialize_inputs(
            plan, {"candidate": {"solution.py": b"x", "tests/test_hidden.py": b"evil"}}
        )
    with pytest.raises(PlanInputError):
        materialize_inputs(plan, {"overlay": sources["overlay"]})  # candidate file missing


def test_declared_input_digests_are_enforced() -> None:
    body = b"def f():\n    return 1\n"
    plan = make_plan(
        ("python", "-V"),
        inputs=[PlanInput(path="tests/test_hidden.py", role="overlay", digest=sha256_bytes(body))],
    )
    assert materialize_inputs(plan, {"overlay": {"tests/test_hidden.py": body}})
    with pytest.raises(PlanInputError):
        materialize_inputs(plan, {"overlay": {"tests/test_hidden.py": body + b"#"}})


def test_digest_files_is_order_independent_and_content_sensitive() -> None:
    a = digest_files({"a.py": b"1", "b.py": b"2"})
    assert a == digest_files({"b.py": b"2", "a.py": b"1"})
    assert a != digest_files({"a.py": b"1", "b.py": b"3"})


needs_docker = pytest.mark.skipif(
    os.environ.get("PCB_TEST_DOCKER") != "1",
    reason="real sandbox tests are opt-in (PCB_TEST_DOCKER=1)",
)


def run_plan(plan: ExecutionPlan, files: dict[str, bytes] | None = None):  # type: ignore[no-untyped-def]
    provider = LocalDockerSandboxProvider(
        allowed_images={
            IDS.runtime.reference: IDS.runtime.digest,
            IDS.evaluator.reference: IDS.evaluator.digest,
        },
        state_dir=ROOT / ".cache" / "plan-runner-state",
        operation_timeout_seconds=120,
    )
    return asyncio.run(PlanRunner(provider).run(plan, files or {}, stage_id="test-plan-runner"))


@needs_docker
def test_exit_codes_outputs_and_stage_inputs_round_trip_through_a_real_guest() -> None:
    plan = make_plan(
        (
            "python",
            "-c",
            "import pathlib,sys; pathlib.Path('out').mkdir(); "
            "src = pathlib.Path('work/in.txt').read_text(); "
            "pathlib.Path('out/r.txt').write_text(src.upper()); sys.exit(1)",
        ),
        outputs=[PlanOutput(path="out/r.txt", format="text")],
    )
    run = run_plan(plan, {"work/in.txt": b"hello"})
    assert run.record.exit_code == 1 and not run.record.timed_out
    assert run.outputs == {"out/r.txt": b"HELLO"}
    assert run.record.isolation_tier == "development"


@needs_docker
def test_a_hard_deadline_kills_only_the_plan_and_keeps_partial_outputs() -> None:
    plan = make_plan(
        (
            "python",
            "-c",
            "import pathlib,time; pathlib.Path('out').mkdir(); "
            "pathlib.Path('out/p.txt').write_text('progress'); time.sleep(60)",
        ),
        timeout=2,
        outputs=[PlanOutput(path="out/p.txt", format="text")],
    )
    run = run_plan(plan)
    assert run.record.timed_out and run.record.exit_code is None
    assert run.outputs == {"out/p.txt": b"progress"}  # the guest survived to be snapshotted


@needs_docker
def test_scored_guests_have_no_network_and_no_package_index() -> None:
    probe = (
        "import subprocess, sys, urllib.request\n"
        "try:\n"
        "    urllib.request.urlopen('https://pypi.org/simple/', timeout=3)\n"
        "    print('NETWORK'); sys.exit(3)\n"
        "except OSError:\n"
        "    pass\n"
        "cmd = [sys.executable, '-m', 'pip', 'install', '--no-input', 'numpy']\n"
        "done = subprocess.run(cmd, capture_output=True, text=True)\n"
        "print('PIP', done.returncode)\n"
        "sys.exit(0 if done.returncode != 0 else 4)\n"
    )
    for kind in ("runtime", "evaluator"):
        run = run_plan(make_plan(("python", "-c", probe), timeout=60, kind=kind))
        assert run.record.exit_code == 0, (kind, run.record.stdout_tail, run.record.stderr_tail)
        assert "PIP" in run.record.stdout_tail and "NETWORK" not in run.record.stdout_tail


@needs_docker
def test_the_image_contains_exactly_the_locked_packages() -> None:
    code = (
        "import importlib.metadata as m, json; "
        "print(json.dumps({d.metadata['Name'].lower().replace('_','-'): d.version "
        "for d in m.distributions()}))"
    )
    run = run_plan(make_plan(("python", "-c", code), kind="evaluator"))
    import json

    installed = json.loads(run.record.stdout_tail.strip().splitlines()[-1])
    for tool, version in IDS.evaluator.tools.items():
        assert installed[tool] == version
    assert len(installed) == IDS.evaluator.installed_package_count
