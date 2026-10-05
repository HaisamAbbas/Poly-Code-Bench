"""Admission accepts C++'s typed JSON performance output as workload evidence."""

from __future__ import annotations

import asyncio

import pytest
from cpp_plugin_support import frozen, package_files
from polycodebench_core.identity import new_entity_id
from polycodebench_evaluation.plan_runner import PlanRun
from polycodebench_evaluation.suite_admission import SuiteAdmission, variant_files
from polycodebench_lang_cpp import CppLanguagePlugin
from polycodebench_plugins_api import make_record


class MetricOutputRunner:
    def __init__(self, output: bytes) -> None:
        self.output = output

    async def run(self, plan, files, *, stage_id):
        del files, stage_id
        record = make_record(
            plan,
            exit_code=0,
            timed_out=False,
            duration_ms=1,
            stdout=b"",
            stderr=b"",
            isolation_tier="development",
            sandbox_id=new_entity_id(),
        )
        return PlanRun(plan, record, {"out/perf.out": self.output})


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (b'{"elapsed_ns":10,"peak_rss_kb":20,"tokens":74}\n', True),
        (b'{"elapsed_ns":0,"peak_rss_kb":20}\n', False),
        (b"74\n", True),
        (b"not a checksum\n", False),
    ],
)
def test_workload_smoke_validates_declared_metrics_or_integer_checksum(
    output: bytes, expected: bool
) -> None:
    plugin = CppLanguagePlugin()
    view = frozen(plugin)
    files = package_files()
    candidate = variant_files(
        files,
        "hidden/reference/src/top_words.cpp",
        ["include/top_words.hpp", "src/top_words.cpp"],
    )
    overlay = {
        f"work/{path.removeprefix('hidden/')}": data
        for path, data in files.items()
        if path.startswith("hidden/perf/")
    }
    runner = MetricOutputRunner(output)
    engine = SuiteAdmission(
        plugin,
        runner,  # type: ignore[arg-type]
        image_digests=(plugin.identities.runtime.digest, plugin.identities.evaluator.digest),
    )

    passed, _ = asyncio.run(
        engine.workload_smoke(
            view=view,
            candidate_files=candidate,
            overlay=overlay,
            config=plugin.trusted_inputs(files, view),
        )
    )

    assert passed is expected
