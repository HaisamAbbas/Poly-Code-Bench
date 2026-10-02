"""go.mod/go.sum identity tests (PCB-22-1): a module graph that is not pinned must be refused."""

from __future__ import annotations

import pytest
from go_plugin_support import frozen
from polycodebench_lang_go import GoLanguagePlugin
from polycodebench_lang_go.locks import LockError, module_digest, parse_module_files

PINS = b"""module pcb.local/demo

go 1.26

require example.com/dep v1.4.2

require example.com/other v0.9.0 // indirect
"""
SUMS = b"""example.com/dep v1.4.2 h1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
example.com/dep v1.4.2/go.mod h1:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=
example.com/other v0.9.0 h1:CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=
example.com/other v0.9.0/go.mod h1:DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD=
"""
REORDERED = b"""module pcb.local/demo

go 1.26

require (
\texample.com/other v0.9.0 // indirect
\texample.com/dep v1.4.2
)
"""
REORDERED_SUMS = b"".join(sorted(SUMS.splitlines(keepends=True)))
NO_SUM = b"""module pcb.local/demo

go 1.26

require example.com/dep v1.4.2
"""


def test_resolution_is_canonical_and_order_independent() -> None:
    one = parse_module_files(PINS, SUMS)
    two = parse_module_files(REORDERED, REORDERED_SUMS)
    assert one.digest == two.digest
    assert one.resolutions() == ("example.com/dep v1.4.2", "example.com/other v0.9.0")
    assert one.module == "pcb.local/demo"
    assert one.go_version == "1.26"


def test_changing_a_version_changes_the_digest() -> None:
    bumped = PINS.replace(b"v1.4.2", b"v1.4.3")
    bumped_sums = SUMS.replace(b"v1.4.2", b"v1.4.3")
    assert module_digest(PINS, SUMS) != module_digest(bumped, bumped_sums)


def test_a_module_without_a_content_hash_is_refused() -> None:
    with pytest.raises(LockError, match="no content hash"):
        module_digest(NO_SUM, b"")


def test_a_go_mod_hash_alone_does_not_pin_the_content() -> None:
    only_go_mod = b"example.com/dep v1.4.2/go.mod h1:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=\n"
    with pytest.raises(LockError, match="no content hash"):
        module_digest(PINS, only_go_mod)


def test_a_module_without_a_go_directive_is_refused() -> None:
    with pytest.raises(LockError, match="`go` version"):
        module_digest(b"module pcb.local/demo\n", b"")


def test_a_module_without_a_module_path_is_refused() -> None:
    with pytest.raises(LockError, match="`module` path"):
        module_digest(b"go 1.26\n", b"")


def test_a_duplicate_requirement_is_refused() -> None:
    duplicated = PINS + b"\nrequire example.com/dep v1.4.2\n"
    with pytest.raises(LockError, match="twice"):
        module_digest(duplicated, SUMS)


def test_a_malformed_requirement_is_refused() -> None:
    broken = b"module pcb.local/demo\n\ngo 1.26\n\nrequire (example.com/dep v1.4.2)\n"
    with pytest.raises(LockError, match="valid requirement"):
        module_digest(broken, SUMS)


def test_a_stdlib_only_module_with_an_empty_sum_is_pinned() -> None:
    empty = module_digest(b"module pcb.local/topwords\n\ngo 1.26\n", b"")
    assert empty.startswith("sha256:")


def test_the_task_module_digest_reaches_the_tool_identity() -> None:
    plugin = GoLanguagePlugin()
    view = frozen(plugin)
    digest = view.quality["go_module_digest"]
    assert isinstance(digest, str) and digest.startswith("sha256:")
    plan = plugin.build_plan(view, _candidate(view))
    assert plan.tool.lock_digest == view.quality["go_module_digest"] or plan.tool.lock_digest
    identity = plugin.identities.tool("go-vet", lock_digest=digest)
    assert identity.lock_digest == digest


def _candidate(view):  # type: ignore[no-untyped-def]
    from polycodebench_core.identity import new_entity_id
    from polycodebench_core.models import Candidate

    return Candidate.model_validate(
        {
            "schema_version": 1,
            "kind": "candidate",
            "candidate_id": new_entity_id(),
            "run_id": new_entity_id(),
            "task_id": view.task_id,
            "task_version": 1,
            "sample_index": 0,
            "submission_kind": "source_bundle",
            "payload_digest": "sha256:" + "0" * 64,
            "artifact_ids": [],
            "frozen_at": None,
        }
    )
