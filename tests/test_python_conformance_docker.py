"""Real-sandbox evidence for the Python plugin (opt-in: PCB_TEST_DOCKER=1).

E2E-04 (Python subcase): reference / faulty / alternative / quality-defective / timeout variants
of the public fixture are executed in the pinned images. E2E-15 (Python): conformance categories.
E2E-16 (Python): findings exits parse, crashes / missing tools / timeouts never look clean.
These run in the local Docker driver (development isolation), not a production worker.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
from polycodebench_core.models import MeasurementStatus
from polycodebench_plugins_api.admission import CONFORMANCE_CATEGORIES

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

pytestmark = pytest.mark.skipif(
    os.environ.get("PCB_TEST_DOCKER") != "1",
    reason="real sandbox tests are opt-in (PCB_TEST_DOCKER=1)",
)


def test_python_conformance_covers_every_category_with_real_execution() -> None:
    import python_conformance

    report = asyncio.run(python_conformance.run_conformance())
    failed = [(c.category, c.name, c.observed) for c in report.cases if not c.passed]
    assert not failed, failed
    assert {c.category for c in report.cases} == set(CONFORMANCE_CATEGORIES)
    assert report.passed and report.execution_tier == "development_sandbox"
    out = ROOT / ".cache" / "conformance-python.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(report.model_dump_json(indent=2), encoding="utf-8")


def test_top_words_executable_admission_and_pending_gates() -> None:
    import python_task_tool
    from polycodebench_evaluation.plan_runner import PlanRunner
    from polycodebench_evaluation.suite_admission import SuiteAdmission
    from polycodebench_lang_python import PythonLanguagePlugin
    from polycodebench_runner.provider import LocalDockerSandboxProvider
    from polycodebench_services.task_packages import TaskPackageImporter

    root = python_task_tool.ROOT / "plugins" / "languages" / "python" / "fixtures" / "top-words"
    assert python_task_tool.seal(root, check=True) == 0
    plugin = PythonLanguagePlugin()
    ids = plugin.identities
    provider = LocalDockerSandboxProvider(
        allowed_images={
            ids.runtime.reference: ids.runtime.digest,
            ids.evaluator.reference: ids.evaluator.digest,
        },
        state_dir=ROOT / ".cache" / "python-admission-state",
        operation_timeout_seconds=120,
    )
    package = TaskPackageImporter().import_package(root)
    manifest = python_task_tool._load(root)
    report = asyncio.run(
        SuiteAdmission(
            plugin, PlanRunner(provider), image_digests=(ids.runtime.digest, ids.evaluator.digest)
        ).admit(
            files=python_task_tool._files(root),
            manifest=manifest,
            package_digest=package.package_digest,
            precheck={
                "strict-schema": (True, "importer"),
                "rights-and-provenance": (True, "authored_fixture"),
            },
        )
    )
    failed = [(c.check_id, c.detail) for c in report.checks if c.status != "pass"]
    assert not failed, failed
    assert report.executable_admission_passed
    # later-stage gates stay explicit: executable admission is not quality admission
    assert report.quality_admission == "pending" and len(report.pending_gates) >= 5
    by_name = {run.name: run for run in report.runs}
    assert set(by_name["reference"].gates) == {"pass"} and len(by_name["reference"].gates) == 5
    assert len(set(by_name["reference"].outcome_digests)) == 1
    assert set(by_name["faulty-ties"].gates) == {"fail"}
    assert set(by_name["quality-defective"].gates) == {"pass"}
    assert {"mutable-default", "bare-except", "manual-counter"} <= set(
        by_name["quality-defective"].issue_families
    )
    assert all(set(by_name[name].gates) == {"fail"} for name in ("timeout-case", "timeout-hard"))
    assert MeasurementStatus.MEASURED  # analyzers produced measured scans for the reference
    assert all("measured" in scan for scan in by_name["reference"].analyzer_scans)
