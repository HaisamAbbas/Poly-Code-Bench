"""S3-compatible object storage with explicit integrity and key semantics."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Iterator
from datetime import datetime
from typing import TYPE_CHECKING

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


RESERVED_LIFECYCLE_PREFIXES = frozenset({"provisional", "debug", "cancelled-logs"})


class ObjectStoreError(RuntimeError):
    """Storage failed without exposing credentials or backend response bodies."""


class S3ArtifactStore:
    """Private S3 adapter; callers supply visibility and never arbitrary keys."""

    def __init__(
        self,
        *,
        endpoint_url: str,
        buckets: dict[str, str],
        region_name: str = "us-east-1",
    ) -> None:
        if set(buckets) != {"hidden", "internal", "public"} or len(set(buckets.values())) != 3:
            raise ValueError("three distinct artifact buckets are required")
        if not endpoint_url.startswith(("http://", "https://")):
            raise ValueError("object store endpoint must be an HTTP(S) URL")
        self._buckets = dict(buckets)
        self._client: S3Client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region_name,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def ensure_buckets(self) -> None:
        """Idempotent local bootstrap helper. Production buckets are provisioned externally."""
        for bucket in self._buckets.values():
            try:
                self._client.head_bucket(Bucket=bucket)
            except ClientError as error:
                code = str(error.response.get("Error", {}).get("Code", ""))
                if code not in {"404", "NoSuchBucket", "NotFound"}:
                    raise ObjectStoreError("object-store bucket inspection failed") from None
                try:
                    self._client.create_bucket(Bucket=bucket)
                except ClientError:
                    raise ObjectStoreError("object-store bucket creation failed") from None

    def put_provisional(self, visibility: str, upload_id: str, body: bytes) -> str:
        bucket = self._bucket(visibility)
        key = f"provisional/{upload_id}"
        checksum = base64.b64encode(hashlib.sha256(body).digest()).decode("ascii")
        try:
            self._client.put_object(
                Bucket=bucket,
                Key=key,
                Body=body,
                ContentLength=len(body),
                ChecksumSHA256=checksum,
                IfNoneMatch="*",
                ContentType="application/octet-stream",
            )
        except ClientError as error:
            if str(error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")) == "412":
                raise ObjectStoreError("provisional upload key already exists") from None
            raise ObjectStoreError("provisional object write failed") from None
        return key

    def get_bytes(self, visibility: str, key: str, *, max_bytes: int) -> bytes:
        self._validate_key(key)
        try:
            response = self._client.get_object(Bucket=self._bucket(visibility), Key=key)
            content_length = int(response["ContentLength"])
            if content_length > max_bytes:
                response["Body"].close()
                raise ObjectStoreError("stored object exceeds configured size limit")
            data = bytes(response["Body"].read(max_bytes + 1))
            response["Body"].close()
        except ObjectStoreError:
            raise
        except (ClientError, KeyError, ValueError):
            raise ObjectStoreError("object read failed") from None
        if len(data) != content_length or len(data) > max_bytes:
            raise ObjectStoreError("stored object is truncated or oversized")
        return data

    def put_verified(self, visibility: str, domain: str, digest: str, body: bytes) -> str:
        self._validate_domain(domain)
        if not _valid_digest(digest) or hashlib.sha256(body).hexdigest() != digest[7:]:
            raise ValueError("verified object digest does not match its bytes")
        key = f"{domain}/{digest[7:9]}/{digest[7:]}"
        checksum = base64.b64encode(hashlib.sha256(body).digest()).decode("ascii")
        try:
            self._client.put_object(
                Bucket=self._bucket(visibility),
                Key=key,
                Body=body,
                ContentLength=len(body),
                ChecksumSHA256=checksum,
                IfNoneMatch="*",
                ContentType="application/octet-stream",
            )
        except ClientError as error:
            status = str(error.response.get("ResponseMetadata", {}).get("HTTPStatusCode"))
            code = str(error.response.get("Error", {}).get("Code", ""))
            if status not in {"409", "412"} and code not in {
                "PreconditionFailed",
                "ConditionalRequestConflict",
            }:
                raise ObjectStoreError("verified object write failed") from None
            # A deduplicated object is still independently read and verified by the service.
        return key

    def delete(self, visibility: str, key: str) -> None:
        self._validate_key(key)
        try:
            self._client.delete_object(Bucket=self._bucket(visibility), Key=key)
        except ClientError:
            raise ObjectStoreError("object deletion failed") from None

    def list_keys(self, visibility: str, prefix: str) -> Iterator[str]:
        if not prefix or prefix.startswith("/") or ".." in prefix.split("/"):
            raise ValueError("unsafe object prefix")
        try:
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._bucket(visibility), Prefix=prefix):
                for item in page.get("Contents", []):
                    key = item.get("Key")
                    if isinstance(key, str):
                        yield key
        except ClientError:
            raise ObjectStoreError("object listing failed") from None

    def list_objects(self, visibility: str) -> Iterator[tuple[str, datetime]]:
        """List key/timestamp pairs for trusted garbage collection only."""
        try:
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._bucket(visibility)):
                for item in page.get("Contents", []):
                    key = item.get("Key")
                    modified = item.get("LastModified")
                    if isinstance(key, str) and isinstance(modified, datetime):
                        yield key, modified
        except ClientError:
            raise ObjectStoreError("object listing failed") from None

    def _bucket(self, visibility: str) -> str:
        try:
            return self._buckets[visibility]
        except KeyError:
            raise ValueError("invalid artifact visibility") from None

    @staticmethod
    def _validate_key(key: str) -> None:
        if not key or key.startswith("/") or ".." in key.split("/") or "\\" in key:
            raise ValueError("unsafe object key")

    @staticmethod
    def _validate_domain(domain: str) -> None:
        if not domain or any(
            not (char.isascii() and (char.isalnum() or char in "-_")) for char in domain
        ):
            raise ValueError("invalid encryption domain")
        if domain in RESERVED_LIFECYCLE_PREFIXES:
            # Canonical keys start with the domain; these prefixes carry bucket expiry rules
            # (infra/terraform/modules/artifacts), so verified evidence must never use them.
            raise ValueError("encryption domain collides with a lifecycle-managed prefix")


def _valid_digest(digest: str) -> bool:
    return (
        len(digest) == 71
        and digest.startswith("sha256:")
        and all(char in "0123456789abcdef" for char in digest[7:])
    )
