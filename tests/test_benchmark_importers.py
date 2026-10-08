from __future__ import annotations

import hashlib
import json
import stat
import zipfile
from io import BytesIO
from uuid import uuid4

import pytest
from polycodebench_core.benchmark_audit_documents import audit_document_digest
from polycodebench_core.benchmark_imports import (
    BenchmarkImportPlan,
    ImportArtifactBindings,
    freeze_sample,
)
from polycodebench_core.canonical import canonical_digest
from polycodebench_persistence.benchmark_imports import PostgresBenchmarkImportRepository
from polycodebench_services.benchmark_importers import parse_benchmark_snapshot
from pydantic import ValidationError

_REVISIONS = {
    "humaneval": "6d43fb980f9fee3c892a914eda09951f772ad10d",
    "mbpp": "a1e7371c5e006f4e8b314bd23d99220d2fe44c51",
    "swe-bench-verified": "78f471bf655a3137b2e8a75af1501690ec009ec3",
    "gsm8k": "3101c7d5072418e28b9008a6636bde82a006892c",
}
_VARIANTS = {
    "humaneval": "official",
    "mbpp": "sanitized",
    "swe-bench-verified": "verified",
    "gsm8k": "official",
}
_RIGHTS_DIGEST = "sha256:" + "b" * 64


def _plan(
    *,
    slug: str = "humaneval",
    source: bytes,
    ids: tuple[str, ...] | None = None,
    variant: str | None = None,
    source_member: str = "data/HumanEval.jsonl",
    file_format: str = "jsonl",
    rights_state: str = "approved",
    source_date_field: str | None = None,
) -> BenchmarkImportPlan:
    split = "all" if slug == "humaneval" else "test"
    revision = _REVISIONS[slug]
    if ids is None:
        if slug == "humaneval":
            candidates = tuple(f"test/{index}" for index in range(100))
        elif slug == "mbpp":
            candidates = tuple(str(index) for index in range(11, 111))
        elif slug == "gsm8k":
            candidates = tuple(f"test/{index}" for index in range(100))
        else:
            candidates = tuple(f"owner/project__{index}" for index in range(100))
        ids, membership_digest = freeze_sample(
            benchmark_slug=slug,  # type: ignore[arg-type]
            revision=revision,
            split=split,
            variant=variant or _VARIANTS[slug],
            seed="7",
            eligible_item_ids=candidates,
        )
    else:
        ids, membership_digest = freeze_sample(
            benchmark_slug=slug,  # type: ignore[arg-type]
            revision=revision,
            split=split,
            variant=variant or _VARIANTS[slug],
            seed="7",
            eligible_item_ids=ids,
        )
    config = {"format": file_format}
    return BenchmarkImportPlan(
        benchmark_slug=slug,  # type: ignore[arg-type]
        source_uri={
            "humaneval": "https://github.com/openai/human-eval",
            "mbpp": "https://github.com/google-research/google-research",
            "swe-bench-verified": "https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified",
            "gsm8k": "https://github.com/openai/grade-school-math",
        }[slug],
        revision=revision,
        split=split,
        variant=variant or _VARIANTS[slug],
        source_member=(
            "grade_school_math/data/test.jsonl"
            if slug == "gsm8k" and source_member == "data/HumanEval.jsonl"
            else source_member
        ),
        source_digest="sha256:" + hashlib.sha256(source).hexdigest(),
        source_visibility="public",
        storage_visibility="private",
        rights_state=rights_state,  # type: ignore[arg-type]
        rights_evidence_digest=_RIGHTS_DIGEST if rights_state == "approved" else None,
        importer_version="benchmark-import-v1",
        parser_config=tuple(sorted(config.items())),
        parser_config_digest=canonical_digest(config),
        sample_seed="7",
        selected_ids=ids,
        membership_digest=membership_digest,
        source_date_field=source_date_field,  # type: ignore[arg-type]
    )


def _human_row(item_id: str, *, prompt: str | None = "Write a function.") -> dict[str, object]:
    row: dict[str, object] = {
        "task_id": item_id,
        "prompt": prompt,
        "entry_point": "solve",
        "canonical_solution": "def solve(): return 1",
        "test": "assert solve() == 1",
    }
    return row


def _human_source(ids: tuple[str, ...], *, omit: str | None = None) -> bytes:
    rows = [
        json.dumps(_human_row(item_id), ensure_ascii=False, separators=(",", ":"))
        for item_id in ids
        if item_id != omit
    ]
    return ("\r\n".join(rows) + "\r\n").encode("utf-8")


def _gsm8k_source(count: int = 100, *, missing_answer_marker: int | None = None) -> bytes:
    rows = []
    for index in range(count):
        answer = "Compute 1 + 1.\n#### 2" if index != missing_answer_marker else "Compute 1 + 1."
        rows.append(
            json.dumps(
                {"question": f"Synthetic question {index}?", "answer": answer},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    return ("\n".join(rows) + "\n").encode("utf-8")


def _zip_bytes(files: tuple[tuple[str, bytes, int | None], ...]) -> bytes:
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body, external_attr in files:
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            if external_attr is not None:
                info.create_system = 3
                info.external_attr = external_attr
            archive.writestr(info, body)
    return stream.getvalue()


def test_sample_freezes_stable_100_ids_before_search() -> None:
    eligible = tuple(f"test/{index}" for index in range(150))
    first, first_digest = freeze_sample(
        benchmark_slug="humaneval",
        revision=_REVISIONS["humaneval"],
        split="all",
        variant="official",
        seed="7",
        eligible_item_ids=eligible,
    )
    second, second_digest = freeze_sample(
        benchmark_slug="humaneval",
        revision=_REVISIONS["humaneval"],
        split="all",
        variant="official",
        seed="7",
        eligible_item_ids=tuple(reversed(eligible)),
    )
    assert len(first) == 100
    assert first == second
    assert first_digest == second_digest
    with pytest.raises(ValueError, match="100 eligible"):
        freeze_sample(
            benchmark_slug="humaneval",
            revision=_REVISIONS["humaneval"],
            split="all",
            variant="official",
            seed="7",
            eligible_item_ids=eligible[:99],
        )


def test_gsm8k_fixture_adapter_preserves_question_solution_and_final_answer() -> None:
    ids = tuple(f"test/{index}" for index in range(100))
    source = _gsm8k_source()
    plan = _plan(slug="gsm8k", source=source, ids=ids)

    result = parse_benchmark_snapshot(plan, source)

    assert result.state == "complete"
    assert len(result.items) == 100
    first = next(item for item in result.items if item.item_id == "test/0")
    components = {component.component_key: component for component in first.components}
    assert components["question"].content == b"Synthetic question 0?"
    assert components["solution"].content == b"Compute 1 + 1.\n#### 2"
    assert components["answer"].content == b"2"
    assert components["split"].content == b"test"
    provenance = json.loads(components["source_provenance"].content)
    assert provenance == {
        "revision": _REVISIONS["gsm8k"],
        "source_member": "grade_school_math/data/test.jsonl",
        "source_uri": "https://github.com/openai/grade-school-math",
        "split": "test",
    }
    assert first.independent_duplicate_eligible is False


def test_gsm8k_incomplete_solution_and_invalid_sequence_do_not_shift_membership() -> None:
    ids = tuple(f"test/{index}" for index in range(100))
    incomplete_source = _gsm8k_source(missing_answer_marker=8)
    incomplete = parse_benchmark_snapshot(
        _plan(slug="gsm8k", source=incomplete_source, ids=ids), incomplete_source
    )
    incomplete_item = next(item for item in incomplete.items if item.item_id == "test/8")
    assert incomplete.state == "partial"
    assert incomplete_item.state == "incomplete"
    assert "component_missing_or_invalid:answer" in incomplete_item.error_codes

    rows = _gsm8k_source().splitlines()
    rows[3] = b"{malformed"
    malformed_source = b"\n".join(rows) + b"\n"
    malformed = parse_benchmark_snapshot(
        _plan(slug="gsm8k", source=malformed_source, ids=ids), malformed_source
    )
    assert malformed.state == "blocked"
    assert malformed.source_error_codes == ("source_record_sequence_invalid",)
    assert all(item.state == "blocked" for item in malformed.items)


def test_humaneval_keeps_exact_records_and_separate_components() -> None:
    ids = tuple(f"test/{index}" for index in range(100))
    source = _human_source(ids)
    plan = _plan(source=source, ids=ids)
    first = parse_benchmark_snapshot(plan, source)
    retry = parse_benchmark_snapshot(plan, source)

    assert first == retry
    assert first.state == "complete"
    assert len(first.items) == 100
    sample_item = first.items[0]
    assert sample_item.source_record in source
    assert (
        sample_item.source_record_digest
        == "sha256:" + hashlib.sha256(sample_item.source_record or b"").hexdigest()
    )
    assert {component.component_key for component in sample_item.components} == {
        "prompt",
        "entry_point",
        "canonical_solution",
        "tests",
    }
    assert sample_item.self_source_exposure is True
    assert sample_item.source_public_exposure is True
    assert sample_item.independent_duplicate_eligible is False


def test_snapshot_document_retry_digest_binds_artifacts_and_membership() -> None:
    ids = tuple(f"test/{index}" for index in range(100))
    source = _human_source(ids)
    plan = _plan(source=source, ids=ids)
    result = parse_benchmark_snapshot(plan, source)
    artifact_bindings = ImportArtifactBindings(
        source_artifact_id=uuid4(),
        rights_evidence_artifact_id=uuid4(),
        item_artifact_ids=tuple((item.item_id, uuid4()) for item in result.items),
        component_artifact_ids=tuple(
            (item.item_id, component.component_key, uuid4())
            for item in result.items
            for component in item.components
        ),
    )
    version = f"{plan.revision}:{plan.variant}:{plan.parser_config_digest}:{plan.membership_digest}"
    common = {
        "registry_id": uuid4(),
        "plan": plan,
        "result": result,
        "artifacts": artifact_bindings,
        "version": version,
    }
    first = PostgresBenchmarkImportRepository._snapshot_document(
        document_id=uuid4(), actor="auditor@example.test", **common
    )
    retry = PostgresBenchmarkImportRepository._snapshot_document(
        document_id=uuid4(), actor="retry@example.test", **common
    )

    assert first.id != retry.id
    assert audit_document_digest(first) == audit_document_digest(retry)
    assert len(first.payload.membership) == 100
    assert any(
        reference.entity_id == artifact_bindings.source_artifact_id
        for reference in first.payload.components
    )
    assert any(
        reference.entity_id == artifact_bindings.rights_evidence_artifact_id
        for reference in first.payload.components
    )


def test_missing_and_incomplete_ids_stay_in_frozen_denominator() -> None:
    ids = tuple(f"test/{index}" for index in range(100))
    rows = [_human_row(item_id) for item_id in ids if item_id != ids[-1]]
    rows[0]["prompt"] = ""
    source = ("\n".join(json.dumps(row) for row in rows) + "\n").encode()
    result = parse_benchmark_snapshot(_plan(source=source, ids=ids), source)

    assert result.state == "partial"
    by_id = {item.item_id: item for item in result.items}
    assert by_id[ids[-1]].state == "missing"
    assert by_id[ids[-1]].error_codes == ("selected_id_absent_from_source",)
    assert by_id[ids[0]].state == "incomplete"
    assert "component_invalid:prompt" in by_id[ids[0]].error_codes
    assert len(result.items) == 100


def test_duplicate_source_ids_are_blocked_and_never_deduplicated_silently() -> None:
    ids = tuple(f"test/{index}" for index in range(100))
    source = _human_source(ids) + (json.dumps(_human_row(ids[0])) + "\n").encode()
    result = parse_benchmark_snapshot(_plan(source=source, ids=ids), source)

    assert result.state == "partial"
    duplicate = next(item for item in result.items if item.item_id == ids[0])
    assert duplicate.state == "blocked"
    assert duplicate.error_codes == ("duplicate_source_item_id",)


def test_duplicate_json_keys_become_source_errors_not_silent_values() -> None:
    ids = tuple(f"test/{index}" for index in range(100))
    source = _human_source(ids)
    target = ids[0]
    malformed = b'{"task_id":"' + target.encode() + b'","task_id":"' + target.encode() + b'"}\n'
    source = malformed + source
    result = parse_benchmark_snapshot(_plan(source=source, ids=ids), source)

    assert result.state == "partial"
    assert "source_record_invalid" in result.source_error_codes
    assert next(item for item in result.items if item.item_id == target).state == "imported"


@pytest.mark.parametrize(
    ("archive_files", "expected_code"),
    [
        (
            (("../outside.jsonl", b"bad", None), ("data/HumanEval.jsonl", b"[]", None)),
            "archive_unsafe_path",
        ),
        (
            (
                ("data/HumanEval.jsonl", b"[]", None),
                ("data/link.jsonl", b"x", (stat.S_IFLNK | 0o777) << 16),
            ),
            "archive_link_or_special_file",
        ),
        (
            (("data/HumanEval.jsonl", b"[]", None), ("run.py", b"print(1)", None)),
            "archive_script_asset",
        ),
        ((("data/HumanEval.jsonl", b"0" * 1024 * 1024, None),), "archive_compression_ratio_limit"),
    ],
)
def test_unsafe_archive_is_blocked_without_extracting_or_executing(
    archive_files: tuple[tuple[str, bytes, int | None], ...], expected_code: str
) -> None:
    ids = tuple(f"test/{index}" for index in range(100))
    archive = _zip_bytes(archive_files)
    plan = _plan(source=archive, ids=ids)

    result = parse_benchmark_snapshot(plan, archive)

    assert result.state == "blocked"
    assert result.source_error_codes == (expected_code,)
    assert all(item.state == "blocked" for item in result.items)
    assert len(result.items) == 100


def test_nonapproved_rights_and_source_digest_mismatch_fail_closed() -> None:
    ids = tuple(f"test/{index}" for index in range(100))
    source = _human_source(ids)
    gated = _plan(source=source, ids=ids, rights_state="needs_review")
    assert parse_benchmark_snapshot(gated, source).source_error_codes == ("rights_not_approved",)

    wrong_bytes = source + b" "
    mismatch = parse_benchmark_snapshot(_plan(source=source, ids=ids), wrong_bytes)
    assert mismatch.state == "blocked"
    assert mismatch.source_error_codes == ("source_digest_mismatch",)


def test_plan_rejects_ssrf_hosts_unsafe_members_and_harness_execution() -> None:
    ids = tuple(f"test/{index}" for index in range(100))
    source = _human_source(ids)
    plan = _plan(source=source, ids=ids)
    plan_data = plan.model_dump(mode="python")
    for url in (
        "http://github.com/openai/human-eval",
        "https://127.0.0.1/openai/human-eval",
        "https://user@github.com/openai/human-eval",
        "https://github.com/openai/human-eval?download=1",
        "https://github.com/openai/human-eval/../other",
    ):
        with pytest.raises(ValidationError):
            BenchmarkImportPlan.model_validate({**plan_data, "source_uri": url})
    with pytest.raises(ValidationError):
        BenchmarkImportPlan.model_validate({**plan_data, "source_member": "../../secret.jsonl"})
    with pytest.raises(ValidationError):
        BenchmarkImportPlan.model_validate({**plan_data, "execute_official_harness": True})


def test_mbpp_sanitized_array_keeps_code_test_imports_and_variant() -> None:
    rows = [
        {
            "task_id": task_id,
            "prompt": f"Write function {task_id}.",
            "code": f"def solve_{task_id}(): return {task_id}",
            "test_list": [f"assert solve_{task_id}() == {task_id}"],
            "test_imports": [],
        }
        for task_id in range(11, 111)
    ]
    source = json.dumps(rows, separators=(",", ":")).encode()
    ids = tuple(str(task_id) for task_id in range(11, 111))
    plan = _plan(
        slug="mbpp",
        source=source,
        ids=ids,
        variant="sanitized",
        source_member="mbpp/sanitized-mbpp.json",
        file_format="json",
    )
    result = parse_benchmark_snapshot(plan, source)

    assert result.state == "complete"
    assert {component.component_key for component in result.items[0].components} == {
        "prompt",
        "canonical_solution",
        "tests",
        "test_imports",
    }
    assert result.items[0].source_record is not None
    assert result.items[0].source_record.startswith(b"{")


def test_swe_verified_keeps_repo_issue_patch_refs_and_date_precision() -> None:
    ids = tuple(f"owner/project__{index}" for index in range(100))
    rows = [
        {
            "instance_id": item_id,
            "repo": "owner/project",
            "base_commit": "c" * 40,
            "problem_statement": "An issue report.",
            "patch": "diff --git a/a.py b/a.py",
            "test_patch": "",
            "created_at": "2024-02-01",
        }
        for item_id in ids
    ]
    source = ("\n".join(json.dumps(row) for row in rows) + "\n").encode()
    plan = _plan(
        slug="swe-bench-verified",
        source=source,
        ids=ids,
        variant="verified",
        source_member="data/verified.jsonl",
        source_date_field="created_at",
    )
    result = parse_benchmark_snapshot(plan, source)

    assert result.state == "complete"
    first = result.items[0]
    assert {component.component_key for component in first.components} == {
        "repository",
        "base_commit",
        "issue",
        "gold_patch",
        "test_patch",
    }
    assert first.source_date_value == "2024-02-01"
    assert first.source_date_precision == "day"
    assert first.source_urls[-1].startswith("https://github.com/owner/project/issues/")
