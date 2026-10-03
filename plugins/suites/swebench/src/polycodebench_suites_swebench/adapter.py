"""The SWE-bench-style suite adapter (Prompt 24, PCB-24-1).

Implements the ``SuiteAdapter`` shape from Technical Spec 17.1 for repository repair: import a
versioned source manifest, validate the methodology record against the deviations register,
build the frozen family protocol, and export the native metric.

Two properties are structural here rather than left to callers:

* **Fail closed on methodology.** ``validate_methodology`` rejects a record whose public label is
  not permitted for the family, and refuses ``native`` for anything without an upstream dataset
  identity. An invented fixture therefore cannot reach a scored release labelled ``native``.
* **The native metric is only ever produced by the upstream evaluator.** ``native_metrics``
  accepts the upstream evaluator's own result and refuses to synthesise one from a local test
  fraction.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import yaml
from polycodebench_core.canonical import canonical_digest, sha256_bytes
from polycodebench_core.models import Slug

from polycodebench_suites_swebench import patching
from polycodebench_suites_swebench.records import (
    FAIL_TO_PASS,
    PASS_TO_PASS,
    MethodologyRecord,
    NativeGradeResult,
    NativeTaskDraft,
    NativeTaskInstance,
    NativeTestSpec,
    check_instance_leakage,
    enforce_patch_paths,
    graded_test_modules,
    instance_digest,
    normalize_native_lists,
)

#: The family this adapter serves.
SUITE_FAMILY = "swebench"
#: The label vocabulary the deviations register permits for this family.
PERMITTED_LABELS = ("native", "adapted", "inspired")
#: The version string an authored fixture must declare, so it can never pass as a dataset release.
AUTHORED_FIXTURE_VERSION = "authored-fixture-v1"


#: Record fields that are sequences in the contract. YAML spells them as lists, and the contract
#: models are strict about ``list`` vs ``tuple``, so the import coerces rather than the models.
SEQUENCE_FIELDS = (
    "official_sources",
    "input_rules",
    "output_rules",
    "feedback_tools",
    "native_metrics",
    "deviations",
    "fail_to_pass",
    "pass_to_pass",
    "fail_to_fail",
    "pass_to_fail",
    "protocol_deviations",
)


def _as_contract(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Coerce YAML sequences to the tuples the strict contract models require."""
    return {
        key: (tuple(value) if key in SEQUENCE_FIELDS and isinstance(value, list) else value)
        for key, value in fields.items()
    }


class SuiteImportError(ValueError):
    """The source manifest or an instance record is not admissible."""


@dataclass(frozen=True, slots=True)
class SourceManifest:
    """A pinned statement of where instance records came from.

    ``dataset_revision`` is the immutable revision of the *data*. ``authored`` marks a fixture this
    repository wrote: such a manifest can never yield a ``native`` label, because there is no
    upstream dataset behind it.
    """

    name: str
    dataset_revision: str
    source_url: str | None
    authored: bool
    license_expression: str
    image_digest: str | None = None
    record_paths: tuple[str, ...] = ()
    #: Where the declared record paths resolve from. A manifest names relative paths, so its base
    #: directory is part of its identity rather than the process working directory.
    base_dir: Path = Path(".")

    @classmethod
    def from_yaml(cls, path: Path) -> SourceManifest:
        """Read a manifest and pin its record paths against the manifest's own directory."""
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(document, Mapping) or document.get("kind") != "suite_source_manifest":
            raise SuiteImportError("a source manifest must be a suite_source_manifest mapping")
        authored = bool(document.get("authored", False))
        source_url = document.get("source_url")
        if not authored and not source_url:
            raise SuiteImportError(
                "an official dataset manifest needs a source_url; an authored fixture must "
                + "declare authored: true so it cannot claim official provenance"
            )
        if authored and source_url:
            raise SuiteImportError(
                "an authored fixture must not name an upstream source_url; that would claim "
                + "provenance it does not have"
            )
        dataset_revision = str(document.get("dataset_revision") or "")
        if not dataset_revision:
            raise SuiteImportError("a source manifest must pin dataset_revision")
        name = str(document.get("name") or "")
        if not name:
            raise SuiteImportError("a source manifest must name its suite")
        image_digest = document.get("image_digest")
        records = document.get("records") or ()
        if not isinstance(records, list):
            raise SuiteImportError("a source manifest records must be a list")
        return cls(
            name=name,
            dataset_revision=dataset_revision,
            source_url=str(source_url) if source_url else None,
            authored=authored,
            license_expression=str(document.get("license_expression") or "unknown"),
            image_digest=str(image_digest) if image_digest else None,
            record_paths=tuple(str(item) for item in records),
            base_dir=path.parent,
        )


def _default_test_command(instance: NativeTaskInstance) -> str:
    """The test command for a record that does not declare one, derived from its test modules."""
    modules = graded_test_modules(instance)
    return f"python -m pytest {' '.join(modules)}" if modules else "python -m pytest -q"


@dataclass(frozen=True, slots=True)
class FamilyProtocol:
    """The frozen solve/grade contract for one native repository-repair instance."""

    instance_id: str
    base_commit: str
    solve_input_paths: tuple[str, ...]
    allowed_change_paths: tuple[str, ...]
    test_command: str
    fail_to_pass: tuple[str, ...]
    pass_to_pass: tuple[str, ...]
    family: Literal["repo_repair"] = "repo_repair"
    output_kind: Literal["patch"] = "patch"
    hidden_feedback: bool = False
    public_feedback: bool = False

    def grading_overlay(self) -> dict[str, object]:
        """The protected material grading overlays on top of the solve workspace."""
        return {
            "apply_test_patch": True,
            "expected_test_lists": {
                FAIL_TO_PASS: list(self.fail_to_pass),
                PASS_TO_PASS: list(self.pass_to_pass),
            },
            "base_commit": self.base_commit,
        }


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """What ``validate_methodology`` found: the checks that passed, or the issues to fix."""

    suite_id: str
    ok: bool
    checks: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()

    def as_json(self) -> str:
        return json.dumps(
            {
                "schema_version": 1,
                "kind": "suite_methodology_validation",
                "suite_id": self.suite_id,
                "ok": self.ok,
                "checks": list(self.checks),
                "issues": list(self.issues),
            },
            sort_keys=True,
        )


@dataclass(frozen=True, slots=True)
class NativeMetricExport:
    """The native metric as reported, plus the PolyCodeBench fields it must not replace."""

    suite_id: str
    methodology_label: str
    metrics: Mapping[str, object]
    gate_evidence_separate: bool = True
    quality_evidence_separate: bool = True
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_json(self) -> str:
        return json.dumps(
            {
                "schema_version": 1,
                "kind": "native_metric_export",
                "suite_id": self.suite_id,
                "methodology_label": self.methodology_label,
                "native_metrics": dict(self.metrics),
                "polycodebench_gate_is_separate": self.gate_evidence_separate,
                "polycodebench_quality_is_separate": self.quality_evidence_separate,
                "notes": list(self.notes),
            },
            sort_keys=True,
        )


class SweBenchStyleSuiteAdapter:
    api_version: int = 1
    suite_id: Slug = SUITE_FAMILY

    def __init__(self, deviations: Mapping[str, Any] | None = None) -> None:
        self._deviations = deviations

    def import_tasks(self, source: SourceManifest) -> list[NativeTaskDraft]:
        """Import every instance record the manifest declares, bound to its methodology record."""
        if not source.record_paths:
            raise SuiteImportError("a source manifest must declare at least one record path")
        drafts: list[NativeTaskDraft] = []
        for relative in source.record_paths:
            instance = self.import_instance(source, source.base_dir / relative)
            drafts.append(self.bind(instance, source))
        return drafts

    def import_instance(self, source: SourceManifest, record_path: Path) -> NativeTaskInstance:
        """Read one instance record and the repository snapshot beside it."""
        document = yaml.safe_load(record_path.read_text(encoding="utf-8"))
        if not isinstance(document, Mapping) or document.get("kind") != "native_task_instance":
            raise SuiteImportError(f"{record_path}: not a native_task_instance record")
        fields = {key: value for key, value in document.items() if key != "repo_dir"}
        repo_files = self._read_snapshot(
            record_path.parent, str(document.get("repo_dir") or "repo")
        )
        try:
            instance = NativeTaskInstance.model_validate(
                {**_as_contract(fields), "repo_files": repo_files}
            )
        except Exception as error:  # pydantic and YAML shape errors alike
            raise SuiteImportError(f"{record_path}: {error}") from error
        instance, _ = normalize_native_lists(instance)
        self._check_provenance(record_path, instance, source)
        leaks = check_instance_leakage(
            instance, {f"repo/{path}": data for path, data in repo_files.items()}
        )
        if leaks:
            raise SuiteImportError(f"{record_path}: " + "; ".join(leaks))
        return instance

    @staticmethod
    def _check_provenance(
        record_path: Path, instance: NativeTaskInstance, source: SourceManifest
    ) -> None:
        """An authored fixture and official data cannot be imported into each other's suite."""
        if source.authored and instance.version != AUTHORED_FIXTURE_VERSION:
            raise SuiteImportError(
                f"{record_path}: an authored fixture must declare version "
                + f"{AUTHORED_FIXTURE_VERSION} so it cannot be mistaken for a dataset release"
            )
        if not source.authored and instance.version == AUTHORED_FIXTURE_VERSION:
            raise SuiteImportError(
                f"{record_path}: an authored fixture cannot be imported as official data"
            )

    @staticmethod
    def _read_snapshot(root: Path, repo_dir: str) -> dict[str, bytes]:
        base = root / PurePosixPath(repo_dir)
        if not base.is_dir():
            raise SuiteImportError(f"snapshot directory {repo_dir!r} is missing")
        files: dict[str, bytes] = {}
        for candidate in sorted(base.rglob("*")):
            if candidate.is_symlink():
                raise SuiteImportError(f"symlinks are forbidden in a snapshot: {candidate}")
            if not candidate.is_file():
                continue
            relative = candidate.relative_to(base).as_posix()
            if ".git" in PurePosixPath(relative).parts:
                raise SuiteImportError(f"git metadata is forbidden in a snapshot: {relative}")
            files[relative] = candidate.read_bytes()
        if not files:
            raise SuiteImportError(f"snapshot directory {repo_dir!r} is empty")
        return files

    def bind(self, instance: NativeTaskInstance, source: SourceManifest) -> NativeTaskDraft:
        """Attach the methodology record the manifest's provenance entitles the task to."""
        visible = {f"repo/{path}": data for path, data in sorted(instance.repo_files.items())}
        visible["issue.md"] = instance.problem_statement.encode("utf-8")
        leaks = check_instance_leakage(instance, visible)
        if leaks:
            raise SuiteImportError("; ".join(leaks))
        return NativeTaskDraft(
            instance=instance,
            methodology=self.methodology_record(instance, source),
            package_digest=instance_digest(instance),
            allowed_change_paths=tuple(sorted(instance.repo_files)),
        )

    def methodology_record(
        self, instance: NativeTaskInstance, source: SourceManifest
    ) -> MethodologyRecord:
        """The record for one instance: its label follows its provenance, not its shape."""
        native_metrics = ("fail_to_pass_resolution_with_pass_to_pass_maintenance",)
        if source.authored:
            # An authored fixture keeps the native record format and the native metric, but no
            # upstream dataset stands behind it, so it is labelled inspired (D-24-01). A fixture
            # that departs from the native evaluation rule is adapted and records the departure.
            if not instance.protocol_deviations:
                return MethodologyRecord(
                    suite_id=self.suite_id,
                    compatibility_level="inspired",
                    official_sources=(
                        "https://www.swebench.com/SWE-bench/reference/harness/",
                        "https://www.swebench.com/original.html",
                    ),
                    source_revision=source.dataset_revision,
                    input_rules=("issue statement plus the pre-fix repository snapshot",),
                    output_rules=("unified diff against the base commit",),
                    feedback_tools=("public test command output only",),
                    native_metrics=native_metrics,
                    license_expression=source.license_expression,
                )
            return MethodologyRecord(
                suite_id=self.suite_id,
                compatibility_level="adapted",
                official_sources=(
                    "https://www.swebench.com/SWE-bench/reference/harness/",
                    "https://www.swebench.com/multilingual.html",
                ),
                source_revision=source.dataset_revision,
                input_rules=("issue statement plus the pre-fix repository snapshot",),
                output_rules=("unified diff against the base commit",),
                feedback_tools=("public test command output only",),
                native_metrics=native_metrics,
                license_expression=source.license_expression,
                deviations=tuple(instance.protocol_deviations),
            )
        return MethodologyRecord(
            suite_id=self.suite_id,
            compatibility_level="native",
            official_sources=(str(source.source_url),),
            source_revision=source.dataset_revision,
            input_rules=("issue statement plus the pre-fix repository snapshot",),
            output_rules=("unified diff against the base commit",),
            feedback_tools=("public test command output only",),
            native_metrics=native_metrics,
            native_evaluator_revision=instance.version,
            upstream_source_url=str(source.source_url),
        )

    # ------------------------------------------------------------------------ protocol

    def protocol(self, draft: NativeTaskDraft) -> FamilyProtocol:
        """The frozen solve/grade contract for one instance."""
        instance = draft.instance
        return FamilyProtocol(
            instance_id=instance.instance_id,
            base_commit=instance.base_commit,
            solve_input_paths=tuple(sorted(draft.visible_files())),
            allowed_change_paths=tuple(draft.allowed_change_paths),
            test_command=instance.test_command or _default_test_command(instance),
            fail_to_pass=tuple(instance.fail_to_pass),
            pass_to_pass=tuple(instance.pass_to_pass),
        )

    # ---------------------------------------------------------------------- validation

    def validate_methodology(self, record: MethodologyRecord) -> ValidationReport:
        """Check a record against the register, its own label rules and the fixture rule."""
        issues: list[str] = []
        label = record.compatibility_level
        if label not in PERMITTED_LABELS:
            issues.append(
                f"label {label!r} is not permitted for the {SUITE_FAMILY} family; permitted "
                + f"labels are {', '.join(PERMITTED_LABELS)}"
            )
        register = self._family_register()
        if register is not None:
            allowed = register.get("label")
            if isinstance(allowed, list) and label not in tuple(allowed):
                issues.append(
                    f"label {label!r} is outside the recorded register labels "
                    + f"{tuple(str(item) for item in allowed)}"
                )
            expected_metric = register.get("native_metric")
            if isinstance(expected_metric, str) and expected_metric not in record.native_metrics:
                issues.append(f"record omits the registered native metric {expected_metric!r}")
        if label == "native" and not (record.upstream_source_url and record.source_revision):
            issues.append("a native label needs an upstream source URL and a pinned revision")
        if label == "inspired" and record.upstream_source_url:
            issues.append(
                "an inspired record must not carry an upstream source URL; that would claim "
                + "official provenance"
            )
        if label == "adapted" and not record.deviations:
            issues.append("an adapted record must record what was altered")
        if not record.native_metrics:
            issues.append("a record must declare the native metric it preserves")
        if not record.license_expression or record.license_expression == "unknown":
            issues.append("a record must declare a license expression")
        return ValidationReport(
            suite_id=record.suite_id,
            ok=not issues,
            checks=() if issues else record.validated_checks(),
            issues=tuple(issues),
        )

    def _family_register(self) -> Mapping[str, Any] | None:
        if self._deviations is None:
            return None
        families = self._deviations.get("families")
        if not isinstance(families, Mapping):
            return None
        entry = families.get(SUITE_FAMILY)
        return entry if isinstance(entry, Mapping) else None

    # ------------------------------------------------------------------ evaluation plan

    def evaluation_plan(self, draft: NativeTaskDraft, candidate_patch: str) -> dict[str, object]:
        """What grading will do with one candidate: overlay the test patch, run, grade."""
        enforce_patch_paths(candidate_patch, draft.allowed_change_paths)
        return {
            "instance_id": draft.instance.instance_id,
            "base_commit": draft.instance.base_commit,
            "apply_candidate_patch": True,
            "apply_test_patch": True,
            "test_command": self.protocol(draft).test_command,
            "expected_test_lists": draft.instance.gold_results(),
            "log_parser": draft.instance.log_parser,
            "eval_type": draft.instance.eval_type,
            "task_digest": draft.package_digest,
        }

    # ------------------------------------------------------------------ native metrics

    def native_metrics(
        self, result: NativeGradeResult, methodology_label: str
    ) -> NativeMetricExport:
        """Export the upstream measure under the task's public label.

        This is the only path that produces a native metric: it forwards the upstream evaluator's
        own counts and never recomputes a fraction from local test tallies. The export keeps gate
        and quality evidence in separate fields, so a PolyCodeBench correctness gate cannot stand
        in for the benchmark's resolution measure, or the reverse.
        """
        notes: tuple[str, ...] = ()
        if not result.resolved and not result.patch_applied:
            notes = (
                "the upstream evaluator produced no parseable result; that is not a zero "
                + "score and must not be replaced by a local test fraction",
            )
        return NativeMetricExport(
            suite_id=self.suite_id,
            methodology_label=methodology_label,
            metrics=dict(result.native_metrics),
            notes=notes,
        )

    # -------------------------------------------------------------------- patch output

    def patch_output(self, draft: NativeTaskDraft, candidate_patch: str) -> dict[str, object]:
        """The submission record for one candidate patch, bound to the frozen task."""
        enforce_patch_paths(candidate_patch, draft.allowed_change_paths)
        return {
            "instance_id": draft.instance.instance_id,
            "base_commit": draft.instance.base_commit,
            "output_kind": "patch",
            "candidate_digest": sha256_bytes(candidate_patch.encode("utf-8")),
            "task_digest": draft.package_digest,
            "changed_paths": list(patching.targets(candidate_patch)),
        }


def test_spec_for(draft: NativeTaskDraft) -> NativeTestSpec:
    """The upstream test spec this instance grades under."""
    return NativeTestSpec.from_instance(draft.instance)


def suite_package_digest(files: Mapping[str, bytes]) -> str:
    """A deterministic digest over a bundle: path -> byte digest, sorted."""
    return str(canonical_digest({path: sha256_bytes(data) for path, data in sorted(files.items())}))
