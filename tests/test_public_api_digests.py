import re

from polycodebench_api.postgres_submissions import _digest as postgres_submission_digest
from polycodebench_api.public_routes import _digest_of as public_payload_digest
from polycodebench_api.submission_routes import _meta
from polycodebench_api.submissions import _digest as local_submission_digest
from polycodebench_core.canonical import canonical_json_bytes, sha256_bytes


def test_api_digests_use_the_canonical_single_sha256_prefix() -> None:
    payload = {"kind": "test_payload", "value": "stable"}
    expected = sha256_bytes(canonical_json_bytes(payload))

    assert re.fullmatch(r"sha256:[0-9a-f]{64}", expected)
    assert local_submission_digest(payload) == expected
    assert postgres_submission_digest(payload) == expected
    assert public_payload_digest(payload) == expected
    assert _meta(payload).release_digest == expected
