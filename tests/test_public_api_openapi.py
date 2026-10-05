"""Success-response schemas are part of the REST contract, not just request validation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from runpy import run_path
from typing import Any

import httpx
from polycodebench_api.app import create_app
from polycodebench_api.dev_fixture import create_synthetic_release
from polycodebench_api.envelope import ApiEnvelope
from polycodebench_api.submission_routes import EndpointDecisionResult, EndpointRegistrationResult
from polycodebench_api.submissions import ModelSubmission, SubmissionReviewView
from polycodebench_publication.aggregation import PublicationModel
from polycodebench_publication.projections import (
    ComparisonResult,
    LanguageProfile,
    LeaderboardEntry,
    Methodology,
    ModelProfile,
    PublicScorecard,
    PublicTaskContent,
    ReleaseSummary,
    TaskSummary,
)
from polycodebench_publication.releases import ReleaseStore
from pydantic import TypeAdapter

SUCCESS_RESPONSES: tuple[tuple[str, str, str, type[Any], bool], ...] = (
    ("get", "/v1/releases", "200", ReleaseSummary, True),
    ("get", "/v1/releases/{release_id}", "200", ReleaseSummary, False),
    ("get", "/v1/leaderboard", "200", LeaderboardEntry, True),
    ("get", "/v1/models/{model_config_id}", "200", ModelProfile, False),
    ("get", "/v1/languages/{language_id}", "200", LanguageProfile, False),
    ("get", "/v1/compare", "200", ComparisonResult, False),
    ("get", "/v1/tasks", "200", TaskSummary, True),
    ("get", "/v1/tasks/{task_id}", "200", TaskSummary, False),
    ("get", "/v1/tasks/{task_id}/content", "200", PublicTaskContent, False),
    ("get", "/v1/scorecards/{scorecard_id}", "200", PublicScorecard, False),
    ("get", "/v1/methodology/{version}", "200", Methodology, False),
    ("post", "/v1/model-submissions", "201", ModelSubmission, False),
    ("get", "/v1/model-submissions/{submission_id}", "200", ModelSubmission, False),
    ("get", "/v1/admin/model-submissions", "200", SubmissionReviewView, True),
    (
        "get",
        "/v1/admin/model-submissions/{submission_id}",
        "200",
        SubmissionReviewView,
        False,
    ),
    (
        "post",
        "/v1/admin/model-submissions/{submission_id}/reject",
        "200",
        SubmissionReviewView,
        False,
    ),
    (
        "post",
        "/v1/admin/model-submissions/{submission_id}/approve",
        "202",
        SubmissionReviewView,
        False,
    ),
    ("post", "/v1/admin/model-endpoints", "201", EndpointRegistrationResult, False),
    (
        "post",
        "/v1/admin/model-endpoints/{endpoint_id}/decision",
        "200",
        EndpointDecisionResult,
        False,
    ),
)


def _resolve_schema(schema: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        schema = components[name]
    return schema


def test_every_route_documents_its_success_envelope_and_status(tmp_path: Path) -> None:
    app = create_app(
        store=ReleaseStore(tmp_path / "openapi.sqlite3"),
        cursor_key=b"public-openapi-contract-test-key-000",
    )
    openapi = app.openapi()
    components = openapi["components"]["schemas"]

    for method, path, status, payload_type, is_list in SUCCESS_RESPONSES:
        operation = openapi["paths"][path][method]
        response = operation["responses"][status]
        response_schema = response["content"]["application/json"]["schema"]
        envelope_schema = _resolve_schema(response_schema, components)
        properties = envelope_schema["properties"]
        assert set(properties) == {"data", "meta"}, f"{method.upper()} {path} envelope"

        data_schema = properties["data"]
        if is_list:
            assert data_schema["type"] == "array", f"{method.upper()} {path} data"
            data_schema = data_schema["items"]
        payload_schema = _resolve_schema(data_schema, components)
        assert payload_schema.get("title") == payload_type.__name__, (
            f"{method.upper()} {path} payload"
        )
        if issubclass(payload_type, PublicationModel):
            required = set(payload_schema.get("required", []))
            assert {"kind", "schema_version"} <= required, (
                f"{method.upper()} {path} serialization discriminators"
            )

        meta_schema = _resolve_schema(properties["meta"], components)
        meta_properties = meta_schema["properties"]
        assert set(meta_schema.get("required", [])) == set(meta_properties)
        assert "release_digest" in meta_properties
        assert "kind" not in meta_properties and "schema_version" not in meta_properties


def test_checked_in_rest_openapi_snapshot_matches_the_app() -> None:
    generator = run_path(
        str(Path(__file__).resolve().parents[1] / "scripts" / "export_public_api_openapi.py")
    )
    generated_outputs = generator["generated_outputs"]

    for path, expected in generated_outputs().items():
        assert path.is_file(), f"generated API contract is missing: {path}"
        assert path.read_text(encoding="utf-8") == expected, (
            f"generated API contract is stale: {path}"
        )


def test_public_route_json_validates_against_the_declared_envelopes(tmp_path: Path) -> None:
    store = ReleaseStore(tmp_path / "responses.sqlite3")
    release_id = create_synthetic_release(store)
    app = create_app(store=store, cursor_key=b"public-response-contract-test-key-00")

    async def verify() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            cases: tuple[tuple[str, TypeAdapter[Any]], ...] = (
                ("/v1/releases", TypeAdapter(ApiEnvelope[list[ReleaseSummary]])),
                (f"/v1/releases/{release_id}", TypeAdapter(ApiEnvelope[ReleaseSummary])),
                (
                    f"/v1/leaderboard?release={release_id}",
                    TypeAdapter(ApiEnvelope[list[LeaderboardEntry]]),
                ),
                (
                    f"/v1/models/synthetic-code-a?release={release_id}",
                    TypeAdapter(ApiEnvelope[ModelProfile]),
                ),
                (
                    f"/v1/languages/python?release={release_id}",
                    TypeAdapter(ApiEnvelope[LanguageProfile]),
                ),
                (
                    f"/v1/compare?release={release_id}&models=synthetic-code-a&models=synthetic-code-c",
                    TypeAdapter(ApiEnvelope[ComparisonResult]),
                ),
                (f"/v1/tasks?release={release_id}", TypeAdapter(ApiEnvelope[list[TaskSummary]])),
                (
                    f"/v1/tasks/synthetic-task-example?release={release_id}",
                    TypeAdapter(ApiEnvelope[TaskSummary]),
                ),
                (
                    f"/v1/tasks/synthetic-task-example/content?release={release_id}",
                    TypeAdapter(ApiEnvelope[PublicTaskContent]),
                ),
                (
                    f"/v1/scorecards/synthetic-scorecard-a?release={release_id}",
                    TypeAdapter(ApiEnvelope[PublicScorecard]),
                ),
                (
                    "/v1/methodology/synthetic-ui-fixture-v1",
                    TypeAdapter(ApiEnvelope[Methodology]),
                ),
            )
            for path, response_adapter in cases:
                response = await client.get(path)
                assert response.status_code == 200, path
                response_adapter.validate_json(response.content)

    asyncio.run(verify())
