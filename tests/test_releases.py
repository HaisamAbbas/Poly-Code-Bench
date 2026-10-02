"""Additional synthetic internal release integrity and concurrent-pointer checks."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from polycodebench_core.application_errors import (
    AuthorizationError,
    InvalidState,
    PersistenceConflict,
)
from polycodebench_publication.releases import (
    REQUIRED_CHECKS,
    ReleasePrincipal,
    ReleaseStore,
    SigningKey,
    ValidationEvidence,
    digest,
    validate_projection,
    verify_manifest,
)


def _approved(store, principal, request):
    projection = {
        "schema_version": 1,
        "fixture_kind": "synthetic_internal",
        "scope": "exploratory",
        "cohort_digest": digest({"synthetic": True}),
        "limitations": ["Synthetic internal behavior fixture; no model benchmark measurements."],
        "metrics": [
            {
                "metric_id": "pass_rate",
                "value": "50.000000",
                "coverage": "1.000000",
                "interval_low": "40.000000",
                "interval_high": "60.000000",
                "conditional_on_pass": False,
            }
        ],
    }
    doc = store.draft(
        principal, {"policy": {}, "private_evidence": "NEVER_EXPORT"}, projection, request
    )
    receipts = tuple(
        ValidationEvidence(
            check,
            doc["content_digest"],
            digest(check),
            digest(check),
            "internal:synthetic/" + check,
        )
        for check in sorted(REQUIRED_CHECKS)
    )
    doc = store.validate(principal, doc["id"], receipts, doc["version"], request + "-validate")
    doc = store.review(
        principal, doc["id"], "Reviewed synthetic projection", doc["version"], request + "-review"
    )
    return store.approve(
        principal,
        doc["id"],
        "Approve exact synthetic content",
        doc["version"],
        request + "-approve",
    )


def test_actual_concurrent_publication_commits_one_complete_snapshot(tmp_path):
    store = ReleaseStore(tmp_path / "releases.db")
    principal = ReleasePrincipal("owner", frozenset({"administrator"}))
    signer = SigningKey("synthetic-test-key", Ed25519PrivateKey.generate())
    drafts = [_approved(store, principal, str(index)) for index in range(2)]

    def publish(doc):
        try:
            return store.publish(principal, doc["id"], signer, 0, doc["version"], doc["id"])
        except PersistenceConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, drafts))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert store.current() == {"generation": 1, "release_id": winners[0]["id"]}
    assert verify_manifest(
        store.public(winners[0]["id"])["manifest"], signer.private_key.public_key()
    )
    assert sorted(store.get(doc["id"])["state"] for doc in drafts) == ["approved", "published"]


def test_projection_change_invalidates_reviewer_and_approval(tmp_path):
    store = ReleaseStore(tmp_path / "releases.db")
    principal = ReleasePrincipal("owner", frozenset({"administrator"}))
    doc = _approved(store, principal, "original")
    projection = deepcopy(doc["projection"])
    projection["metrics"][0]["value"] = "49.000000"
    changed = store.update(
        principal, doc["id"], doc["content"], projection, doc["version"], "change"
    )
    assert changed["content_digest"] != doc["content_digest"]
    assert changed["state"] == "draft"
    assert all(changed[field] is None for field in ("review", "approval", "validation"))
    with pytest.raises(InvalidState):
        store.approve(principal, changed["id"], "Old review", changed["version"], "stale-approval")


def test_nested_private_fields_and_mfa_rejected(tmp_path):
    store = ReleaseStore(tmp_path / "releases.db")
    principal = ReleasePrincipal("owner", frozenset({"administrator"}))
    doc = _approved(store, principal, "original")
    projection = deepcopy(doc["projection"])
    projection["metrics"][0]["artifact_storage_url"] = "private:internal/hidden"
    with pytest.raises(InvalidState):
        validate_projection(projection)
    with pytest.raises(AuthorizationError):
        store.draft(
            ReleasePrincipal("owner", frozenset({"administrator"}), mfa=False),
            {},
            doc["projection"],
            "without-mfa",
        )
    assert not verify_manifest(
        {"algorithm": "Ed25519", "signature": "invalid"}, Ed25519PrivateKey.generate().public_key()
    )
