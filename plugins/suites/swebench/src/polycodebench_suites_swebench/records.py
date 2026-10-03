"""SWE-bench-style repository-repair suite contracts (Prompt 24, PCB-24-1/PCB-24-4).

The shapes here are the *native record* vocabulary of the official SWE-bench harness: an
instance names a repository, an immutable base commit, an issue statement, the gold patch and
test patch, and the two expected test lists ``FAIL_TO_PASS`` / ``PASS_TO_PASS`` that the upstream
grading code turns into the resolution measure. Nothing here recomputes that measure - see
``polycodebench_suites_swebench.grading`` for the call into the pinned upstream evaluator.

Three labels are in play and each one is checked against the deviations register
(``config/methodology/deviations-v1.yaml``) and ``docs/methodology/swebench.md``:

``native``
    Reserved for an official upstream benchmark record: an imported instance with a real upstream
    source URL and a pinned dataset revision. Prompt 24 has no cleared dataset, so no task this
    repository ships may claim it.
``inspired``
    An authored fixture that follows the repository-patch pattern and keeps the native record
    format and the native fail-to-pass/pass-to-pass evaluation. This is what the native-*compatible*
    fixtures of PCB-24-4 are.
``adapted``
    The same shape with a deliberately altered rule - a port to another language, or a modified
    test/patch rule - so its metric is no longer the native one and must be reported separately.

See ``decisions.md`` D-24-01 for why "native-compatible" resolves to ``inspired`` rather than
``native``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from polycodebench_core.models import RelativePath, Slug
from polycodebench_plugins_api import PluginModel
from pydantic import Field, model_validator

from polycodebench_suites_swebench import patching
from polycodebench_suites_swebench.patching import PatchRejected

FAIL_TO_PASS = "FAIL_TO_PASS"
PASS_TO_PASS = "PASS_TO_PASS"
FAIL_TO_FAIL = "FAIL_TO_FAIL"
PASS_TO_FAIL = "PASS_TO_FAIL"

#: The upstream grading vocabulary, keyed by the names ``swebench.harness.grading`` uses.
TEST_LIST_KEYS = (FAIL_TO_PASS, PASS_TO_PASS, FAIL_TO_FAIL, PASS_TO_FAIL)

CompatibilityLevel = Literal["native", "adapted", "inspired", "independent"]
EvalType = Literal["pass_and_fail", "fail_only"]


class NativeRecordError(ValueError):
    """An imported instance record is not a usable native task record."""


class MethodologyRecord(PluginModel):
    """The public methodology record for one imported suite (Technical Spec 17.1).

    ``compatibility_level`` is the public label. ``native`` additionally requires an upstream
    source URL and an official dataset revision: an authored fixture that merely follows the
    pattern is ``inspired``, never ``native``.
    """

    kind: Literal["methodology_record"] = "methodology_record"
    suite_id: Slug
    family: Literal["swebench"] = "swebench"
    compatibility_level: CompatibilityLevel
    official_sources: tuple[str, ...] = ()
    source_revision: str | None = None
    input_rules: tuple[str, ...] = ()
    output_rules: tuple[str, ...] = ()
    feedback_tools: tuple[str, ...] = ()
    native_metrics: tuple[str, ...] = ()
    license_expression: str | None = None
    deviations: tuple[str, ...] = ()
    native_evaluator_revision: str | None = None
    upstream_source_url: str | None = None

    @model_validator(mode="after")
    def native_requires_upstream_identity(self) -> MethodologyRecord:
        if self.compatibility_level == "native" and not (
            self.upstream_source_url and self.source_revision
        ):
            raise ValueError(
                "a native methodology record needs an upstream source URL and a pinned "
                + "source revision; authored fixtures are labelled 'inspired'"
            )
        if self.compatibility_level == "adapted" and not self.deviations:
            raise ValueError("an adapted record must record what was altered")
        return self

    def validated_checks(self) -> tuple[str, ...]:
        """Check ids this record satisfies; the import fails closed on any missing one."""
        checks = ["official-sources", "input-output-rules", "native-metrics", "license"]
        if self.compatibility_level == "adapted":
            checks.append("deviations-recorded")
        if self.compatibility_level == "native":
            checks.append("upstream-identity")
        return tuple(checks)


class NativeTaskInstance(PluginModel):
    """One SWE-bench-style instance: the record shape the upstream evaluator understands."""

    kind: Literal["native_task_instance"] = "native_task_instance"
    instance_id: str = Field(min_length=1, max_length=256)
    repo: str = Field(min_length=1, max_length=256)
    base_commit: str = Field(pattern=r"^[0-9a-f]{7,64}$")
    problem_statement: str = Field(min_length=1)
    version: str = Field(min_length=1, max_length=64)
    created_at: str | None = None
    environment_setup_commit: str | None = None
    test_patch: str = ""
    gold_patch: str = ""
    fail_to_pass: tuple[str, ...] = ()
    pass_to_pass: tuple[str, ...] = ()
    fail_to_fail: tuple[str, ...] = ()
    pass_to_fail: tuple[str, ...] = ()
    repo_files: Mapping[RelativePath, bytes] = Field(default_factory=dict)
    image_digest: str | None = None
    eval_type: EvalType = "pass_and_fail"
    log_parser: str = "parse_log_pytest"
    #: Deliberate departures from the native evaluation rules, in the words of the port. A
    #: non-empty tuple makes the record ``adapted``: its resolution measure is then not the native
    #: one, and its score must never be mixed with native scores (see D-24-01).
    protocol_deviations: tuple[str, ...] = ()
    #: The command whose log the declared ``log_parser`` reads. Frozen with the record so the
    #: protocol and the grading overlay cannot name different commands for the same task.
    test_command: str = ""

    @model_validator(mode="after")
    def lists_are_disjoint(self) -> NativeTaskInstance:
        if not set(self.fail_to_pass):
            raise ValueError("an instance needs at least one FAIL_TO_PASS test")
        overlap = set(self.fail_to_pass) & set(self.pass_to_pass)
        if overlap:
            raise ValueError(f"FAIL_TO_PASS and PASS_TO_PASS share {sorted(overlap)}")
        if not self.repo_files:
            raise ValueError("an instance needs its immutable repository snapshot")
        return self

    def gold_results(self) -> dict[str, list[str]]:
        return {
            FAIL_TO_PASS: list(self.fail_to_pass),
            PASS_TO_PASS: list(self.pass_to_pass),
            FAIL_TO_FAIL: list(self.fail_to_fail),
            PASS_TO_FAIL: list(self.pass_to_fail),
        }


class NativeTestSpec(PluginModel):
    """The subset of upstream ``swebench.types.TestSpec`` that grading actually reads."""

    kind: Literal["native_test_spec"] = "native_test_spec"
    instance_id: str = Field(min_length=1, max_length=256)
    repo: str = Field(min_length=1, max_length=256)
    version: str = Field(min_length=1, max_length=64)
    image: str = Field(min_length=1, max_length=256)
    fail_to_pass: tuple[str, ...] = Field(min_length=1)
    pass_to_pass: tuple[str, ...] = ()
    fail_to_fail: tuple[str, ...] = ()
    pass_to_fail: tuple[str, ...] = ()
    log_parser: str = "parse_log_pytest"
    eval_type: EvalType = "pass_and_fail"

    @classmethod
    def from_instance(cls, instance: NativeTaskInstance) -> NativeTestSpec:
        return cls(
            instance_id=instance.instance_id,
            repo=instance.repo,
            version=instance.version,
            image=instance.image_digest or f"{instance.repo}@{instance.base_commit}",
            fail_to_pass=instance.fail_to_pass,
            pass_to_pass=instance.pass_to_pass,
            fail_to_fail=instance.fail_to_fail,
            pass_to_fail=instance.pass_to_fail,
            log_parser=instance.log_parser,
            eval_type=instance.eval_type,
        )


class NativeTaskDraft(PluginModel):
    """An imported instance plus its methodology record, before anything is frozen."""

    kind: Literal["native_task_draft"] = "native_task_draft"
    instance: NativeTaskInstance
    methodology: MethodologyRecord
    package_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    allowed_change_paths: tuple[RelativePath, ...] = Field(min_length=1)

    def visible_files(self) -> dict[str, bytes]:
        """What a solve session may read: the pre-fix snapshot and the issue statement."""
        files = {f"repo/{path}": data for path, data in sorted(self.instance.repo_files.items())}
        files["issue.md"] = self.instance.problem_statement.encode("utf-8")
        return files

    def hidden_files(self) -> dict[str, bytes]:
        """What grading alone may read: gold patch, test patch and the expected test lists."""
        files: dict[str, bytes] = {}
        if self.instance.gold_patch:
            files["gold.patch"] = self.instance.gold_patch.encode("utf-8")
        if self.instance.test_patch:
            files["test.patch"] = self.instance.test_patch.encode("utf-8")
        files["expected-tests.json"] = json.dumps(
            {
                FAIL_TO_PASS: list(self.instance.fail_to_pass),
                PASS_TO_PASS: list(self.instance.pass_to_pass),
                FAIL_TO_FAIL: list(self.instance.fail_to_fail),
                PASS_TO_FAIL: list(self.instance.pass_to_fail),
            },
            sort_keys=True,
        ).encode("utf-8")
        return files


@dataclass(frozen=True, slots=True)
class NativeGradeResult:
    """One candidate graded by the upstream evaluator: its report plus what it is bound to."""

    instance_id: str
    #: Digests the result is valid for. A cached value is reusable only when all three match.
    task_digest: str
    candidate_digest: str
    evaluator_digest: str
    #: The upstream run identity, derived from exactly those three digests.
    run_identity: str
    resolved: bool
    resolution_status: str
    fail_to_pass_success: tuple[str, ...]
    fail_to_pass_failure: tuple[str, ...]
    pass_to_pass_success: tuple[str, ...]
    pass_to_pass_failure: tuple[str, ...]
    patch_applied: bool
    infra_failure: bool
    infra_failure_reason: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def native_metrics(self) -> dict[str, object]:
        """The native measure, kept separate from any PolyCodeBench gate or quality item."""
        return {
            "fail_to_pass": len(self.fail_to_pass_success),
            "fail_to_pass_total": len(self.fail_to_pass_success) + len(self.fail_to_pass_failure),
            "pass_to_pass": len(self.pass_to_pass_success),
            "pass_to_pass_total": len(self.pass_to_pass_success) + len(self.pass_to_pass_failure),
            "resolved": self.resolved,
            "resolution_status": self.resolution_status,
        }


def normalize_native_lists(
    instance: NativeTaskInstance,
) -> tuple[NativeTaskInstance, tuple[str, ...]]:
    """De-duplicate and sort the expected test lists, reporting what changed."""
    changes: list[str] = []
    lists = ((FAIL_TO_PASS, instance.fail_to_pass), (PASS_TO_PASS, instance.pass_to_pass))
    for name, values in lists:
        ordered = tuple(sorted(dict.fromkeys(values)))
        if ordered != values:
            changes.append(f"{name}: {len(values)} entries -> {len(ordered)} unique, sorted")
    updated = instance.model_copy(
        update={
            "fail_to_pass": tuple(sorted(dict.fromkeys(instance.fail_to_pass))),
            "pass_to_pass": tuple(sorted(dict.fromkeys(instance.pass_to_pass))),
        }
    )
    return updated, tuple(changes)


def instance_digest(instance: NativeTaskInstance) -> str:
    """A digest of the whole record, including the snapshot bytes."""
    from polycodebench_core.canonical import canonical_digest, sha256_bytes

    document: dict[str, object] = json.loads(instance.model_dump_json())
    document["repo_file_digests"] = {
        path: sha256_bytes(data) for path, data in sorted(instance.repo_files.items())
    }
    del document["repo_files"]
    return str(canonical_digest(document))


def graded_test_modules(instance: NativeTaskInstance) -> tuple[str, ...]:
    """The test modules the resolution measure reads, resolved from the record's test ids.

    Upstream test ids come in two shapes: path-qualified (``tests/x.py::test_y``) for pytest, and
    bare names (``test_y``) for log parsers that report only names, such as Rust's. A bare id names
    no module, so the module is recovered by looking the test name up in the snapshot - a test
    patch that introduces the test would otherwise leave the module unfindable.
    """
    modules: set[str] = set()
    names: set[str] = set()
    ids = (*instance.fail_to_pass, *instance.pass_to_pass)
    for case_id in ids:
        module, _, name = case_id.rpartition("::")
        if module:
            modules.add(module)
            continue
        names.add(case_id)
    if not names:
        return tuple(sorted(modules))
    for path in sorted(instance.repo_files):
        present = _defined_tests(path, instance.repo_files[path])
        if names & present:
            modules.add(path)
    return tuple(sorted(modules))


def check_instance_leakage(
    instance: NativeTaskInstance, visible: Mapping[str, bytes]
) -> tuple[str, ...]:
    """Fail closed when hidden grading material would reach the solve session.

    A solve session sees the pre-fix snapshot and the issue statement. It must never see the gold
    patch, the test patch, or the graded fail-to-pass test that reveals the answer. Pass-to-pass
    tests are *meant* to be visible - the solver must keep them passing - so a test list is only
    checked for path-qualified ids, where the id itself is not a line of repository source.
    Returns one message per leak found; an empty tuple means the bundle is safe.
    """
    problems: list[str] = []
    visible_body = b"\n".join(visible.values())
    hidden_payloads = (
        ("gold patch", instance.gold_patch),
        ("test patch", instance.test_patch),
        *(
            (label, "\n".join(case_id for case_id in ids if "::" in case_id))
            for label, ids in (
                (FAIL_TO_PASS, instance.fail_to_pass),
                (PASS_TO_PASS, instance.pass_to_pass),
            )
        ),
    )
    for label, payload in hidden_payloads:
        if payload and payload.encode("utf-8") in visible_body:
            problems.append(f"the {label} is present in the visible bundle")
    for module in graded_test_modules(instance):
        current = visible.get(f"repo/{module}")
        if current is None:
            continue
        # Only *fail-to-pass* tests are leaks. A pass-to-pass test is supposed to be visible: it
        # exists in the pre-fix snapshot, the solver must keep it passing, and the maintenance
        # measure reads it. A fail-to-pass test is introduced by the hidden test patch, so
        # finding one in the solve bundle means the graded answer was readable before the
        # candidate ran.
        present = _defined_tests(module, current)
        for case_id in instance.fail_to_pass:
            if case_id.split("::")[-1] in present:
                problems.append(
                    f"repo/{module}: defines graded fail-to-pass test {case_id} "
                    + "before the candidate runs"
                )
    return tuple(problems)


def _defined_tests(path: str, module: bytes) -> frozenset[str]:
    """The test names a module defines, read statically without importing or compiling it.

    Python modules are parsed (a test is a ``def test_*``); other languages are scanned for a
    ``fn test_name`` definition. Nothing is executed, so a fixture's source never reaches the
    supervisor process merely to decide whether it leaks.
    """
    import ast
    import re

    try:
        text = module.decode("utf-8")
    except UnicodeDecodeError:
        return frozenset()
    if path.endswith(".py"):
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return frozenset()
        return frozenset(
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name.startswith("test")
        )
    return frozenset(re.findall(r"\bfn\s+([A-Za-z_][A-Za-z0-9_]*)", text))


def module_source_after_test_patch(instance: NativeTaskInstance, module: str) -> str | None:
    """The text of a test module after the hidden test patch applies, or None when unresolvable."""
    original = instance.repo_files.get(module)
    if original is None or not instance.test_patch:
        return None
    try:
        applied = patching.apply_one(original, _change_for(instance.test_patch, module))
    except PatchRejected:
        return None
    return None if applied is None else applied.decode("utf-8")


def _change_for(patch: str, module: str) -> patching.FileChange:
    """The change a patch makes to one module; an absent module yields an empty change."""
    for change in patching.parse(patch):
        if change.path == module:
            return change
    return patching.FileChange(path=module, hunks=None, removed=True)


def enforce_patch_paths(patch: str, allowed: Sequence[str]) -> None:
    """Reject a candidate patch that touches a path outside the frozen allowlist.

    An empty patch passes: it touches nothing, so it cannot escape the allowlist, and a candidate
    that submits nothing is graded as unresolved rather than refused as malformed.
    """
    try:
        changes = patching.parse(patch)
    except PatchRejected as error:
        raise NativeRecordError(str(error)) from error
    for change in changes:
        permitted = any(
            change.path == allowed_path or change.path.startswith(allowed_path.rstrip("/") + "/")
            for allowed_path in allowed
        )
        if not permitted:
            raise NativeRecordError(f"patch touches {change.path}, outside the allowed change set")
