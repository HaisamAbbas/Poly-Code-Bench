"""Synthetic internal publication E2E-30 and privacy acceptance scenarios."""

import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from polycodebench_core.application_errors import (
    AuthorizationError,
    IdempotencyConflict,
    InvalidState,
    PersistenceConflict,
)
from polycodebench_publication.cli import main as release_main
from polycodebench_publication.releases import (
    REQUIRED_CHECKS,
    ReleasePrincipal,
    ReleaseStore,
    SigningKey,
    ValidationEvidence,
    digest,
    verify_manifest,
)


@pytest.fixture
def environment(tmp_path):
    store = ReleaseStore(tmp_path / "releases.db")
    principal = ReleasePrincipal("local-owner", frozenset({"curator", "reviewer", "publisher"}))
    signer = SigningKey("test-only", Ed25519PrivateKey.generate())
    return store, principal, signer


def draft(store, principal, request="draft"):
    projection = {
        "schema_version": 1,
        "fixture_kind": "synthetic_internal",
        "scope": "exploratory",
        "cohort_digest": digest({"tasks": ["synthetic"]}),
        "limitations": ["Synthetic internal fixture"],
        "metrics": [
            {
                "metric_id": "pass_rate",
                "value": "0.5",
                "interval_low": None,
                "interval_high": None,
                "coverage": "0.5",
                "conditional_on_pass": False,
            }
        ],
    }
    return store.draft(
        principal,
        {"policy": {"editorial_index": False}, "private_evidence": "SECRET_MARKER"},
        projection,
        request,
    )


def ready(store, principal, doc):
    request_prefix = f"{doc['id']}:{doc['version']}:"
    receipts = tuple(
        ValidationEvidence(
            check,
            doc["content_digest"],
            digest({"receipt": check}),
            digest({"receipt": check}),
            "internal:synthetic/" + check,
        )
        for check in sorted(REQUIRED_CHECKS)
    )
    doc = store.validate(
        principal, doc["id"], receipts, doc["version"], request_prefix + "validate"
    )
    doc = store.review(
        principal,
        doc["id"],
        "Reviewed synthetic fixture",
        doc["version"],
        request_prefix + "review",
    )
    return store.approve(
        principal, doc["id"], "Approve exact fixture", doc["version"], request_prefix + "approve"
    )


def test_e2e30_approval_changes_invalidate_and_history_correction(environment):
    store, principal, signer = environment
    approved = ready(store, principal, draft(store, principal))
    changed = store.update(
        principal,
        approved["id"],
        {"policy": {"changed": True}},
        approved["projection"],
        approved["version"],
        "change",
    )
    assert changed["state"] == "draft" and changed["approval"] is None and changed["review"] is None
    with pytest.raises(InvalidState):
        store.publish(principal, changed["id"], signer, 0, changed["version"], "unapproved")
    approved = ready(store, principal, changed)
    published = store.publish(principal, approved["id"], signer, 0, approved["version"], "publish")
    assert verify_manifest(published["manifest"], signer.private_key.public_key())
    original = store.public(published["id"])
    with pytest.raises(InvalidState):
        store.update(
            principal,
            published["id"],
            {},
            published["projection"],
            published["version"],
            "mutate-history",
        )
    successor = store.draft(
        principal,
        {"policy": {"corrected": True}},
        published["projection"],
        "correction",
        predecessor=published["id"],
        correction_reason="Synthetic correction",
    )
    successor = ready(store, principal, successor)
    store.publish(principal, successor["id"], signer, 1, successor["version"], "publish-correction")
    assert store.public(published["id"]) == original
    assert store.current()["release_id"] == successor["id"]
    assert ReleaseStore(store.path).current()["generation"] == 2


def test_e2e30_pointer_race_is_atomic_and_safe_projection(environment):
    store, principal, signer = environment
    first = ready(store, principal, draft(store, principal, "first"))
    second = ready(store, principal, draft(store, principal, "second"))
    store.publish(principal, first["id"], signer, 0, first["version"], "publish-first")
    with pytest.raises(PersistenceConflict):
        store.publish(principal, second["id"], signer, 0, second["version"], "race")
    assert store.get(second["id"])["state"] == "approved"
    public = store.public(first["id"])
    assert "SECRET_MARKER" not in json.dumps(public)
    assert "content" not in public
    public["manifest"]["projection_digest"] = digest({"tampered": True})
    assert not verify_manifest(public["manifest"], signer.private_key.public_key())
    with pytest.raises(InvalidState):
        store.publish(
            principal,
            second["id"],
            signer,
            1,
            second["version"],
            "remote",
            target="https://example.com",
        )


def test_safe_projection_rejects_private_fields_and_missing_receipts(environment):
    store, principal, _ = environment
    doc = draft(store, principal)
    with pytest.raises(InvalidState):
        store.validate(principal, doc["id"], (), doc["version"], "no-receipts")
    unsafe = {**doc["projection"], "private_url": "https://private.invalid/hidden"}
    with pytest.raises(InvalidState, match="outside the public allowlist"):
        store.update(principal, doc["id"], doc["content"], unsafe, doc["version"], "unsafe")
    unchanged = store.get(doc["id"])
    assert unchanged["state"] == "draft" and unchanged["version"] == doc["version"]


def test_authorization_idempotency_audit_and_withdrawal(environment):
    store, principal, signer = environment
    doc = draft(store, principal)
    assert draft(store, principal) == doc
    with pytest.raises(IdempotencyConflict):
        store.draft(principal, {}, doc["projection"], "draft")
    with pytest.raises(AuthorizationError):
        store.draft(ReleasePrincipal("reader", frozenset()), {}, doc["projection"], "deny")
    approved = ready(store, principal, doc)
    published = store.publish(principal, doc["id"], signer, 0, approved["version"], "publish")
    assert (
        store.publish(principal, doc["id"], signer, 0, approved["version"], "publish") == published
    )
    withdrawn = store.withdraw(
        principal, doc["id"], "Synthetic withdrawal", published["version"], 1, "withdraw"
    )
    assert store.current() == {"generation": 2, "release_id": None}
    assert store.public(doc["id"])["manifest"] == published["manifest"]
    assert withdrawn["state"] == "withdrawn"
    assert [item["action"] for item in store.audit()] == [
        "draft",
        "validate",
        "review",
        "approve",
        "publish",
        "withdraw",
    ]


def test_stale_receipts_and_bool_only_validation_are_rejected(environment):
    store, principal, _ = environment
    doc = draft(store, principal)
    receipts = tuple(
        ValidationEvidence(
            check, digest({"stale": True}), digest(check), digest(check), "internal:receipt"
        )
        for check in REQUIRED_CHECKS
    )
    with pytest.raises(InvalidState):
        store.validate(principal, doc["id"], receipts, doc["version"], "stale")
    assert store.get(doc["id"])["validation"] is None


def test_release_cli_preserves_permission_exit_code(tmp_path, capsys):
    content_path = tmp_path / "content.json"
    projection_path = tmp_path / "projection.json"
    content_path.write_text("{}", encoding="utf-8")
    projection_path.write_text("{}", encoding="utf-8")
    code = release_main(
        [
            "create",
            "--store",
            str(tmp_path / "cli.db"),
            "--subject",
            "reader",
            "--role",
            "public_reader",
            "--request-id",
            "denied",
            "--content",
            str(content_path),
            "--projection",
            str(projection_path),
        ]
    )
    assert code == 3
    assert "publication refused" in capsys.readouterr().err
