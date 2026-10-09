"""Load one frozen, code-only evaluation assignment from durable records."""

from __future__ import annotations

import hashlib
import io
import json
import stat
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

import yaml
from polycodebench_core.canonical import canonical_document_digest
from polycodebench_core.jobs import JobClaim
from polycodebench_core.models import Candidate, TaskVersion
from polycodebench_core.solve_contracts import PathForbidden, normalize_workspace_path
from polycodebench_evaluation.evaluator import baseline_from_package
from polycodebench_evaluation.plan_runner import digest_files
from polycodebench_persistence.artifacts import ArtifactRepository
from polycodebench_persistence.models import (
    artifact,
    attempt,
    config_document,
    evaluation,
    task_version,
)
from polycodebench_persistence.models import candidate as candidate_table
from polycodebench_plugins_api import (
    ExecutableLanguagePlugin,
    FrozenTask,
    PluginAllowlist,
    TaskDraft,
    load_language_plugin,
)
from polycodebench_scoring.policy import FrozenScoringPolicy
from polycodebench_services.task_packages import TaskPackageManifest
from sqlalchemy import select
from sqlalchemy.engine import Engine

from polycodebench_orchestration.grading.candidates import decode_file_candidate
from polycodebench_orchestration.solve.types import workspace_from_visible_archive

MAX_TASK_BUNDLE_BYTES = 256 * 1024**2
MAX_TASK_BUNDLE_FILES = 20_000


class EvaluationAssignmentRejected(ValueError):
    """The queued evaluation does not resolve to complete, digest-bound task material."""


@dataclass(frozen=True)
class EvaluationAssignment:
    evaluation_id: UUID
    attempt_id: UUID
    run_id: UUID
    oracle_digest: str
    policy_config_id: UUID
    task: TaskVersion
    view: FrozenTask
    plugin: ExecutableLanguagePlugin
    candidate: Candidate
    candidate_files: Mapping[str, bytes]
    visible_files: Mapping[str, bytes]
    baseline_files: Mapping[str, bytes]
    overlay_files: Mapping[str, bytes]
    config_files: Mapping[str, bytes]
    allowed_paths: tuple[str, ...]
    policy: FrozenScoringPolicy | None = None
    policy_digest: str | None = None
    evaluation_state: str = "running"
    evaluation_gate: str = "unknown"
    evidence_manifest_id: UUID | None = None


class DatabaseEvaluationAssignmentLoader:
    """Resolve a claim to immutable task, candidate, and plugin-controlled grading inputs."""

    def __init__(
        self,
        engine: Engine,
        artifacts: ArtifactRepository,
        plugin_allowlist: PluginAllowlist,
        *,
        allow_completed: bool = False,
    ) -> None:
        self._engine = engine
        self._artifacts = artifacts
        self._plugins = plugin_allowlist
        self._allow_completed = allow_completed

    def __call__(self, claim: JobClaim) -> EvaluationAssignment:
        if claim.scope_type != "evaluation":
            raise EvaluationAssignmentRejected("evaluation stages require an evaluation scope")
        return self.load(claim.scope_id)

    def load(self, evaluation_id: UUID) -> EvaluationAssignment:
        """Resolve an evaluation by identity for an evaluator or a post-run scorer."""
        policy_artifact = artifact.alias("frozen_scoring_policy_artifact")
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(
                        evaluation.c.id.label("evaluation_id"),
                        evaluation.c.attempt_id,
                        evaluation.c.policy_config_id,
                        evaluation.c.oracle_digest,
                        evaluation.c.state.label("evaluation_state"),
                        evaluation.c.gate.label("evaluation_gate"),
                        evaluation.c.evidence_manifest_id,
                        attempt.c.run_id,
                        attempt.c.task_version_id,
                        attempt.c.sample_index,
                        attempt.c.state.label("attempt_state"),
                        attempt.c.candidate_artifact_id,
                        task_version.c.digest.label("task_digest"),
                        task_version.c.document.label("task_document"),
                        candidate_table.c.id.label("candidate_id"),
                        candidate_table.c.payload_digest,
                        candidate_table.c.submission_kind,
                        candidate_table.c.payload,
                        candidate_table.c.canonical_artifact_id,
                        candidate_table.c.frozen_at,
                        config_document.c.kind.label("policy_kind"),
                        config_document.c.digest.label("policy_digest"),
                        config_document.c.document.label("policy_document"),
                        policy_artifact.c.status.label("policy_artifact_status"),
                        policy_artifact.c.visibility.label("policy_artifact_visibility"),
                        policy_artifact.c.encryption_domain.label("policy_artifact_domain"),
                        policy_artifact.c.content_digest.label("policy_artifact_digest"),
                    )
                    .select_from(
                        evaluation.join(
                            config_document,
                            config_document.c.id == evaluation.c.policy_config_id,
                        )
                        .join(
                            policy_artifact,
                            policy_artifact.c.id == config_document.c.canonical_artifact_id,
                        )
                        .join(attempt, attempt.c.id == evaluation.c.attempt_id)
                        .join(task_version, task_version.c.id == attempt.c.task_version_id)
                        .join(
                            candidate_table,
                            (candidate_table.c.attempt_id == attempt.c.id)
                            & (candidate_table.c.revision == 1),
                        )
                    )
                    .where(evaluation.c.id == evaluation_id)
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise EvaluationAssignmentRejected("evaluation or its frozen candidate is missing")
        allowed_states = {"running", "ready", "failed"} if self._allow_completed else {"running"}
        if row["evaluation_state"] not in allowed_states or row["attempt_state"] != "completed":
            raise EvaluationAssignmentRejected("evaluation does not own a completed solve attempt")
        if (
            row["policy_kind"] != "frozen_scoring_policy"
            or row["policy_artifact_status"] != "verified"
            or row["policy_artifact_visibility"] != "internal"
            or row["policy_artifact_domain"] != "worker-config"
            or row["policy_artifact_digest"] != row["policy_digest"]
        ):
            raise EvaluationAssignmentRejected("frozen scoring policy artifact is not verified")
        try:
            policy = FrozenScoringPolicy.model_validate_json(
                json.dumps(row["policy_document"]), strict=True
            )
        except (TypeError, ValueError):
            raise EvaluationAssignmentRejected(
                "frozen scoring policy document is invalid"
            ) from None
        if canonical_document_digest(policy) != row["policy_digest"]:
            raise EvaluationAssignmentRejected("frozen scoring policy digest is not canonical")
        if self._allow_completed and row["evidence_manifest_id"] is None:
            raise EvaluationAssignmentRejected("completed evaluation has no evidence artifact")
        if (
            row["candidate_artifact_id"] is None
            or row["candidate_artifact_id"] != row["canonical_artifact_id"]
            or row["submission_kind"] != "files"
            or row["payload"].get("validity") != "valid"
            or row["frozen_at"] is None
        ):
            raise EvaluationAssignmentRejected("attempt has no valid frozen file candidate")

        task = TaskVersion.model_validate_json(json.dumps(row["task_document"]), strict=True)
        if canonical_document_digest(task) != row["task_digest"]:
            raise EvaluationAssignmentRejected("frozen task document digest is not canonical")
        if canonical_document_digest(task.oracle) != row["oracle_digest"]:
            raise EvaluationAssignmentRejected(
                "evaluation oracle identity differs from the frozen task"
            )
        if task.output_contract.submission_kind != "files":
            raise EvaluationAssignmentRejected("this evaluator only accepts frozen file tasks")
        plugin_id = str(task.runtime.language_plugin_id)
        try:
            allowed = self._plugins.get(plugin_id)
            plugin = cast(ExecutableLanguagePlugin, load_language_plugin(self._plugins, plugin_id))
        except Exception as error:
            raise EvaluationAssignmentRejected("task language plugin is unavailable") from error
        if (
            task.runtime.language_plugin_version != allowed.plugin_version
            or plugin.language_id != plugin_id
        ):
            raise EvaluationAssignmentRejected("task language plugin identity is not allowlisted")

        visible_meta, visible_archive = self._read_bundle(
            UUID(str(task.visible_bundle.artifact_id)),
            expected_digest=str(task.visible_bundle.digest),
            expected_visibility=str(task.visible_bundle.visibility),
            expected_prefix="visible/",
            special_names=frozenset({"visible-manifest.json"}),
        )
        hidden_meta, hidden_archive = self._read_bundle(
            UUID(str(task.hidden_bundle.artifact_id)),
            expected_digest=str(task.hidden_bundle.digest),
            expected_visibility="hidden",
            expected_prefixes=("hidden/", "admission/"),
            # The importer always writes this one file at the archive root (task_packages.py).
            special_names=frozenset({"administrative-manifest.yaml"}),
        )
        if (
            visible_meta["visibility"] != str(task.visible_bundle.visibility)
            or hidden_meta["visibility"] != "hidden"
        ):
            raise EvaluationAssignmentRejected(
                "task bundle visibility does not match its reference"
            )
        _verify_admin_manifest(hidden_archive, task)

        files = {**visible_archive, **hidden_archive}
        view = freeze_task_view(task, files, str(row["task_digest"]), plugin)

        candidate_meta, candidate_body = self._artifacts.read_verified(
            UUID(str(row["canonical_artifact_id"]))
        )
        if (
            candidate_meta["status"] != "verified"
            or candidate_meta["visibility"] != "internal"
            or candidate_meta["content_digest"] != row["payload_digest"]
        ):
            raise EvaluationAssignmentRejected("candidate artifact metadata does not match its row")
        candidate_files = decode_file_candidate(
            candidate_body,
            expected_artifact_digest=str(row["payload_digest"]),
            contract=task.output_contract,
        )
        candidate = Candidate(
            kind="candidate",
            schema_version=1,
            candidate_id=str(row["candidate_id"]),
            run_id=str(row["run_id"]),
            task_id=task.task_id,
            task_version=task.version,
            sample_index=int(row["sample_index"]),
            submission_kind="source_bundle",
            payload_digest=digest_files(candidate_files),
            artifact_ids=[str(row["canonical_artifact_id"])],
            frozen_at=row["frozen_at"].isoformat().replace("+00:00", "Z"),
        )

        overlay_files = self._declared_overlay_files(view, hidden_archive)
        trusted_inputs = getattr(plugin, "trusted_inputs", None)
        config_files: Mapping[str, bytes] = {}
        if callable(trusted_inputs):
            config_files = trusted_inputs(files, view)
        if not isinstance(config_files, Mapping) or any(
            not isinstance(path, str) or not isinstance(data, bytes)
            for path, data in config_files.items()
        ):
            raise EvaluationAssignmentRejected("plugin returned invalid trusted config inputs")

        visible_workspace = workspace_from_visible_archive(visible_meta["body"])
        return EvaluationAssignment(
            evaluation_id=UUID(str(row["evaluation_id"])),
            attempt_id=UUID(str(row["attempt_id"])),
            run_id=UUID(str(row["run_id"])),
            oracle_digest=str(row["oracle_digest"]),
            policy_config_id=UUID(str(row["policy_config_id"])),
            task=task,
            view=view,
            plugin=plugin,
            candidate=candidate,
            candidate_files=candidate_files,
            visible_files=visible_workspace,
            baseline_files=baseline_from_package(visible_archive),
            overlay_files=overlay_files,
            config_files=config_files,
            allowed_paths=tuple(str(path) for path in task.output_contract.allowed_paths),
            policy=policy,
            policy_digest=str(row["policy_digest"]),
            evaluation_state=str(row["evaluation_state"]),
            evaluation_gate=str(row["evaluation_gate"]),
            evidence_manifest_id=(
                UUID(str(row["evidence_manifest_id"]))
                if row["evidence_manifest_id"] is not None
                else None
            ),
        )

    def _read_bundle(
        self,
        artifact_id: UUID,
        *,
        expected_digest: str,
        expected_visibility: str,
        expected_prefix: str | None = None,
        expected_prefixes: tuple[str, ...] = (),
        special_names: frozenset[str] = frozenset(),
    ) -> tuple[dict[str, Any], dict[str, bytes]]:
        metadata, body = self._artifacts.read_verified(artifact_id)
        if (
            metadata["status"] != "verified"
            or metadata["visibility"] != expected_visibility
            or metadata["content_digest"] != expected_digest
            or "sha256:" + hashlib.sha256(body).hexdigest() != expected_digest
            or len(body) > MAX_TASK_BUNDLE_BYTES
        ):
            raise EvaluationAssignmentRejected("task bundle failed visibility or digest checks")
        try:
            files = _archive_files(body)
        except (ValueError, OSError, zipfile.BadZipFile, RuntimeError) as error:
            raise EvaluationAssignmentRejected(
                "task bundle is not a safe bounded archive"
            ) from error
        prefixes: tuple[str, ...]
        if expected_prefix is not None:
            prefixes = (expected_prefix,)
        else:
            prefixes = expected_prefixes
        if any(
            not (path in special_names or any(path.startswith(prefix) for prefix in prefixes))
            for path in files
        ):
            raise EvaluationAssignmentRejected("task bundle contains a file outside its namespace")
        return {**metadata, "body": body}, files

    @staticmethod
    def _declared_overlay_files(
        view: FrozenTask, hidden_files: Mapping[str, bytes]
    ) -> dict[str, bytes]:
        paths: set[str] = set()
        groups = view.inventory.get("groups", ())
        if isinstance(groups, list | tuple):
            for group in groups:
                if not isinstance(group, Mapping):
                    continue
                group_files = group.get("files", ())
                if isinstance(group_files, list | tuple):
                    paths.update(path for path in group_files if isinstance(path, str))
        quality = view.quality
        performance = quality.get("performance") if isinstance(quality, Mapping) else None
        if isinstance(performance, Mapping):
            workload = performance.get("workload_file")
            if isinstance(workload, str):
                paths.add(workload)
        overlays: dict[str, bytes] = {}
        for path in sorted(paths):
            stored_path = path
            if stored_path not in hidden_files and not path.startswith(("hidden/", "admission/")):
                stored_path = "hidden/" + path
            if stored_path not in hidden_files:
                raise EvaluationAssignmentRejected(
                    "declared task overlay is absent from the hidden bundle"
                )
            overlays[path] = hidden_files[stored_path]
        return overlays


def freeze_task_view(
    task: TaskVersion,
    package_files: Mapping[str, bytes],
    task_digest: str,
    plugin: ExecutableLanguagePlugin,
) -> FrozenTask:
    """Ask the allowlisted plugin for its view, then bind every shared field to the DB record."""
    draft = TaskDraft(
        task_id=task.task_id,
        primary_language=task.primary_language,
        manifest={
            "acceptance": task.acceptance.model_dump(mode="json"),
            "quality_plan": task.quality_plan.model_dump(mode="json"),
            "runtime": task.runtime.model_dump(mode="json"),
        },
        files=dict(package_files),
    )
    try:
        view = plugin.freeze_view(draft, task_digest, task.version)
    except Exception as error:
        raise EvaluationAssignmentRejected("frozen task material cannot be interpreted") from error
    if (
        view.task_id != task.task_id
        or view.task_version != task.version
        or view.task_digest != task_digest
        or view.primary_language != task.primary_language
        or view.image_digest != task.runtime.image_digest
        or view.required_outputs != tuple(task.acceptance.required_outputs)
        or view.protected_paths != tuple(task.acceptance.protected_paths)
        or view.required_test_group_ids != tuple(task.acceptance.required_test_group_ids)
        or view.required_analyzers != tuple(task.quality_plan.required_analyzers)
        or view.applicable_dimensions != tuple(task.quality_plan.applicable_dimensions)
    ):
        raise EvaluationAssignmentRejected("plugin view differs from the frozen task record")
    return view


def _archive_files(body: bytes) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    total_bytes = 0
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_TASK_BUNDLE_FILES:
            raise ValueError("task archive has too many entries")
        for entry in entries:
            name = entry.filename
            if "\\" in name or "\x00" in name:
                raise ValueError("task archive has an unsafe path")
            try:
                path = normalize_workspace_path(name.rstrip("/"))
            except PathForbidden as error:
                raise ValueError("task archive has an unsafe path") from error
            mode = (entry.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise ValueError("task archive contains a symbolic link")
            if entry.is_dir():
                continue
            if mode not in {0, stat.S_IFREG} or path != name:
                raise ValueError("task archive contains a noncanonical or nonregular path")
            if path in files or entry.flag_bits & 1 or entry.file_size > MAX_TASK_BUNDLE_BYTES:
                raise ValueError("task archive is duplicated, encrypted, or oversized")
            total_bytes += entry.file_size
            if total_bytes > MAX_TASK_BUNDLE_BYTES:
                raise ValueError("task archive exceeds its expanded size bound")
            data = archive.read(entry)
            if len(data) != entry.file_size:
                raise ValueError("task archive entry size changed while reading")
            files[path] = data
    if not files:
        raise ValueError("task archive is empty")
    return files


def _verify_admin_manifest(hidden_files: Mapping[str, bytes], task: TaskVersion) -> None:
    raw = hidden_files.get("administrative-manifest.yaml")
    if raw is None or len(raw) > 1_048_576:
        raise EvaluationAssignmentRejected("hidden task bundle lacks its frozen admin manifest")
    try:
        document_text = raw.decode("utf-8", errors="strict")
        if any(
            isinstance(token, (yaml.tokens.AnchorToken, yaml.tokens.AliasToken))
            for token in yaml.scan(document_text)
        ):
            raise ValueError("YAML aliases are forbidden")
        decoded = yaml.load(document_text, Loader=_UniqueKeyLoader)
        manifest = TaskPackageManifest.model_validate_json(json.dumps(decoded))
    except (UnicodeError, ValueError, yaml.YAMLError):
        raise EvaluationAssignmentRejected("hidden task admin manifest is invalid") from None

    identity = manifest.task
    if (
        identity.version != task.version
        or identity.track != task.track
        or identity.family != task.family
        or identity.primary_language != task.primary_language
        or tuple(identity.secondary_languages) != tuple(task.secondary_languages)
        or identity.cluster_id != task.cluster_id
        or identity.difficulty != task.difficulty
        or identity.stratum_id != task.stratum_id
        or identity.output_contract_digest != task.output_contract_digest
        or manifest.output_contract != task.output_contract
        or manifest.source != task.source
        or manifest.runtime != task.runtime
        or manifest.acceptance != task.acceptance
        or manifest.quality_plan != task.quality_plan
        or manifest.oracle != task.oracle
        or manifest.protocol_constraints != task.protocol_constraints
    ):
        raise EvaluationAssignmentRejected(
            "hidden task admin manifest differs from its frozen database record"
        )


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate keys in versioned task documents."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[object, object]:
        self.flatten_mapping(node)
        mapping: dict[object, object] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)  # type: ignore[no-untyped-call]
            if key in mapping:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"duplicate key {key!r}",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)  # type: ignore[no-untyped-call]
        return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _UniqueKeyLoader.construct_mapping,
)


__all__ = [
    "DatabaseEvaluationAssignmentLoader",
    "EvaluationAssignment",
    "EvaluationAssignmentRejected",
    "_archive_files",
    "freeze_task_view",
    "_verify_admin_manifest",
]
