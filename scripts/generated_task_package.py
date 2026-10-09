"""Turn a package-draft JSON file into a suite-mode Python task package and admit it.

    python scripts/generated_task_package.py DRAFT.json [--output-root DIR] [--report FILE]
                                                          [--no-admit]

The draft carries only what a task author (or a task generator) has to supply: the statement,
the starter solution, public and hidden tests, the reference solution and the admission fixtures.
Everything the admission pipeline derives is generated here:

* ``hidden/oracle.json`` - every module-level ``test_*`` function in the hidden tests becomes a
  required example case of a single required ``acceptance`` group;
* ``hidden/quality-plan.yaml`` - deliberately minimal: no required analyzers, typing expectation
  ``none``, no performance workload, no judge items;
* ``admission/exposure-rights.json`` - built and checked with the plugin's ``ExposureRights``
  model, rights status ``authored_fixture`` and owner confirmation ``pending``;
* ``manifest.yaml`` - the Python pilot ``rate-limiter`` manifest with only the task-specific
  fields replaced.

The package is written under ``.protected/taskpacks/generated/<task_id>/`` (git-ignored) and then
passed through the existing ``scripts/python_task_tool.py`` ``seal``, ``seal --check``,
``validate`` and ``admit`` functions. An existing package directory is never overwritten.

Governance: a generated package is an internal development artefact. Admission here proves only
that the draft travels through the executable pipeline; it does not publish, score or freeze the
task, and the report it writes is not a benchmark result.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import copy
import json
import re
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = ROOT / ".protected" / "taskpacks" / "generated"
DEFAULT_REPORT_ROOT = ROOT / ".protected" / "reports" / "generated"
SOLUTION = "solution.py"
GROUP_ID = "acceptance"
PLACEHOLDER_DIGEST = "sha256:" + "0" * 64
SLUG = r"^[a-z0-9][a-z0-9-]{0,63}$"
TEST_FILE = r"^test_[a-z0-9_]{1,60}\.py$"
REQUIRED_VARIANTS = ("faulty", "alternative", "quality_defective", "timeout")

# Derived from .protected/taskpacks/python-pilot/rate-limiter/manifest.yaml. Only the fields a
# draft needs are replaced in `_manifest`; the computed fields (file lists, digests) are filled
# by `python_task_tool.seal`.
MANIFEST_TEMPLATE: dict[str, Any] = {
    "schema_version": 1,
    "kind": "task_package",
    "task": {
        "task_id": None,
        "version": 1,
        "track": "B",
        "family": "codegen",
        "primary_language": "python",
        "secondary_languages": [],
        "cluster_id": None,
        "difficulty": "intermediate",
        "stratum_id": "python-generated",
        "methodology_label": "independent",
        "output_contract_digest": PLACEHOLDER_DIGEST,
    },
    "output_contract": {
        "schema_version": 1,
        "kind": "task_output_contract",
        "submission_kind": "files",
        "allowed_paths": [SOLUTION],
        "maximum_artifact_bytes": 100000,
        "maximum_file_bytes": 50000,
        "maximum_files": 1,
        "findings_limit": None,
    },
    "source": {
        "schema_version": 1,
        "kind": "task_source",
        "source_kind": "generated_task_draft",
        "immutable_revision": None,
        "issue_or_cve": None,
        "rights_record_id": "polycodebench-authored-fixtures",
        "first_public_at": None,
        "curated_at": None,
        "date_confidence": "estimated",
    },
    "rights": {
        "license_expression": "CC0-1.0",
        "source_url": None,
        "source_revision": None,
        "redistribution_status": "authored_fixture",
        "attribution": None,
        "review_reference": "local-authorship",
    },
    "runtime": {
        "schema_version": 1,
        "kind": "task_runtime",
        "image_digest": PLACEHOLDER_DIGEST,
        "language_plugin_id": "python",
        "language_plugin_version": "0.1.0",
        "build_recipe_digest": PLACEHOLDER_DIGEST,
        "test_recipe_digest": PLACEHOLDER_DIGEST,
        "resource_class": "local-development-small",
    },
    "acceptance": {
        "schema_version": 1,
        "kind": "task_acceptance",
        "required_test_group_ids": [GROUP_ID],
        "hard_condition_ids": [],
        "required_outputs": [SOLUTION],
        "protected_paths": [],
    },
    "quality_plan": {
        "schema_version": 1,
        "kind": "task_quality_plan",
        "applicable_dimensions": ["correctness"],
        "evidence_owners": {},
        "required_analyzers": [],
        "performance_policy_id": None,
        "judge_policy_id": None,
    },
    "oracle": {
        "schema_version": 1,
        "kind": "task_oracle",
        "test_version": None,
        "ground_truth_version": None,
        "reference_version": "reference-v1",
    },
    "protocol_constraints": {
        "schema_version": 1,
        "kind": "protocol_constraints",
        "protocol_id": "single-shot-v1",
        "allowed_tools": [],
        "public_test_feedback": False,
        "hidden_feedback": False,
        "network_policy": "disabled",
        "dependency_inventory_digest": None,
        "maximum_model_turns": 1,
        "maximum_tool_calls": 0,
        "maximum_wall_seconds": 60,
    },
    "visible_files": [],
    "hidden_files": [],
    "fixtures": [],
}

QUALITY_PLAN: dict[str, Any] = {
    "schema_version": 1,
    "kind": "python_task_quality_plan",
    "typing_expectation": "none",
    "opportunity_tags": [],
    "opportunities": {},
    "required_analyzers": [],
    "dependency_inventory": [],
    "judge_items": [],
}


class DraftError(ValueError):
    """The draft cannot become a package."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DraftFixture(_Strict):
    variant: Literal["faulty", "alternative", "quality_defective", "timeout"]
    name: str = Field(pattern=SLUG)
    solution: str = Field(min_length=1)
    expectation: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def expectation_matches_variant(self) -> DraftFixture:
        keys = set(self.expectation)
        if self.variant == "faulty":
            cases = self.expectation.get("failing_cases")
            if keys != {"failing_cases"} or not isinstance(cases, list) or not cases:
                raise ValueError(f"{self.name}: faulty expects a non-empty failing_cases list")
        elif self.variant == "alternative":
            if keys:
                raise ValueError(f"{self.name}: alternative takes an empty expectation")
        elif self.variant == "quality_defective":
            families = self.expectation.get("expected_issue_families")
            if (
                keys != {"expected_issue_families"}
                or not isinstance(families, list)
                or not families
            ):
                raise ValueError(
                    f"{self.name}: quality_defective expects a non-empty expected_issue_families"
                )
        elif self.expectation != {"expected_failure": "candidate_timeout"}:
            raise ValueError(f"{self.name}: timeout expects expected_failure candidate_timeout")
        return self


class DraftProvenance(_Strict):
    authorship: str = Field(min_length=3, max_length=500)
    originality_statement: str = Field(min_length=10, max_length=1000)
    public_exposure_review: str = Field(min_length=5, max_length=1000)
    access_history: tuple[dict[str, str], ...] = Field(min_length=1)


class PackageDraft(_Strict):
    schema_version: Literal[1] = 1
    kind: Literal["generated_task_package_draft"] = "generated_task_package_draft"
    task_id: str = Field(pattern=SLUG)
    screening_reference: str = Field(min_length=3, max_length=200)
    curated_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    difficulty: str = Field(default="intermediate", pattern=SLUG)
    statement: str = Field(min_length=20)
    starter_solution: str = Field(min_length=1)
    public_tests: dict[str, str] = Field(min_length=1)
    hidden_tests: dict[str, str] = Field(min_length=1)
    reference_solution: str = Field(min_length=1)
    fixtures: tuple[DraftFixture, ...] = Field(min_length=4)
    provenance: DraftProvenance

    @model_validator(mode="after")
    def complete(self) -> PackageDraft:
        for name in (*self.public_tests, *self.hidden_tests):
            if not re.fullmatch(TEST_FILE, name):
                raise ValueError(f"test file name must match {TEST_FILE}: {name}")
        variants = {f.variant for f in self.fixtures}
        missing = [v for v in REQUIRED_VARIANTS if v not in variants]
        if missing:
            raise ValueError(f"draft declares no fixture for variant(s): {', '.join(missing)}")
        names = [f.name for f in self.fixtures]
        if len(names) != len(set(names)) or "reference" in names:
            raise ValueError("fixture names must be unique and must not be 'reference'")
        return self


def load_draft(source: Mapping[str, Any] | Path) -> PackageDraft:
    document = (
        json.loads(source.read_text(encoding="utf-8")) if isinstance(source, Path) else source
    )
    try:
        return PackageDraft.model_validate(document)
    except ValidationError as error:
        raise DraftError(str(error)) from error


def hidden_cases(path: str, source: str) -> list[str]:
    """Case ids (``tests/<file>::test_x``) of the module-level ``test_*`` functions."""
    tree = ast.parse(source, filename=path)
    cases: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            raise DraftError(f"{path}: test classes are not supported, use test_* functions")
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
            "test"
        ):
            if not node.name.startswith("test_"):
                raise DraftError(f"{path}: {node.name} would be collected but is not test_*")
            cases.append(f"{path}::{node.name}")
    return cases


def build_oracle(draft: PackageDraft) -> dict[str, Any]:
    files = [f"tests/{name}" for name in sorted(draft.hidden_tests)]
    cases = [
        case
        for name in sorted(draft.hidden_tests)
        for case in hidden_cases(f"tests/{name}", draft.hidden_tests[name])
    ]
    if not cases:
        raise DraftError("hidden tests define no test_* function")
    return {
        "schema_version": 1,
        "kind": "python_task_oracle",
        "oracle_version": f"{draft.task_id}-oracle-v1",
        "case_timeout_seconds": 5,
        "suite_timeout_seconds": 60,
        "hypothesis_examples": 50,
        "groups": [
            {
                "group_id": GROUP_ID,
                "required": True,
                "classification": "acceptance",
                "files": files,
                "cases": [
                    {"case_id": case, "required": True, "case_kind": "example"} for case in cases
                ],
            }
        ],
    }


def attribution(draft: PackageDraft) -> str:
    return (
        f"Generated by scripts/generated_task_package.py from a package draft under screening "
        f"report {draft.screening_reference}; internal development package, not published"
    )


def build_exposure_rights(draft: PackageDraft) -> dict[str, Any]:
    from polycodebench_lang_python.taskspec import ExposureRights

    record = {
        "authorship": draft.provenance.authorship,
        "originality_statement": draft.provenance.originality_statement,
        "first_public_at": None,
        "public_exposure_review": draft.provenance.public_exposure_review,
        "access_history": list(draft.provenance.access_history),
        "provider_transmission_policy": "visible bundle only",
        "rights": {
            "license_expression": "CC0-1.0",
            "status": "authored_fixture",
            "owner_confirmation": "pending",
            "attribution": attribution(draft),
        },
    }
    model = ExposureRights.model_validate_json(json.dumps(record))
    return json.loads(model.model_dump_json())


def _manifest(draft: PackageDraft) -> dict[str, Any]:
    manifest = copy.deepcopy(MANIFEST_TEMPLATE)
    revision = f"generated-{draft.task_id}-draft-v1"
    manifest["task"]["task_id"] = draft.task_id
    manifest["task"]["cluster_id"] = f"{draft.task_id}-cluster"
    manifest["task"]["difficulty"] = draft.difficulty
    manifest["source"]["immutable_revision"] = revision
    manifest["source"]["curated_at"] = draft.curated_at
    manifest["rights"]["source_revision"] = revision
    manifest["rights"]["attribution"] = attribution(draft)
    manifest["oracle"]["test_version"] = f"{draft.task_id}-oracle-v1"
    fixtures: list[dict[str, Any]] = [
        {
            "name": "reference",
            "variant": "reference",
            "solution_path": f"hidden/reference/{SOLUTION}",
            "expectation": {},
        }
    ]
    fixtures.extend(
        {
            "name": f.name,
            "variant": f.variant,
            "solution_path": f"admission/{f.name}/{SOLUTION}",
            "expectation": dict(f.expectation),
        }
        for f in draft.fixtures
    )
    manifest["fixtures"] = fixtures
    return manifest


def package_files(draft: PackageDraft) -> dict[str, str]:
    """Every package file (relative path to text) except the manifest."""
    oracle = build_oracle(draft)
    known = {c["case_id"] for c in oracle["groups"][0]["cases"]}
    for fixture in draft.fixtures:
        unknown = set(fixture.expectation.get("failing_cases", ())) - known
        if unknown:
            raise DraftError(f"{fixture.name}: failing_cases not in the oracle: {sorted(unknown)}")
    files = {
        "visible/task.md": draft.statement,
        f"visible/repo/{SOLUTION}": draft.starter_solution,
        f"hidden/reference/{SOLUTION}": draft.reference_solution,
        "hidden/oracle.json": json.dumps(oracle, indent=2) + "\n",
        "hidden/quality-plan.yaml": yaml.safe_dump(QUALITY_PLAN, sort_keys=False),
        "admission/exposure-rights.json": json.dumps(build_exposure_rights(draft), indent=2) + "\n",
    }
    files.update({f"visible/tests/{n}": body for n, body in draft.public_tests.items()})
    files.update({f"hidden/tests/{n}": body for n, body in draft.hidden_tests.items()})
    files.update({f"admission/{f.name}/{SOLUTION}": f.solution for f in draft.fixtures})
    visible = "\n".join(body for path, body in files.items() if path.startswith("visible/"))
    for path, body in files.items():
        if not path.startswith("visible/") and body.strip() and body in visible:
            raise DraftError(f"hidden content of {path} appears in the visible bundle")
    return files


def generate(draft: PackageDraft, output_root: Path = DEFAULT_OUTPUT_ROOT) -> Path:
    """Write the unsealed package; refuses to touch an existing package directory."""
    files = package_files(draft)
    root = output_root / draft.task_id
    if root.exists():
        raise FileExistsError(f"package directory already exists, refusing to overwrite: {root}")
    output_root.mkdir(parents=True, exist_ok=True)
    root.mkdir()
    for relative, body in sorted(files.items()):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body if body.endswith("\n") else body + "\n", encoding="utf-8")
    (root / "manifest.yaml").write_text(
        yaml.safe_dump(_manifest(draft), sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )
    return root


def _tool() -> Any:
    scripts = str(Path(__file__).resolve().parent)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import python_task_tool

    return python_task_tool


def run_pipeline(root: Path, report: Path | None) -> int:
    tool = _tool()
    steps: list[tuple[str, int]] = [("seal", tool.seal(root, check=False))]
    steps.append(("seal --check", tool.seal(root, check=True)))
    steps.append(("validate", tool.validate(root)))
    if report is not None:
        steps.append(("admit", asyncio.run(tool.admit(root, report))))
    for name, code in steps:
        print(f"step {name}: {'ok' if code == 0 else f'FAILED ({code})'}")
    return 0 if all(code == 0 for _, code in steps) else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="generated_task_package")
    parser.add_argument("draft", type=Path)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--report", type=Path, help="admission report path")
    parser.add_argument("--no-admit", action="store_true", help="stop after validate")
    args = parser.parse_args(argv)
    try:
        draft = load_draft(args.draft)
        root = generate(draft, args.output_root.resolve())
    except (DraftError, FileExistsError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"generated {root} at {datetime.now(UTC).isoformat(timespec='seconds')}")
    report = (
        None if args.no_admit else (args.report or DEFAULT_REPORT_ROOT / f"{draft.task_id}.json")
    )
    return run_pipeline(root, report)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
