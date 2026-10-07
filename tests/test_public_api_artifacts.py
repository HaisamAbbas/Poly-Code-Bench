"""E2E-26 controlled downloads are release-scoped and expose only approved public bytes."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from polycodebench_api.app import create_app
from polycodebench_api.dev_fixture import create_synthetic_release
from polycodebench_api.download import mint_download_token, verify_download_token
from polycodebench_core.application_errors import NotFound
from polycodebench_publication.projections import ArtifactRef
from polycodebench_publication.releases import ReleaseStore
from polycodebench_services.artifacts import ArtifactAccessService, ArtifactPrincipal


class MemoryPublicArtifactReader:
    def __init__(self, records: dict[str, tuple[dict[str, object], bytes]]) -> None:
        self.records = records

    def get_verified(self, artifact_id):
        item = self.records.get(str(artifact_id))
        if item is None or item[0]["status"] != "verified":
            raise NotFound()
        return dict(item[0])

    def read_verified(self, artifact_id):
        record = self.get_verified(artifact_id)
        return record, self.records[str(artifact_id)][1]

    def is_publicly_released(self, artifact_id):
        item = self.records.get(str(artifact_id))
        return bool(item and item[0]["publicly_released"])


def _artifact_record(body: bytes, *, visibility: str = "public", released: bool = True):
    content_digest = "sha256:" + hashlib.sha256(body).hexdigest()
    return {
        "visibility": visibility,
        "content_digest": content_digest,
        "size_bytes": len(body),
        "media_type": "application/json",
        "storage_key": f"public-projection/{content_digest[7:9]}/{content_digest[7:]}",
        "status": "verified",
        "publicly_released": released,
    }


def test_e2e26_public_artifact_link_and_binary_route_are_scoped_and_integrity_bound(
    tmp_path,
) -> None:
    public_id = str(uuid4())
    hidden_id = str(uuid4())
    body = b'{"fixture":"public bytes","schema_version":1}'
    hidden_body = b"private hidden fixture"
    public_record = _artifact_record(body)
    hidden_record = _artifact_record(hidden_body, visibility="hidden", released=False)
    artifacts = (
        ArtifactRef(
            artifact_id=public_id,
            content_type="application/json",
            size_bytes=len(body),
            sha256=str(public_record["content_digest"]),
            download_url="https://untrusted.invalid/private-object-url",
            expires_at="2000-01-01T00:00:00+00:00",
        ),
        ArtifactRef(
            artifact_id=hidden_id,
            content_type="application/json",
            size_bytes=len(hidden_body),
            sha256=str(hidden_record["content_digest"]),
            download_url="https://untrusted.invalid/hidden-object-url",
            expires_at="2000-01-01T00:00:00+00:00",
        ),
    )
    store = ReleaseStore(tmp_path / "public-artifact-release.sqlite3")
    release_id = create_synthetic_release(store, artifacts=artifacts)
    access = ArtifactAccessService(
        MemoryPublicArtifactReader(
            {
                public_id: (public_record, body),
                hidden_id: (hidden_record, hidden_body),
            }
        )
    )
    app = create_app(
        store=store,
        cursor_key=b"public-artifact-route-test-signing-key",
        artifact_access=access,
    )

    async def verify() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            link_response = await client.get(
                f"/v1/artifacts/{public_id}", params={"release": release_id}
            )
            assert link_response.status_code == 200
            assert link_response.headers["cache-control"] == "private, no-store"
            link = link_response.json()["data"]
            assert link["download_url"].startswith(f"/v1/artifacts/{public_id}/download?")
            assert "untrusted.invalid" not in link_response.text
            assert link["expires_at"] != "2000-01-01T00:00:00+00:00"

            binary = await client.get(link["download_url"])
            assert binary.status_code == 200
            assert binary.content == body
            assert binary.headers["content-type"] == "application/octet-stream"
            assert binary.headers["content-disposition"].startswith("attachment;")
            assert binary.headers["cache-control"] == "private, no-store"
            assert binary.headers["x-content-type-options"] == "nosniff"
            assert binary.headers["etag"] == f'"{public_record["content_digest"]}"'

            hidden_link = await client.get(
                f"/v1/artifacts/{hidden_id}", params={"release": release_id}
            )
            unknown_link = await client.get(
                f"/v1/artifacts/{uuid4()}", params={"release": release_id}
            )
            assert hidden_link.status_code == unknown_link.status_code == 404
            assert hidden_link.json()["error"]["message"] == unknown_link.json()["error"]["message"]
            assert hidden_id not in hidden_link.text

            # Even a correctly signed token cannot turn a hidden record into a public download.
            services = app.state.services
            hidden_url, _ = mint_download_token(services, hidden_id, release_id)
            hidden_download = await client.get(hidden_url)
            assert hidden_download.status_code == 404
            assert hidden_body.decode() not in hidden_download.text

            other_release = create_synthetic_release(store)
            token = link["download_url"].split("token=", 1)[1]
            cross_release = await client.get(
                f"/v1/artifacts/{public_id}/download",
                params={"release": other_release, "token": token},
            )
            assert cross_release.status_code == 404

            tampered = await client.get(
                f"/v1/artifacts/{public_id}/download",
                params={"release": release_id, "token": token + "x"},
            )
            assert tampered.status_code == 404

    asyncio.run(verify())


def test_e2e26_download_tokens_expire_and_bind_artifact_and_release(monkeypatch) -> None:
    services = SimpleNamespace(
        secret_key=b"download-token-unit-test-signing-key",
        artifact_ttl_seconds=5,
    )
    monkeypatch.setattr("polycodebench_api.download.time.time", lambda: 1000)

    path, expires_at = mint_download_token(services, "artifact-1", "release-1")
    token = path.split("token=", 1)[1]
    assert expires_at == datetime.fromtimestamp(1005, tz=UTC).isoformat()
    assert verify_download_token(services, "artifact-1", "release-1", token)
    assert not verify_download_token(services, "artifact-2", "release-1", token)
    assert not verify_download_token(services, "artifact-1", "release-2", token)

    monkeypatch.setattr("polycodebench_api.download.time.time", lambda: 1005)
    assert not verify_download_token(services, "artifact-1", "release-1", token)
    monkeypatch.setattr("polycodebench_api.download.time.time", lambda: 1006)
    assert not verify_download_token(services, "artifact-1", "release-1", token)


def test_oversized_public_artifact_does_not_receive_a_download_link() -> None:
    artifact_id = uuid4()
    body = b"larger than allowed"
    record = _artifact_record(body)
    access = ArtifactAccessService(
        MemoryPublicArtifactReader({str(artifact_id): (record, body)}),
        max_download_bytes=4,
    )

    with pytest.raises(NotFound):
        access.public_metadata(
            ArtifactPrincipal("public", frozenset({"public_reader"})),
            artifact_id,
            expected_digest=str(record["content_digest"]),
            expected_size=len(body),
            expected_media_type="application/json",
        )
