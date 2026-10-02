"""Language-neutral conformance and executable-admission evidence models.

These records only carry observed facts. Gates that need later stages (generic evaluator,
performance baseline, judge anchors, scoring replay) are listed as ``pending_gates`` so an
unfinished task can never read as fully admitted (Execution Contract 4.2).
"""

from __future__ import annotations

import json
from typing import Literal

from polycodebench_core.canonical import canonical_digest
from polycodebench_core.models import Digest, Slug
from pydantic import Field, model_validator

from polycodebench_plugins_api.contracts import PluginModel

CONFORMANCE_CATEGORIES = (
    "valid_solution",
    "incorrect_solution",
    "anti_pattern",
    "analyzer_failure",
    "missing_dependency",
    "timeout",
    "profile_applicability",
)
CheckStatus = Literal["pass", "fail", "not_run"]


class ConformanceCaseResult(PluginModel):
    kind: Literal["conformance_case_result"] = "conformance_case_result"
    category: Literal[
        "valid_solution",
        "incorrect_solution",
        "anti_pattern",
        "analyzer_failure",
        "missing_dependency",
        "timeout",
        "profile_applicability",
    ]
    name: Slug
    passed: bool
    expected: str = Field(max_length=300)
    observed: str = Field(max_length=600)


class ConformanceReport(PluginModel):
    kind: Literal["conformance_report"] = "conformance_report"
    plugin_id: Slug
    plugin_version: str
    execution_tier: Literal["local_fixture", "development_sandbox", "production_worker"]
    image_digests: tuple[Digest, ...]
    cases: tuple[ConformanceCaseResult, ...] = Field(min_length=1)
    passed: bool
    report_digest: Digest

    @model_validator(mode="after")
    def digest_and_summary_match(self) -> ConformanceReport:
        content = self.model_dump(mode="json", exclude={"report_digest"})
        if canonical_digest(content) != self.report_digest:
            raise ValueError("conformance report digest does not match its contents")
        covered = {case.category for case in self.cases}
        expected_pass = covered == set(CONFORMANCE_CATEGORIES) and all(c.passed for c in self.cases)
        if self.passed is not expected_pass:
            raise ValueError("conformance summary must require every category and every case")
        return self


def make_conformance_report(
    *,
    plugin_id: str,
    plugin_version: str,
    execution_tier: Literal["local_fixture", "development_sandbox", "production_worker"],
    image_digests: tuple[str, ...],
    cases: tuple[ConformanceCaseResult, ...],
) -> ConformanceReport:
    covered = {case.category for case in cases}
    passed = covered == set(CONFORMANCE_CATEGORIES) and all(case.passed for case in cases)
    draft = {
        "schema_version": 1,
        "kind": "conformance_report",
        "plugin_id": plugin_id,
        "plugin_version": plugin_version,
        "execution_tier": execution_tier,
        "image_digests": list(image_digests),
        "cases": [case.model_dump(mode="json") for case in cases],
        "passed": passed,
    }
    return ConformanceReport.model_validate_json(
        json.dumps({**draft, "report_digest": canonical_digest(draft)})
    )


class VariantRun(PluginModel):
    kind: Literal["variant_run"] = "variant_run"
    name: Slug
    # Language task contracts can name the defect subtype while the generic admission engine
    # still applies its shared acceptance roles (`faulty` / `quality_defective`).
    variant: Slug
    repetitions: int = Field(ge=1)
    gates: tuple[Literal["pass", "fail", "incomplete"], ...]
    failing_cases: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    outcome_digests: tuple[Digest, ...] = ()
    analyzer_scans: tuple[str, ...] = ()
    issue_families: tuple[str, ...] = ()
    durations_ms: tuple[int, ...] = ()
    quality_only_pass: bool | None = None


class AdmissionCheck(PluginModel):
    kind: Literal["admission_check"] = "admission_check"
    check_id: Slug
    status: CheckStatus
    detail: str = Field(default="", max_length=600)


class SuiteAdmissionReport(PluginModel):
    kind: Literal["suite_admission_report"] = "suite_admission_report"
    profile_id: Slug
    task_id: Slug
    task_version: int = Field(gt=0)
    package_digest: Digest
    plugin_id: Slug
    execution_tier: Literal["local_fixture", "development_sandbox", "production_worker"]
    image_digests: tuple[Digest, ...]
    checks: tuple[AdmissionCheck, ...] = Field(min_length=1)
    runs: tuple[VariantRun, ...] = Field(min_length=1)
    executable_admission_passed: bool
    pending_gates: tuple[str, ...] = ()
    quality_admission: Literal["pending", "complete"] = "pending"
    report_digest: Digest

    @model_validator(mode="after")
    def consistent(self) -> SuiteAdmissionReport:
        content = self.model_dump(mode="json", exclude={"report_digest"})
        if canonical_digest(content) != self.report_digest:
            raise ValueError("admission report digest does not match its contents")
        expected = all(check.status == "pass" for check in self.checks)
        if self.executable_admission_passed is not expected:
            raise ValueError("executable admission summary disagrees with its checks")
        if self.quality_admission == "complete" and self.pending_gates:
            raise ValueError("quality admission cannot be complete with pending gates")
        return self
