"""S3-compatible object storage with explicit integrity and key semantics."""

from __future__ import annotations

import base64
import hashlib
import os
import re
from collections.abc import Iterator, Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, cast
from urllib.parse import urlsplit

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client
else:
    S3Client = Any


RESERVED_LIFECYCLE_PREFIXES = frozenset({"provisional", "debug", "cancelled-logs"})
ObjectStoreProvider = Literal["s3", "alibaba_oss"]


class ObjectStoreError(RuntimeError):
    """Storage failed without exposing credentials or backend response bodies."""


def object_store_addressing_style(
    environ: Mapping[str, str] | None = None,
) -> Literal["path", "virtual"]:
    """Validate the provider-specific S3 endpoint addressing mode."""
    source = os.environ if environ is None else environ
    style = source.get("PCB_OBJECT_STORE_ADDRESSING_STYLE", "path")
    if style not in {"path", "virtual"}:
        raise ValueError("PCB_OBJECT_STORE_ADDRESSING_STYLE must be path or virtual")
    return cast(Literal["path", "virtual"], style)


def object_store_provider(environ: Mapping[str, str] | None = None) -> ObjectStoreProvider:
    """Validate the compatible object-store protocol variant."""
    source = os.environ if environ is None else environ
    provider = source.get("PCB_OBJECT_STORE_PROVIDER", "s3")
    if provider not in {"s3", "alibaba_oss"}:
        raise ValueError("PCB_OBJECT_STORE_PROVIDER must be s3 or alibaba_oss")
    return cast(ObjectStoreProvider, provider)


def object_store_region(environ: Mapping[str, str] | None = None) -> str:
    """Return a validated signing region for the configured object-store endpoint."""
    source = os.environ if environ is None else environ
    region = source.get("PCB_OBJECT_STORE_REGION", "us-east-1")
    if (
        not region
        or len(region) > 64
        or any(not (char.isascii() and (char.isalnum() or char == "-")) for char in region)
    ):
        raise ValueError("PCB_OBJECT_STORE_REGION must be a region identifier")
    return region


def object_store_ram_role_name(environ: Mapping[str, str] | None = None) -> str:
    """Read the non-secret ECS RAM role name required for Alibaba OSS credentials."""
    source = os.environ if environ is None else environ
    role_name = source.get("PCB_ALIBABA_RAM_ROLE_NAME", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", role_name):
        raise ValueError("PCB_ALIBABA_RAM_ROLE_NAME must be a valid ECS RAM role name")
    return role_name


def _add_oss_no_overwrite_header(request: Any, **kwargs: Any) -> None:
    """Sign OSS's native no-overwrite control before the SigV4 request is finalized."""
    request.headers["x-oss-forbid-overwrite"] = "true"


def configure_oss_put_protection(client: Any) -> None:
    """Use OSS's atomic overwrite prevention on a raw Botocore client."""
    client.meta.events.register_first(
        "before-sign.s3.PutObject",
        _add_oss_no_overwrite_header,
        unique_id="polycodebench-oss-no-overwrite",
    )


def _validate_alibaba_oss_endpoint(endpoint_url: str, region_name: str) -> None:
    """Keep temporary RAM-role credentials on Alibaba's regional HTTPS OSS endpoint."""
    parsed = urlsplit(endpoint_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != f"oss-{region_name}.aliyuncs.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Alibaba OSS requires its regional HTTPS endpoint")


def create_s3_compatible_client(
    *,
    endpoint_url: str,
    region_name: str,
    addressing_style: Literal["path", "virtual"],
    provider: ObjectStoreProvider,
    ram_role_name: str | None = None,
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
) -> S3Client:
    """Create a regional S3 client with the selected backend's signing and credential flow."""
    if provider == "alibaba_oss":
        _validate_alibaba_oss_endpoint(endpoint_url, region_name)
        if addressing_style != "virtual":
            raise ValueError("Alibaba OSS requires virtual-hosted object addressing")
        if not ram_role_name:
            raise ValueError("Alibaba OSS requires an attached ECS RAM role")
        from polycodebench_persistence.alibaba_credentials import boto3_session_for_ram_role

        from_config = Config(
            signature_version="s3v4",
            s3={"addressing_style": addressing_style},
            request_checksum_calculation="when_required",
        )
        client = boto3_session_for_ram_role(ram_role_name).client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region_name,
            config=from_config,
        )
        configure_oss_put_protection(client)
        return cast(S3Client, client)
    return cast(
        S3Client,
        boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region_name,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(signature_version="s3v4", s3={"addressing_style": addressing_style}),
        ),
    )


class S3ArtifactStore:
    """Private S3 adapter; callers supply visibility and never arbitrary keys."""

    def __init__(
        self,
        *,
        endpoint_url: str,
        buckets: dict[str, str],
        region_name: str = "us-east-1",
        addressing_style: Literal["path", "virtual"] = "path",
        provider: ObjectStoreProvider = "s3",
        ram_role_name: str | None = None,
    ) -> None:
        if set(buckets) != {"hidden", "internal", "public"} or len(set(buckets.values())) != 3:
            raise ValueError("three distinct artifact buckets are required")
        if not endpoint_url.startswith(("http://", "https://")):
            raise ValueError("object store endpoint must be an HTTP(S) URL")
        if addressing_style not in {"path", "virtual"}:
            raise ValueError("object-store addressing style must be path or virtual")
        if provider not in {"s3", "alibaba_oss"}:
            raise ValueError("object-store provider must be s3 or alibaba_oss")
        if provider == "alibaba_oss" and addressing_style != "virtual":
            raise ValueError("Alibaba OSS requires virtual-hosted object addressing")
        if provider == "alibaba_oss" and any(
            not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", bucket)
            for bucket in buckets.values()
        ):
            raise ValueError("Alibaba OSS bucket names must be DNS-compatible lowercase names")
        self._buckets = dict(buckets)
        self._provider = provider
        self._client = create_s3_compatible_client(
            endpoint_url=endpoint_url,
            region_name=region_name,
            addressing_style=addressing_style,
            provider=provider,
            ram_role_name=ram_role_name,
        )

    @classmethod
    def from_environment(
        cls,
        *,
        endpoint_url: str,
        buckets: dict[str, str],
        region_name: str | None = None,
    ) -> S3ArtifactStore:
        """Build a client with provider-specific signing and addressing settings."""
        provider = object_store_provider()
        ram_role_name = object_store_ram_role_name() if provider == "alibaba_oss" else None
        return cls(
            endpoint_url=endpoint_url,
            buckets=buckets,
            region_name=(object_store_region() if region_name is None else region_name),
            addressing_style=object_store_addressing_style(),
            provider=provider,
            ram_role_name=ram_role_name,
        )

    def _put_object(self, *, bucket: str, key: str, body: bytes) -> None:
        """Write bytes with backend-specific integrity and atomic no-overwrite controls."""
        if self._provider == "alibaba_oss":
            content_md5 = base64.b64encode(
                hashlib.md5(body, usedforsecurity=False).digest()
            ).decode("ascii")
            self._client.put_object(
                Bucket=bucket,
                Key=key,
                Body=body,
                ContentLength=len(body),
                ContentMD5=content_md5,
                ContentType="application/octet-stream",
            )
            return
        checksum = base64.b64encode(hashlib.sha256(body).digest()).decode("ascii")
        self._client.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentLength=len(body),
            ChecksumSHA256=checksum,
            IfNoneMatch="*",
            ContentType="application/octet-stream",
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
        try:
            self._put_object(bucket=bucket, key=key, body=body)
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
        try:
            self._put_object(bucket=self._bucket(visibility), key=key, body=body)
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
