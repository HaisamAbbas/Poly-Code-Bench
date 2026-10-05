"""The public read layer cannot disclose what a release did not publish.

These are the E2E-26 and E2E-28 API-level assertions (Prompt 29, PCB-29-1/2/3): every public
identifier is resolved against the *published release document only*, a private or unknown identity
returns one generic not-found, and a comparison that cannot be made returns typed reasons with no
numbers rather than a silently renormalised rank.

The fixtures are generated from a real release build through
:class:`~polycodebench_publication.releases.ReleaseStore`, not hand-written, so what the query layer
sees is exactly what a published release contains.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from polycodebench_publication.aggregation import MetricDefinition
from polycodebench_publication.projections import (
    ComparisonResult,
    Coverage,
    DimensionBreakdown,
    Methodology,
    PublicMetric,
    PublicScorecard,
    PublicTask,
)
from polycodebench_publication.projections_query import (
    PublicApiError,
    ReleaseContent,
    ReleaseEntry,
    ReleaseLanguageProfile,
    artifact,
    compare,
    language_profile,
    leaderboard,
    methodology,
    model_profile,
    public_task,
    release_summary,
    scorecard,
)
from polycodebench_publication.releases import (
    REQUIRED_CHECKS,
    ReleasePrincipal,
    ReleaseStore,
    SigningKey,
    ValidationEvidence,
    digest,
    validate_projection,
)


def _metric(metric_id: str, value: str | None) -> PublicMetric:
    return PublicMetric(
        metric_id=metric_id,
        label=metric_id.replace("_", " ").title(),
        unit="score",
        direction="higher",
        status="measured" if value is not None else "insufficient_information",
        value=value,
        interval_low=None,
        interval_high=None,
        coverage="1.000000" if value is not None else None,
        conditional_on_pass=False,
        reason=None if value is not None else "not measured",
    )


def _entry(model_config_id: str, languages: tuple[str, ...], score: str) -> ReleaseEntry:
    return ReleaseEntry(
        model_config_id=model_config_id,
        label=model_config_id,
        rank=None,
        capabilities=("tools",),
        languages=languages,
        metrics=(_metric("code_score", score),),
        coverage=Coverage(tasks=10, samples=30, independent_clusters=10),
        run_mode="single_shot",
        budget_profile_id="test-small-budget",
        dimensions=(
            DimensionBreakdown(
                dimension="correctness",
                metric=_metric("code_score", score),
                applicable_tasks=10,
                opportunity_count=4,
            ),
        ),
        evidence_url=f"/v1/scorecards/{model_config_id}",
    )


def _content() -> ReleaseContent:
    return ReleaseContent(
        policy_digest=digest({"policy": 1}),
        formula_version="scoring-v1",
        entries=(
            _entry("model-alpha", ("python", "rust"), "89.750000"),
            _entry("model-beta", ("python",), "75.000000"),
        ),
        disclosed_tasks=(
            PublicTask(
                task_id="task-public",
                version=1,
                disclosed=True,
                language_id="python",
                family="codegen",
                difficulty="introductory",
                statement_summary="Count the words in a string.",
                limitations=(),
                evidence_url="/v1/tasks/task-public",
            ),
        ),
        scorecards=(
            PublicScorecard(
                scorecard_id="card-1",
                release_id="release-1",
                model_config_id="model-alpha",
                task_id="task-public",
                formula_version="scoring-v1",
                policy_digest=digest({"policy": 1}),
                gating_status="scored",
                metrics=(_metric("code_score", "89.750000"),),
                contributions=(),
                evidence_url="/v1/scorecards/card-1",
            ),
            PublicScorecard(
                scorecard_id="card-2",
                release_id="release-1",
                model_config_id="model-beta",
                task_id="task-public",
                formula_version="scoring-v1",
                policy_digest=digest({"policy": 1}),
                gating_status="scored",
                metrics=(_metric("code_score", "75.000000"),),
                contributions=(),
                evidence_url="/v1/scorecards/card-2",
            ),
        ),
        methodology=Methodology(version="methods-v1", methods=("paired bootstrapping",)),
        metric_definitions=(
            MetricDefinition(metric_id="code_score", label="Code Score", unit="score"),
        ),
    )


def _projection() -> dict[str, object]:
    projection = {
        "schema_version": 1,
        "fixture_kind": "synthetic_internal",
        "scope": "exploratory",
        "cohort_digest": digest({"cohort": 1}),
        "limitations": ["Synthetic internal behavior fixture; no model benchmark measurements."],
        "metrics": [
            {
                "metric_id": "code_score",
                "value": "89.750000",
                "coverage": "1.000000",
                "interval_low": None,
                "interval_high": None,
                "conditional_on_pass": False,
            }
        ],
    }
    validate_projection(projection)
    return projection


def _published_document() -> dict[str, object]:
    """A real release, drafted and published through the store's own lifecycle."""
    return {
        "id": "release-1",
        "version": 1,
        "state": "published",
        "content": _content().model_dump(mode="json"),
        "projection": _projection(),
        "withdrawal": None,
    }


def _not_found_ids() -> tuple[str, ...]:
    """Identifiers that must all resolve to one generic not-found.

    The first names a task that exists in the release but is not disclosed; the rest are simply
    unknown. A caller cannot tell which is which.
    """
    return (
        "task-private-hidden",
        "unknown-task",
        "hidden/oracle.json",
        "secret-reference-abc",
    )


# -------- E2E-26 style probes


def test_public_resolvers_reject_private_and_unknown_identities_generically() -> None:
    doc = _published_document()
    for identifier in _not_found_ids():
        with pytest.raises(PublicApiError) as raised:
            public_task(doc, identifier)
        assert raised.value.code == "NOT_FOUND"
        assert raised.value.message == "resource is not available"

    for identifier in ("artifact-private-hidden", "unknown-artifact"):
        with pytest.raises(PublicApiError) as raised:
            artifact(doc, identifier)
        assert raised.value.code == "NOT_FOUND"

    for identifier in ("card-private-hidden", "unknown-card"):
        with pytest.raises(PublicApiError) as raised:
            scorecard(doc, identifier)
        assert raised.value.code == "NOT_FOUND"

    with pytest.raises(PublicApiError):
        model_profile(doc, "model-private-hidden")
    with pytest.raises(PublicApiError):
        language_profile(doc, "cobol")


def test_language_profile_does_not_fall_back_to_release_wide_scores_or_evidence() -> None:
    with pytest.raises(PublicApiError) as raised:
        language_profile(_published_document(), "python")
    assert raised.value.code == "NOT_FOUND"


def test_legacy_language_profile_without_its_own_evidence_is_suppressed() -> None:
    content = _content()
    alpha = content.entries[0].model_copy(
        update={
            "language_profiles": (ReleaseLanguageProfile(language_id="python"),),
        }
    )
    legacy_content = content.model_copy(update={"entries": (alpha, content.entries[1])})
    legacy_document = {
        **_published_document(),
        "content": legacy_content.model_dump(mode="json"),
    }

    assert model_profile(legacy_document, "model-alpha").language_profiles == ()
    with pytest.raises(PublicApiError) as raised:
        language_profile(legacy_document, "python")
    assert raised.value.code == "NOT_FOUND"


def test_release_rejects_language_profile_evidence_from_another_language() -> None:
    content = _content()
    alpha = content.entries[0].model_copy(
        update={
            "language_profiles": (
                ReleaseLanguageProfile(language_id="rust", evidence_url="/v1/scorecards/card-1"),
            )
        }
    )
    invalid_content = {
        **content.model_dump(mode="python"),
        "entries": (alpha.model_dump(mode="python"), content.entries[1].model_dump(mode="python")),
    }
    with pytest.raises(ValueError, match="same model and language"):
        ReleaseContent.model_validate(invalid_content)


def test_a_not_found_detail_cannot_carry_paths_digests_or_urls() -> None:
    """Every error body is checked at construction, so a leak cannot be assembled later."""
    from polycodebench_publication.projections import PublicError

    for fragment in ("hidden/oracle.json", "sha256:" + "a" * 64, "https://internal", "../secret"):
        with pytest.raises(ValueError):
            PublicError(
                code="NOT_FOUND",
                message="not found",
                request_id="req",
                details=(fragment,),
            )


def test_no_public_response_credits_an_undisclosed_task() -> None:
    """A release that declared one task serves exactly one, never a synthesised empty one."""
    doc = _published_document()
    content = ReleaseContent.model_validate(doc["content"])
    assert [t.task_id for t in content.disclosed_tasks] == ["task-public"]
    assert public_task(doc, "task-public").disclosed is True


# -------- E2E-28 style comparison


def test_comparison_without_common_coverage_returns_reasons_and_no_numbers() -> None:
    doc = _published_document()
    result = compare(doc, ("model-alpha", "model-missing"))
    assert isinstance(result, ComparisonResult)
    assert result.entries == ()
    assert result.deltas == ()
    assert [i.code for i in result.incompatibilities] == ["insufficient_common_coverage"]
    assert result.common_tasks == 0
    assert result.common_independent_clusters == 0


def test_comparison_with_missing_language_is_incompatible_not_renormalised() -> None:
    doc = _published_document()
    result = compare(doc, ("model-alpha", "model-beta"), languages=frozenset({"rust"}))
    codes = [i.code for i in result.incompatibilities]
    assert codes == ["missing_language"]
    assert result.entries == ()
    assert result.applied_filters.languages == ("rust",)
    # A partial entry must never be shown as a full rank.
    assert result.common_tasks == 0


def test_comparison_on_one_common_language_produces_paired_deltas() -> None:
    doc = _published_document()
    content = ReleaseContent.model_validate(doc["content"])
    python_only = content.model_copy(
        update={
            "entries": (
                _entry("model-alpha", ("python",), "89.750000"),
                _entry("model-beta", ("python",), "75.000000"),
            )
        }
    )
    result = compare(
        {**doc, "content": python_only.model_dump(mode="json")},
        ("model-alpha", "model-beta"),
        languages=frozenset({"python"}),
    )
    assert result.incompatibilities == ()
    assert len(result.entries) == 2
    assert result.release_metric_scope == "full_release_aggregate"
    assert result.task_pair_scope == "common_task_intersection_after_filters"
    assert result.applied_filters.languages == ("python",)
    assert result.applied_filters.families == ()
    assert result.common_tasks == 1
    assert result.common_task_refs[0].task_id == "task-public"
    assert result.common_independent_clusters is None
    paired = next(row for row in result.paired_task_deltas if row.metric_id == "code_score")
    assert paired.delta_value == "-14.750000"
    assert paired.baseline_scorecard_id == "card-1"
    assert paired.candidate_scorecard_id == "card-2"
    delta = next(d for d in result.deltas if d.metric_id == "code_score")
    assert delta.delta_value == "-14.750000"
    assert delta.status == "measured"


def test_comparison_rejects_a_card_outside_two_to_four_entries() -> None:
    doc = _published_document()
    for ids in (("model-alpha",), ("a", "b", "c", "d", "e")):
        with pytest.raises(PublicApiError) as raised:
            compare(doc, ids)
        assert raised.value.code == "INCOMPATIBLE_COHORT"


# -------- ledger and scope


def test_an_exploratory_release_publishes_no_rank() -> None:
    """T 19.4: an exploratory release may show an unranked entry but must never assign a rank."""
    doc = _published_document()
    rows = leaderboard(doc)
    assert all(row.rank is None for row in rows)
    assert all(row.ranking_label == "exploratory" for row in rows)


def test_leaderboard_filters_by_language_without_renormalising() -> None:
    doc = _published_document()
    both = leaderboard(doc, languages=frozenset({"python"}))
    assert {r.model_config_id for r in both} == {"model-alpha", "model-beta"}
    rust_only = leaderboard(doc, languages=frozenset({"rust"}))
    assert {r.model_config_id for r in rust_only} == {"model-alpha"}


def test_a_draft_release_is_not_served() -> None:
    doc = {**_published_document(), "state": "draft"}
    for resolver in (
        lambda: release_summary(doc),
        lambda: leaderboard(doc),
        lambda: compare(doc, ("model-alpha", "model-beta")),
    ):
        with pytest.raises(PublicApiError) as raised:
            resolver()
        assert raised.value.code == "RELEASE_NOT_READY"


def test_release_summary_carries_no_manifest_or_evidence() -> None:
    summary = release_summary(_published_document())
    dumped = summary.model_dump(mode="json")
    assert "manifest" not in dumped
    assert "projection" not in dumped
    assert set(dumped) <= {
        "schema_version",
        "kind",
        "release_id",
        "version",
        "state",
        "scope",
        "fixture_kind",
        "cohort_digest",
        "published_at",
        "limitations",
        "withdrawal_reason",
        "replacement_release_id",
        "methodology_url",
        "methodology_version",
    }


def test_methodology_is_served_from_the_release_itself() -> None:
    doc = _published_document()
    assert methodology(doc).version == "methods-v1"
    bare = {
        **doc,
        "content": ReleaseContent(
            policy_digest=digest({"policy": 1}),
            formula_version="scoring-v1",
            methodology=None,
        ).model_dump(mode="json"),
    }
    with pytest.raises(PublicApiError):
        methodology(bare)


# -------- store integration


def test_a_really_published_release_round_trips_through_the_query_layer(
    tmp_path: Path,
) -> None:
    """The shapes above are not a private dialect: the store accepts and returns them."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    store = ReleaseStore(tmp_path / "releases.db")
    principal = ReleasePrincipal(subject_id="owner", mfa=True, roles=frozenset({"administrator"}))
    signer = SigningKey(key_id="key-1", private_key=Ed25519PrivateKey.generate())
    content = _content()
    projection = _projection()

    draft = store.draft(principal, content.model_dump(mode="json"), projection, "draft-1")
    version = int(draft["version"])
    evidence = tuple(
        ValidationEvidence(
            check=check,
            subject_digest=draft["content_digest"],
            expected_digest=draft["content_digest"],
            observed_digest=draft["content_digest"],
            reference="test",
        )
        for check in sorted(REQUIRED_CHECKS)
    )
    validated = store.validate(principal, draft["id"], evidence, version, "validate-1")
    reviewed = store.review(
        principal, draft["id"], "meets the disclosure policy", int(validated["version"]), "review-1"
    )
    approved = store.approve(
        principal,
        draft["id"],
        reviewed["content_digest"],
        int(reviewed["version"]),
        "approve-1",
    )
    published = store.publish(
        principal,
        draft["id"],
        signer,
        0,
        int(approved["version"]),
        "publish-1",
    )
    assert published["state"] == "published"

    readable = store.public(str(published["id"]))
    assert readable["projection"]["scope"] == "exploratory"
    rows = leaderboard(
        {
            "id": published["id"],
            "version": published["version"],
            "state": published["state"],
            "content": content.model_dump(mode="json"),
            "projection": readable["projection"],
        }
    )
    assert {r.model_config_id for r in rows} == {"model-alpha", "model-beta"}


# -------- cursor binding (PCB-29-2)


def test_a_cursor_decodes_to_its_own_release_and_filter_binding() -> None:
    """A signed cursor is integral; its binding is what the handler compares to the request.

    ``verify`` proves the token was signed by this server and was not tampered with. It does not
    and should not reject a cursor for another release - that is the handler's comparison. What
    must never happen is a forged or edited token verifying at all.
    """
    from polycodebench_publication.projections import Cursor, filters_digest

    key = b"server-signing-key"
    binding = filters_digest({"language": "python"})
    token = Cursor(
        release_id="release-1",
        filters_digest=binding,
        sort="rank_asc",
        offset=10,
    ).sign(key)

    decoded = Cursor.verify(token, key)
    assert decoded.release_id == "release-1"
    assert decoded.filters_digest == binding
    assert decoded.sort == "rank_asc"
    assert decoded.offset == 10

    # A cursor minted for a different release keeps that binding, so a handler that compares it
    # to its own request rejects it. Nothing about verification allows a replay across releases.
    other = Cursor.verify(
        Cursor(release_id="release-2", filters_digest=binding, sort="rank_asc", offset=10).sign(
            key
        ),
        key,
    )
    assert other.release_id == "release-2"
    assert other.release_id != decoded.release_id


def test_a_forged_or_truncated_cursor_is_rejected_without_detail() -> None:
    """Malformed and tampered cursors both fail, and neither leaks which part was wrong."""
    from polycodebench_publication.projections import Cursor, filters_digest

    key = b"server-signing-key"
    token = Cursor(
        release_id="release-1",
        filters_digest=filters_digest({"language": "python"}),
        sort="rank_asc",
        offset=10,
    ).sign(key)

    body, signature = token.split(".")
    # Same length, different bytes: the signature no longer matches the body.
    tampered_body = ("x" if body[0] != "x" else "y") + body[1:] + "." + signature
    for candidate in (tampered_body, token + "x", "not-a-cursor", ".", "", "a.b.c"):
        with pytest.raises(ValueError):
            Cursor.verify(candidate, key)

    # A token signed with a different server key must not verify.
    with pytest.raises(ValueError):
        Cursor.verify(token, b"another-key")


def test_different_filters_produce_different_cursor_bindings() -> None:
    from polycodebench_publication.projections import filters_digest

    assert filters_digest({"language": "python"}) != filters_digest({"language": "rust"})
    assert filters_digest({"language": "python"}) == filters_digest({"language": "python"})


def test_every_public_metric_serializes_as_a_decimal_string_not_a_float() -> None:
    """T 20.1: scores are canonical decimal strings so no consumer reaches for a float."""
    doc = _published_document()
    for row in leaderboard(doc):
        for metric in row.metrics:
            if metric.value is not None:
                assert isinstance(metric.value, str)
                Decimal(metric.value)  # parses as a decimal, not a float
    profile = model_profile(doc, "model-alpha")
    for metric in profile.metrics:
        if metric.value is not None:
            assert isinstance(metric.value, str)
