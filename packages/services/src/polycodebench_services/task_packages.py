"""Safe task-package ingestion and local authored-fixture admission."""

from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import zipfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Literal

import yaml  # type: ignore[import-untyped]
from polycodebench_core.canonical import canonical_json_bytes, sha256_bytes
from polycodebench_core.identity import validate_relative_path
from polycodebench_core.models import (
    ProtocolConstraints,
    TaskAcceptance,
    TaskOracle,
    TaskOutputContract,
    TaskQualityPlan,
    TaskRuntime,
    TaskSource,
)
from polycodebench_core.tasksets import package_snapshot_digest
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from yaml.constructor import ConstructorError  # type: ignore[import-untyped]

_SECRET_MARKERS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
)
_HIDDEN_ROOTS = {"hidden", "admission"}


class _UniqueKeyLoader(yaml.SafeLoader):  # type: ignore[misc]
    pass


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[object, object]:
    loader.flatten_mapping(node)
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ConstructorError(
                "while parsing task manifest",
                node.start_mark,
                f"duplicate key: {key!r}",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class SourceRights(StrictModel):
    license_expression: str = Field(min_length=1, max_length=256)
    source_url: str | None = Field(default=None, max_length=2048)
    source_revision: str = Field(min_length=1, max_length=256)
    redistribution_status: Literal["cleared", "restricted", "unknown", "authored_fixture"]
    attribution: str = Field(min_length=1, max_length=2048)
    review_reference: str | None = Field(default=None, max_length=2048)


class TaskIdentity(StrictModel):
    task_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$")
    version: int = Field(gt=0)
    track: Literal["A", "B"]
    family: Literal[
        "bug_hunt",
        "codegen",
        "repo_repair",
        "repo_task",
        "self_repair",
        "repo_qa",
        "output_prediction",
        "test_prediction",
    ]
    primary_language: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$")
    secondary_languages: tuple[str, ...] = ()
    cluster_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$")
    difficulty: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$")
    stratum_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$")
    output_contract_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    methodology_label: Literal["native", "adapted", "inspired", "independent"]


class FixtureCase(StrictModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    variant: Literal["reference", "faulty", "alternative"]
    solution_path: str
    input_path: str
    expected_output_path: str


class TaskPackageManifest(StrictModel):
    schema_version: Literal[1]
    kind: Literal["task_package"]
    task: TaskIdentity
    output_contract: TaskOutputContract
    source: TaskSource
    rights: SourceRights
    runtime: TaskRuntime
    acceptance: TaskAcceptance
    quality_plan: TaskQualityPlan
    oracle: TaskOracle
    protocol_constraints: ProtocolConstraints
    visible_files: tuple[str, ...] = Field(min_length=1)
    hidden_files: tuple[str, ...] = Field(min_length=1)
    fixtures: tuple[FixtureCase, ...] = Field(min_length=3)

    def validate_paths(self) -> TaskPackageManifest:
        from polycodebench_core.canonical import canonical_document_digest

        if canonical_document_digest(self.output_contract) != self.task.output_contract_digest:
            raise ValueError("manifest output contract digest does not match its contract")
        if not set(self.acceptance.required_outputs) <= set(self.output_contract.allowed_paths):
            raise ValueError("required outputs must be included in the output contract allowlist")
        for path in (*self.visible_files, *self.hidden_files):
            validate_relative_path(path)
            parts = PurePosixPath(path).parts
            if ".git" in parts:
                raise ValueError("repository Git metadata is forbidden in task packages")
        if len(set(self.visible_files)) != len(self.visible_files):
            raise ValueError("visible_files contains duplicate paths")
        if len(set(self.hidden_files)) != len(self.hidden_files):
            raise ValueError("hidden_files contains duplicate paths")
        if set(self.visible_files) & set(self.hidden_files):
            raise ValueError("visible and hidden paths must be disjoint")
        if any(not path.startswith("visible/") for path in self.visible_files):
            raise ValueError("visible files must be beneath visible/")
        if any(
            not any(path.startswith(f"{root}/") for root in _HIDDEN_ROOTS)
            for path in self.hidden_files
        ):
            raise ValueError("hidden files must be beneath hidden/ or admission/")
        fixture_paths = {
            path
            for fixture in self.fixtures
            for path in (fixture.solution_path, fixture.input_path, fixture.expected_output_path)
        }
        if not fixture_paths <= set(self.hidden_files) | set(self.visible_files):
            raise ValueError("fixture references a file not declared in the package")
        for fixture in self.fixtures:
            if fixture.solution_path not in self.hidden_files:
                raise ValueError("fixture solutions must remain in the hidden bundle")
            if fixture.expected_output_path not in self.hidden_files:
                raise ValueError("fixture expected outputs must remain in the hidden bundle")
        variants = [case.variant for case in self.fixtures]
        if set(variants) != {"reference", "faulty", "alternative"}:
            raise ValueError("fixtures must include reference, faulty, and alternative variants")
        if self.rights.redistribution_status not in {"cleared", "authored_fixture"}:
            raise ValueError("source rights are not cleared for admission")
        if self.task.methodology_label == "native" and self.rights.source_url is None:
            raise ValueError("native methodology tasks require an upstream source URL")
        return self


class ImportedTaskPackage(StrictModel):
    package_digest: str
    manifest_digest: str
    visible_digest: str
    hidden_digest: str
    visible_archive: bytes
    hidden_archive: bytes
    manifest: TaskPackageManifest
    source_root: Path

    model_config = ConfigDict(
        arbitrary_types_allowed=True, extra="forbid", strict=True, frozen=True
    )


class TaskPackageImporter:
    """Builds physically separate, deterministic visible and hidden archives."""

    def load(self, package_root: Path) -> tuple[TaskPackageManifest, bytes]:
        root = package_root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("task package root must be a directory")
        manifest_path = root / "manifest.yaml"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise ValueError("task package must contain a regular manifest.yaml")
        raw = manifest_path.read_bytes()
        if len(raw) > 1_048_576:
            raise ValueError("task manifest exceeds 1 MiB")
        try:
            text = raw.decode("utf-8", errors="strict")
            if any(
                isinstance(token, (yaml.tokens.AnchorToken, yaml.tokens.AliasToken))
                for token in yaml.scan(text)
            ):
                raise ValueError("YAML anchors and aliases are not allowed")
            decoded = yaml.load(text, Loader=_UniqueKeyLoader)
            manifest = TaskPackageManifest.model_validate_json(
                json.dumps(decoded, ensure_ascii=False, allow_nan=False)
            )
        except (UnicodeDecodeError, yaml.YAMLError, ValidationError) as error:
            raise ValueError("task manifest is invalid") from error
        manifest.validate_paths()
        self._validate_tree(root, manifest)
        return manifest, raw

    def import_package(self, package_root: Path) -> ImportedTaskPackage:
        root = package_root.resolve(strict=True)
        manifest, raw_manifest = self.load(root)
        visible_files = {path: self._read_file(root, path) for path in manifest.visible_files}
        hidden_files = {path: self._read_file(root, path) for path in manifest.hidden_files}
        self._scan_disclosure(visible_files, hidden_files)
        visible_manifest = {
            "schema_version": 1,
            "kind": "visible_task_manifest",
            "task_id": manifest.task.task_id,
            "version": manifest.task.version,
            "track": manifest.task.track,
            "family": manifest.task.family,
            "primary_language": manifest.task.primary_language,
            "secondary_languages": list(manifest.task.secondary_languages),
            "difficulty": manifest.task.difficulty,
            "output_contract_digest": manifest.task.output_contract_digest,
            "output_contract": manifest.output_contract.model_dump(mode="json"),
            "methodology_label": manifest.task.methodology_label,
        }
        visible_files["visible-manifest.json"] = canonical_json_bytes(visible_manifest)
        hidden_files["administrative-manifest.yaml"] = raw_manifest
        visible_archive = self._archive(visible_files)
        hidden_archive = self._archive(hidden_files)
        manifest_digest = sha256_bytes(raw_manifest)
        visible_digest = sha256_bytes(visible_archive)
        hidden_digest = sha256_bytes(hidden_archive)
        return ImportedTaskPackage(
            package_digest=package_snapshot_digest(manifest_digest, visible_digest, hidden_digest),
            manifest_digest=manifest_digest,
            visible_digest=visible_digest,
            hidden_digest=hidden_digest,
            visible_archive=visible_archive,
            hidden_archive=hidden_archive,
            manifest=manifest,
            source_root=root,
        )

    @staticmethod
    def _validate_tree(root: Path, manifest: TaskPackageManifest) -> None:
        declared = set(manifest.visible_files) | set(manifest.hidden_files)
        for path in declared:
            current = root
            for part in PurePosixPath(path).parts:
                current = current / part
                if current.is_symlink():
                    raise ValueError(f"symlinks are forbidden in task snapshots: {path}")
            resolved = current.resolve(strict=True)
            if root not in resolved.parents or not stat.S_ISREG(resolved.stat().st_mode):
                raise ValueError(f"task package path is not a contained regular file: {path}")
        for area in ("visible", "hidden", "admission"):
            area_path = root / area
            if not area_path.exists():
                continue
            if area_path.is_symlink() or area_path.is_junction():
                raise ValueError(f"symlinks are forbidden in task snapshots: {area}")
            for candidate_path in area_path.rglob("*"):
                relative = candidate_path.relative_to(root).as_posix()
                if candidate_path.is_symlink() or candidate_path.is_junction():
                    raise ValueError(f"symlinks are forbidden in task snapshots: {relative}")
                if candidate_path.is_file() and relative not in declared:
                    raise ValueError(f"undeclared task file is not admitted: {relative}")

    @staticmethod
    def _read_file(root: Path, path: str) -> bytes:
        return (root / Path(*PurePosixPath(path).parts)).read_bytes()

    @staticmethod
    def _scan_disclosure(
        visible_files: Mapping[str, bytes], hidden_files: Mapping[str, bytes]
    ) -> None:
        visible_body = b"\n".join(visible_files.values())
        visible_paths = set(visible_files)
        for hidden_path, hidden_body in hidden_files.items():
            digest = hashlib.sha256(hidden_body).hexdigest().encode("ascii")
            if hidden_path.encode("utf-8") in visible_body or digest in visible_body:
                raise ValueError("visible data references a hidden path or digest")
            if hidden_body and hidden_body in visible_body:
                raise ValueError("hidden file bytes were copied into the visible bundle")
        for body in visible_files.values():
            if any(marker.search(body) for marker in _SECRET_MARKERS):
                raise ValueError("visible bundle contains a likely credential or private key")
        if visible_paths & set(hidden_files):
            raise ValueError("visible and hidden bundle paths overlap")

    @staticmethod
    def _archive(files: Mapping[str, bytes]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
            for path in sorted(files):
                info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                info.compress_type = zipfile.ZIP_STORED
                archive.writestr(info, files[path])
        return buffer.getvalue()


def post_cutoff_eligibility(
    *,
    earliest_public_at: str | None,
    date_confidence: str,
    cutoff_at: str | None,
    cutoff_confidence: str,
) -> tuple[bool, str]:
    """Use exposure dates only; curation time never establishes post-cutoff status."""
    if cutoff_at is None or cutoff_confidence == "unknown":
        return False, "model_cutoff_unknown"
    if earliest_public_at is None or date_confidence != "verified":
        return False, "earliest_public_exposure_unknown_or_unverified"
    from datetime import datetime

    exposure = datetime.fromisoformat(earliest_public_at.replace("Z", "+00:00"))
    cutoff = datetime.fromisoformat(cutoff_at.replace("Z", "+00:00"))
    if exposure > cutoff:
        return True, "earliest_public_exposure_after_declared_cutoff"
    return False, "earliest_public_exposure_not_after_cutoff"
