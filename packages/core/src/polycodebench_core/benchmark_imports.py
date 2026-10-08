"""Immutable contracts for local-only benchmark snapshot imports."""

from __future__ import annotations

import hashlib
import re
from typing import Annotated, Literal
from urllib.parse import unquote, urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from polycodebench_core.canonical import canonical_digest
from polycodebench_core.models import Digest, Seed64

BenchmarkSlug = Literal["humaneval", "mbpp", "swe-bench-verified"]
SourceVisibility = Literal["public", "restricted", "private"]
ImportStorageVisibility = Literal["private", "restricted"]
ImportState = Literal["imported", "incomplete", "missing", "blocked"]
SourceDatePrecision = Literal["day", "second", "millisecond", "microsecond", "nanosecond"]
_ImportText = Annotated[str, Field(min_length=1, max_length=512)]
_ITEM_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$", re.ASCII)
_SOURCE_HOSTS = {
    "humaneval": ("github.com", "/openai/human-eval"),
    "mbpp": ("github.com", "/google-research/google-research"),
    "swe-bench-verified": (
        "huggingface.co",
        "/datasets/SWE-bench/SWE-bench_Verified",
    ),
}
_SOURCE_REVISIONS = {
    "humaneval": "6d43fb980f9fee3c892a914eda09951f772ad10d",
    "mbpp": "a1e7371c5e006f4e8b314bd23d99220d2fe44c51",
    "swe-bench-verified": "78f471bf655a3137b2e8a75af1501690ec009ec3",
}
_ALLOWED_PUBLIC_SOURCE_HOSTS = frozenset({"github.com", "huggingface.co"})
_SEED_TEXT = re.compile(r"^(?:0|[1-9][0-9]{0,19})$", re.ASCII)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ImportFamilyLink(_StrictFrozenModel):
    """Explicit lineage asserted by an approved source manifest; never inferred from text."""

    child_item_id: _ImportText
    parent_benchmark_slug: _ImportText
    parent_item_id: _ImportText
    relation: Literal["variant_of", "derived_from", "translated_from"]
    evidence_artifact_id: UUID
    evidence_digest: Digest

    @model_validator(mode="after")
    def item_ids_are_safe(self) -> ImportFamilyLink:
        if not _ITEM_ID.fullmatch(self.child_item_id) or not _ITEM_ID.fullmatch(
            self.parent_item_id
        ):
            raise ValueError("lineage item IDs must use the bounded source ID format")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,95}", self.parent_benchmark_slug, re.ASCII):
            raise ValueError("lineage benchmark slug is invalid")
        return self


def validate_benchmark_source_uri(value: str, slug: BenchmarkSlug) -> str:
    """Validate provenance URLs without making a request or resolving a host."""
    try:
        parsed = urlsplit(value)
        host, expected_path = _SOURCE_HOSTS[slug]
        decoded_path = unquote(parsed.path, errors="strict")
    except (KeyError, UnicodeError, ValueError) as error:
        raise ValueError("source URI is malformed") from error
    if (
        parsed.scheme != "https"
        or parsed.hostname != host
        or parsed.netloc != host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != expected_path
        or decoded_path != parsed.path
        or "\\" in parsed.path
        or any(part in {".", ".."} for part in parsed.path.split("/"))
    ):
        raise ValueError("source URI is outside the benchmark's exact HTTPS origin")
    return value


def _validate_public_source_url(value: str) -> None:
    try:
        parsed = urlsplit(value)
        decoded_path = unquote(parsed.path, errors="strict")
    except (UnicodeError, ValueError):
        raise ValueError("item source URL is malformed") from None
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _ALLOWED_PUBLIC_SOURCE_HOSTS
        or parsed.netloc != parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
        or decoded_path != parsed.path
        or "\\" in parsed.path
        or any(part in {".", ".."} for part in parsed.path.split("/"))
    ):
        raise ValueError("item source URL is outside the approved public reference hosts")


def _membership_digest(
    slug: BenchmarkSlug,
    revision: str,
    split: str,
    variant: str,
    seed: str,
    item_ids: tuple[str, ...],
) -> str:
    return canonical_digest(
        {
            "benchmark_slug": slug,
            "revision": revision,
            "split": split,
            "variant": variant,
            "seed": seed,
            "selected_ids": list(item_ids),
        }
    )


def freeze_sample(
    *,
    benchmark_slug: BenchmarkSlug,
    revision: str,
    split: str,
    variant: str,
    seed: Seed64,
    eligible_item_ids: tuple[str, ...],
) -> tuple[tuple[str, ...], str]:
    """Freeze a reproducible 100-ID sample before any source search or task matching."""
    if revision != _SOURCE_REVISIONS[benchmark_slug]:
        raise ValueError("sample revision must match the repository's frozen source pin")
    if not isinstance(seed, str) or not _SEED_TEXT.fullmatch(seed) or int(seed) > 2**64 - 1:
        raise ValueError("sample seed must be a canonical unsigned 64-bit integer string")
    _validate_variant(benchmark_slug, variant)
    expected_split = {
        "humaneval": "all",
        "mbpp": "test",
        "swe-bench-verified": "test",
    }[benchmark_slug]
    if split != expected_split:
        raise ValueError(
            f"{benchmark_slug} audit sampling requires the pinned {expected_split!r} split"
        )
    if len(eligible_item_ids) < 100:
        raise ValueError("the frozen audit sample requires at least 100 eligible source IDs")
    if len(set(eligible_item_ids)) != len(eligible_item_ids) or any(
        not _ITEM_ID.fullmatch(item_id) for item_id in eligible_item_ids
    ):
        raise ValueError("eligible source IDs must be unique and use the bounded ID format")
    if any(not _id_belongs_to_split(benchmark_slug, item_id) for item_id in eligible_item_ids):
        raise ValueError("eligible item IDs do not belong to the declared benchmark split")
    seed_bytes = seed.encode("ascii")
    ranked = sorted(
        eligible_item_ids,
        key=lambda item_id: (
            hashlib.sha256(
                b"pcb-benchmark-sample-v1\0"
                + benchmark_slug.encode("ascii")
                + b"\0"
                + seed_bytes
                + b"\0"
                + item_id.encode("ascii")
            ).digest(),
            item_id,
        ),
    )
    selected = tuple(ranked[:100])
    return selected, _membership_digest(benchmark_slug, revision, split, variant, seed, selected)


class BenchmarkImportPlan(_StrictFrozenModel):
    """Pinned, rights-gated import plan; all parser inputs are local and immutable."""

    benchmark_slug: BenchmarkSlug
    source_uri: _ImportText
    revision: str
    split: _ImportText
    variant: Literal["official", "original", "sanitized", "verified"]
    source_member: _ImportText
    source_digest: Digest
    source_visibility: SourceVisibility
    storage_visibility: ImportStorageVisibility
    rights_state: Literal["approved", "needs_review", "blocked", "gated"]
    rights_evidence_digest: Digest | None
    importer_version: Literal["benchmark-import-v1"]
    parser_config: tuple[tuple[_ImportText, _ImportText], ...]
    parser_config_digest: Digest
    sample_seed: Seed64
    selected_ids: tuple[_ImportText, ...]
    membership_digest: Digest
    family_links: tuple[ImportFamilyLink, ...] = ()
    source_date_field: Literal["created_at", "created_on", "source_date"] | None = None
    execute_official_harness: Literal[False] = False

    @model_validator(mode="after")
    def validate_frozen_plan(self) -> BenchmarkImportPlan:
        validate_benchmark_source_uri(self.source_uri, self.benchmark_slug)
        if self.revision != _SOURCE_REVISIONS[self.benchmark_slug]:
            raise ValueError("revision must match the repository's frozen source pin")
        expected_split = {
            "humaneval": "all",
            "mbpp": "test",
            "swe-bench-verified": "test",
        }[self.benchmark_slug]
        if self.split != expected_split:
            raise ValueError(
                f"{self.benchmark_slug} audit imports require the pinned {expected_split!r} split"
            )
        _validate_variant(self.benchmark_slug, self.variant)
        _validate_member_path(self.source_member)
        if not self.source_member.lower().endswith((".json", ".jsonl", ".ndjson")):
            raise ValueError("only JSON benchmark data members are supported")
        if len(self.selected_ids) != 100 or len(set(self.selected_ids)) != 100:
            raise ValueError("a frozen benchmark import must contain exactly 100 unique IDs")
        if any(not _ITEM_ID.fullmatch(item_id) for item_id in self.selected_ids):
            raise ValueError("selected item IDs must use the bounded source ID format")
        if any(
            not _id_belongs_to_split(self.benchmark_slug, item_id) for item_id in self.selected_ids
        ):
            raise ValueError("selected item IDs do not belong to the declared benchmark split")
        config = dict(self.parser_config)
        if (
            len(config) != len(self.parser_config)
            or tuple(sorted(config.items())) != self.parser_config
        ):
            raise ValueError("parser configuration keys must be unique and sorted")
        expected_format = "json" if self.source_member.lower().endswith(".json") else "jsonl"
        if config != {"format": expected_format}:
            raise ValueError("parser configuration must freeze the supported JSON format")
        if self.parser_config_digest != canonical_digest(config):
            raise ValueError("parser configuration digest does not match its frozen values")
        expected_membership = _membership_digest(
            self.benchmark_slug,
            self.revision,
            self.split,
            self.variant,
            self.sample_seed,
            self.selected_ids,
        )
        if self.membership_digest != expected_membership:
            raise ValueError("selected membership digest does not match the frozen plan")
        if self.rights_state == "approved" and self.rights_evidence_digest is None:
            raise ValueError("approved imports require an immutable rights evidence digest")
        if self.rights_state != "approved" and self.rights_evidence_digest is not None:
            raise ValueError("unapproved imports cannot carry an approval evidence digest")
        if len({link.child_item_id for link in self.family_links}) != len(self.family_links):
            raise ValueError("each selected child item may have at most one explicit family link")
        if any(link.child_item_id not in self.selected_ids for link in self.family_links):
            raise ValueError("family links may reference only selected child items")
        return self

    @property
    def plan_digest(self) -> str:
        return canonical_digest(self.model_dump(mode="json"))


def _validate_member_path(value: str) -> None:
    if (
        not value
        or not value.isascii()
        or value.startswith(("/", "\\"))
        or "\\" in value
        or "\x00" in value
        or ":" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or any(ord(char) < 0x20 for char in value)
    ):
        raise ValueError("source member must be a safe relative POSIX path")


def _id_belongs_to_split(slug: BenchmarkSlug, item_id: str) -> bool:
    if slug == "humaneval":
        return re.fullmatch(r"(?:test|HumanEval)/[0-9]+", item_id, re.ASCII) is not None
    if slug == "mbpp":
        if not item_id.isdecimal():
            return False
        return 11 <= int(item_id) <= 510
    return (
        re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}__[0-9]+",
            item_id,
            re.ASCII,
        )
        is not None
    )


def _validate_variant(slug: BenchmarkSlug, variant: str) -> None:
    allowed = {
        "humaneval": {"official"},
        "mbpp": {"original", "sanitized"},
        "swe-bench-verified": {"verified"},
    }[slug]
    if variant not in allowed:
        raise ValueError(f"{slug} requires an explicitly supported variant")


class ImportComponent(_StrictFrozenModel):
    component_key: Annotated[str, Field(min_length=1, max_length=128)]
    media_type: Annotated[str, Field(min_length=1, max_length=128)]
    content: bytes
    digest: Digest

    @model_validator(mode="after")
    def content_digest_matches(self) -> ImportComponent:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,127}", self.component_key, re.ASCII):
            raise ValueError("component key must be a lowercase ASCII identifier")
        if not re.fullmatch(
            r"[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+(?:; charset=utf-8)?", self.media_type
        ):
            raise ValueError("component media type is malformed")
        actual = "sha256:" + hashlib.sha256(self.content).hexdigest()
        if actual != self.digest:
            raise ValueError("component digest does not match exact component bytes")
        return self


class ImportItem(_StrictFrozenModel):
    item_id: _ImportText
    membership_index: int = Field(ge=0, le=99)
    state: ImportState
    source_record: bytes | None
    source_record_digest: Digest | None
    components: tuple[ImportComponent, ...]
    error_codes: tuple[Annotated[str, Field(min_length=1, max_length=96)], ...]
    source_date_value: str | None
    source_date_precision: SourceDatePrecision | None
    source_date_raw: str | None
    source_urls: tuple[_ImportText, ...]
    family_links: tuple[ImportFamilyLink, ...]
    self_source_exposure: Literal[True] = True
    source_public_exposure: bool
    independent_duplicate_eligible: Literal[False] = False

    @model_validator(mode="after")
    def validate_item_state(self) -> ImportItem:
        if not _ITEM_ID.fullmatch(self.item_id):
            raise ValueError("item ID must use the bounded source ID format")
        if not self.source_urls:
            raise ValueError("each selected item must retain its source/public exposure reference")
        for source_url in self.source_urls:
            _validate_public_source_url(source_url)
        if len({component.component_key for component in self.components}) != len(self.components):
            raise ValueError("item component keys must be unique")
        if (self.source_record is None) != (self.source_record_digest is None):
            raise ValueError("source record bytes and digest must be present together")
        if self.source_record is not None:
            actual = "sha256:" + hashlib.sha256(self.source_record).hexdigest()
            if self.source_record_digest != actual:
                raise ValueError("source record digest does not match the retained bytes")
        if self.state == "imported" and (self.error_codes or self.source_record is None):
            raise ValueError("imported items require source bytes and no errors")
        if self.state in {"incomplete", "missing", "blocked"} and not self.error_codes:
            raise ValueError("non-imported items require explicit error codes")
        if self.state == "missing" and (self.source_record is not None or self.components):
            raise ValueError("missing items cannot claim parsed source records or components")
        if self.source_date_value is None and self.source_date_precision is not None:
            raise ValueError("source date precision requires a parsed date value")
        if self.source_date_value is not None and self.source_date_precision is None:
            raise ValueError("parsed source date value requires its original precision")
        if self.source_date_raw is not None and self.source_date_value is not None:
            raise ValueError("retain either a recognized source date or an unparsed raw date")
        if any(link.child_item_id != self.item_id for link in self.family_links):
            raise ValueError("item lineage must target the item's exact source ID")
        return self


class BenchmarkImportResult(_StrictFrozenModel):
    plan_digest: Digest
    source_digest: Digest
    observed_source_digest: Digest | None
    membership_digest: Digest
    state: Literal["complete", "partial", "blocked"]
    items: tuple[ImportItem, ...]
    source_error_codes: tuple[Annotated[str, Field(min_length=1, max_length=96)], ...]

    @model_validator(mode="after")
    def validate_total_membership(self) -> BenchmarkImportResult:
        if len(self.items) != 100 or tuple(item.membership_index for item in self.items) != tuple(
            range(100)
        ):
            raise ValueError("import result must retain all 100 frozen membership positions")
        if len({item.item_id for item in self.items}) != 100:
            raise ValueError("import result membership IDs must be unique")
        if self.state != "blocked" and self.observed_source_digest != self.source_digest:
            raise ValueError("partial or complete imports must match their pinned source bytes")
        if self.state == "complete" and (
            self.source_error_codes or any(item.state != "imported" for item in self.items)
        ):
            raise ValueError("complete import cannot contain source or item errors")
        if self.state == "blocked" and any(item.state != "blocked" for item in self.items):
            raise ValueError("blocked import must retain every selected ID as blocked")
        return self


class ImportArtifactBindings(_StrictFrozenModel):
    """Verified artifact IDs produced by the existing artifact service."""

    source_artifact_id: UUID
    rights_evidence_artifact_id: UUID
    item_artifact_ids: tuple[tuple[_ImportText, UUID], ...]
    component_artifact_ids: tuple[tuple[_ImportText, _ImportText, UUID], ...]

    @model_validator(mode="after")
    def binding_keys_are_unique(self) -> ImportArtifactBindings:
        if len({key for key, _ in self.item_artifact_ids}) != len(self.item_artifact_ids):
            raise ValueError("raw item artifact bindings must have unique item IDs")
        component_keys = {(item_id, key) for item_id, key, _ in self.component_artifact_ids}
        if len(component_keys) != len(self.component_artifact_ids):
            raise ValueError("component artifact bindings must have unique item/component keys")
        return self
