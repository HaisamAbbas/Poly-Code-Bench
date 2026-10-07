"""Validated settings; secret values are never accepted from this environment."""

from collections.abc import Mapping
from enum import StrEnum
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError, model_validator


class Environment(StrEnum):
    DEV = "dev"
    INTEGRATION = "integration"
    STAGING = "staging"
    PRODUCTION = "production"


class ProcessRole(StrEnum):
    API = "api"
    SCHEDULER = "scheduler"
    MODEL_GATEWAY = "model-gateway"
    SOLVE_SUPERVISOR = "solve-supervisor"
    EVAL_SUPERVISOR = "eval-supervisor"
    JUDGE_GATEWAY = "judge-gateway"
    SCORER = "scorer"
    PUBLISHER = "publisher"
    WEB = "web"


class StartupConfig(BaseModel):
    """Role-specific service settings. Values are references, never resolved secrets."""

    model_config = ConfigDict(extra="forbid", strict=True)

    environment: Environment
    role: ProcessRole
    service_identity: str = Field(min_length=1, max_length=128)
    database_dsn_ref: str | None = Field(default=None, min_length=1)
    oidc_issuer: HttpUrl | None = None
    oidc_audience: str | None = Field(default=None, min_length=1)
    model_secret_namespace: str | None = Field(default=None, min_length=1)
    judge_secret_namespace: str | None = Field(default=None, min_length=1)
    sandbox_provider: str | None = Field(default=None, min_length=1)
    approved_vm_image: str | None = Field(default=None, min_length=1)
    signing_key_ref: str | None = Field(default=None, min_length=1)
    public_api_base_url: HttpUrl | None = None
    object_store_endpoint: HttpUrl | None = None
    object_store_addressing_style: Literal["path", "virtual"] = "path"
    bucket_hidden: str | None = Field(default=None, min_length=1)
    bucket_internal: str | None = Field(default=None, min_length=1)
    bucket_public: str | None = Field(default=None, min_length=1)
    oci_registry: str | None = Field(default=None, min_length=1)
    worker_queue_classes: str | None = Field(default=None, min_length=1)
    max_concurrency: int | None = Field(default=None, ge=1, le=1024)
    artifact_max_bytes: int = Field(default=10_485_760, ge=1, le=1_073_741_824)
    log_max_bytes: int = Field(default=1_048_576, ge=1, le=67_108_864)
    otel_endpoint: HttpUrl | None = None
    metrics_port: int | None = Field(default=None, ge=1, le=65535)

    @model_validator(mode="after")
    def validate_role_requirements(self) -> "StartupConfig":
        required = ROLE_REQUIRED_FIELDS[self.role]
        missing = [name for name in required if getattr(self, name) is None]
        if missing:
            raise ValueError(
                f"missing required settings for {self.role.value}: {', '.join(missing)}"
            )
        if self.environment != Environment.DEV:
            production_required: tuple[str, ...] = (
                ("database_dsn_ref",)
                if self.role
                not in {
                    ProcessRole.MODEL_GATEWAY,
                    ProcessRole.JUDGE_GATEWAY,
                    ProcessRole.WEB,
                    ProcessRole.SOLVE_SUPERVISOR,
                }
                else ()
            )
            if self.role == ProcessRole.API:
                production_required += ("oidc_issuer", "oidc_audience")
            if self.role == ProcessRole.PUBLISHER:
                production_required += ("signing_key_ref",)
            if self.role in {ProcessRole.MODEL_GATEWAY, ProcessRole.JUDGE_GATEWAY}:
                production_required += (
                    ("model_secret_namespace",)
                    if self.role == ProcessRole.MODEL_GATEWAY
                    else ("judge_secret_namespace",)
                )
            if self.role in {ProcessRole.SOLVE_SUPERVISOR, ProcessRole.EVAL_SUPERVISOR}:
                production_required += ("sandbox_provider", "approved_vm_image")
            missing = [name for name in production_required if getattr(self, name) is None]
            if missing:
                raise ValueError(f"missing non-development settings: {', '.join(missing)}")
            for name in (
                "oidc_issuer",
                "public_api_base_url",
                "object_store_endpoint",
                "otel_endpoint",
            ):
                endpoint = getattr(self, name)
                if endpoint is not None and urlparse(str(endpoint)).scheme != "https":
                    raise ValueError(f"{name} must use HTTPS outside development")
            if self.approved_vm_image and "@sha256:" not in self.approved_vm_image:
                raise ValueError(
                    "approved_vm_image must use an immutable digest outside development"
                )
        bucket_names = [
            value
            for value in (self.bucket_hidden, self.bucket_internal, self.bucket_public)
            if value
        ]
        if len(set(bucket_names)) != len(bucket_names):
            raise ValueError("artifact bucket names must be distinct")
        return self


ENV_KEYS = {
    "environment": "PCB_ENVIRONMENT",
    "role": "PCB_ROLE",
    "service_identity": "PCB_SERVICE_IDENTITY",
    "database_dsn_ref": "PCB_DATABASE_DSN_REF",
    "oidc_issuer": "PCB_OIDC_ISSUER",
    "oidc_audience": "PCB_OIDC_AUDIENCE",
    "model_secret_namespace": "PCB_MODEL_SECRET_NAMESPACE",
    "judge_secret_namespace": "PCB_JUDGE_SECRET_NAMESPACE",
    "sandbox_provider": "PCB_SANDBOX_PROVIDER",
    "approved_vm_image": "PCB_APPROVED_VM_IMAGE",
    "signing_key_ref": "PCB_SIGNING_KEY_REF",
    "public_api_base_url": "PCB_PUBLIC_API_BASE_URL",
    "object_store_endpoint": "PCB_OBJECT_STORE_ENDPOINT",
    "object_store_addressing_style": "PCB_OBJECT_STORE_ADDRESSING_STYLE",
    "bucket_hidden": "PCB_BUCKET_HIDDEN",
    "bucket_internal": "PCB_BUCKET_INTERNAL",
    "bucket_public": "PCB_BUCKET_PUBLIC",
    "oci_registry": "PCB_OCI_REGISTRY",
    "worker_queue_classes": "PCB_WORKER_QUEUE_CLASSES",
    "max_concurrency": "PCB_MAX_CONCURRENCY",
    "artifact_max_bytes": "PCB_ARTIFACT_MAX_BYTES",
    "log_max_bytes": "PCB_LOG_MAX_BYTES",
    "otel_endpoint": "PCB_OTEL_ENDPOINT",
    "metrics_port": "PCB_METRICS_PORT",
}
ROLE_REQUIRED_FIELDS = {
    ProcessRole.API: ("database_dsn_ref", "oidc_issuer", "oidc_audience"),
    ProcessRole.SCHEDULER: ("database_dsn_ref", "worker_queue_classes", "max_concurrency"),
    ProcessRole.MODEL_GATEWAY: ("model_secret_namespace",),
    ProcessRole.SOLVE_SUPERVISOR: (
        "sandbox_provider",
        "approved_vm_image",
        "worker_queue_classes",
        "max_concurrency",
    ),
    ProcessRole.EVAL_SUPERVISOR: (
        "database_dsn_ref",
        "sandbox_provider",
        "approved_vm_image",
        "worker_queue_classes",
        "max_concurrency",
    ),
    ProcessRole.JUDGE_GATEWAY: ("judge_secret_namespace",),
    ProcessRole.SCORER: ("database_dsn_ref",),
    ProcessRole.PUBLISHER: ("database_dsn_ref", "signing_key_ref"),
    ProcessRole.WEB: ("public_api_base_url",),
}


def load_startup_config(environ: Mapping[str, str] | None = None) -> StartupConfig:
    """Parse PCB_* settings, rejecting unknown PCB keys and never reading secret values."""
    import os

    source = os.environ if environ is None else environ
    allowed = set(ENV_KEYS.values())
    unknown = sorted(key for key in source if key.startswith("PCB_") and key not in allowed)
    if unknown:
        raise ValueError(f"unknown PCB configuration keys: {', '.join(unknown)}")
    values: dict[str, object] = {}
    for field, env_key in ENV_KEYS.items():
        if env_key in source:
            value: object = source[env_key]
            if field in {"max_concurrency", "artifact_max_bytes", "log_max_bytes", "metrics_port"}:
                try:
                    value = int(str(value))
                except ValueError as exc:
                    raise ValueError(f"{env_key} must be an integer") from exc
            values[field] = value
    raw_role = values.get("role")
    try:
        role = ProcessRole(str(raw_role))
    except ValueError:
        role = None
    if role is not None:
        missing = [
            ENV_KEYS[field]
            for field in ROLE_REQUIRED_FIELDS[role]
            if field not in values or values[field] in (None, "")
        ]
        if missing:
            raise ValueError(f"missing required settings for {role.value}: {', '.join(missing)}")
    try:
        return StartupConfig.model_validate_strings(values)
    except ValidationError as exc:
        safe_messages = []
        for item in exc.errors(include_input=False):
            location = ".".join(str(part) for part in item["loc"])
            custom_error = item.get("ctx", {}).get("error")
            detail = str(custom_error) if isinstance(custom_error, ValueError) else item["type"]
            safe_messages.append(f"{location}: {detail}" if location else detail)
        safe_errors = "; ".join(safe_messages)
        raise ValueError(f"invalid startup configuration: {safe_errors}") from None
