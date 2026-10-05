"""C++ test-process crashes are candidate failures only when supervisor evidence agrees."""

from __future__ import annotations

import json

from cpp_plugin_support import frozen
from polycodebench_core.identity import new_entity_id
from polycodebench_lang_cpp import CppLanguagePlugin
from polycodebench_plugins_api import (
    EXECUTION_RECORD_PATH,
    DictArtifactReader,
    make_record,
    record_bytes,
)


def test_a_signal_exit_matching_the_supervisor_record_is_a_candidate_failure() -> None:
    plugin = CppLanguagePlugin()
    view = frozen(plugin)
    group = next(group for group in plugin.test_plan(view).groups if group.group_id == "behaviour")
    inventory = next(group for group in plugin.inventory(view) if group.group_id == "behaviour")
    record = make_record(
        group.plan,
        exit_code=134,
        timed_out=False,
        duration_ms=10,
        stdout=b"",
        stderr=b"",
        isolation_tier="development",
        sandbox_id=new_entity_id(),
    )
    reader = DictArtifactReader(
        {
            "out/behaviour.out": b"",
            "out/behaviour.err": b"uncaught candidate exception\n",
            "out/behaviour.run.json": json.dumps(
                {
                    "schema": "pcb-cpp-test-v1",
                    "phase": "run",
                    "compile_exit_code": 0,
                    "exit_code": 134,
                    "timed_out": False,
                }
            ).encode(),
            EXECUTION_RECORD_PATH: record_bytes(record),
        }
    )

    records, control = plugin.parse_test_group(group, inventory, reader, repetition=0)

    assert records == []
    assert control.status == "candidate_killed"
    assert "terminated abnormally" in control.detail
