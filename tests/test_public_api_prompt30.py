"""Prompt 30 page data must remain tied to the published release projections."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from fastapi import FastAPI
from polycodebench_api.app import create_app
from polycodebench_api.dev_fixture import create_synthetic_release
from polycodebench_publication.releases import ReleaseStore


async def _assert_published_routes(app: FastAPI, release_id: str) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        releases = (await client.get("/v1/releases?limit=200")).json()
        assert releases["meta"]["current_release_id"] == release_id
        assert releases["data"][0]["fixture_kind"] == "synthetic_internal"

        board = (
            await client.get("/v1/leaderboard", params={"release": release_id, "limit": 200})
        ).json()
        assert {entry["model_config_id"] for entry in board["data"]} == {
            "synthetic-code-a",
            "synthetic-code-b",
            "synthetic-answer-only",
        }
        assert {
            definition["metric_id"] for definition in board["meta"]["registry"]["definitions"]
        } >= {
            "code_score",
            "pass_rate",
        }

        source = (
            await client.get("/v1/scorecards/synthetic-scorecard-a", params={"release": release_id})
        ).json()
        assert source["data"]["release_id"] == release_id
        assert "code_score" in {metric["metric_id"] for metric in source["data"]["metrics"]}

        javascript = (
            await client.get("/v1/languages/javascript", params={"release": release_id})
        ).json()["data"]
        javascript_profile = javascript["entries"][0]
        assert [row["dimension"] for row in javascript_profile["dimensions"]] == ["correctness"]
        assert javascript_profile["tool_coverage"][0][0] == "eslint"

        answer_only = (
            await client.get("/v1/models/synthetic-answer-only", params={"release": release_id})
        ).json()["data"]
        assert answer_only["dimensions"] == []
        assert "code_score" not in {metric["metric_id"] for metric in answer_only["metrics"]}

        missing = await client.get("/v1/languages/not-published", params={"release": release_id})
        assert missing.status_code == 404


def test_prompt30_pages_consume_published_release_data(tmp_path: Path) -> None:
    store = ReleaseStore(tmp_path / "prompt30.sqlite3")
    release_id = create_synthetic_release(store)
    app = create_app(store=store, cursor_key=b"prompt30-test-cursor-key-00000000")
    asyncio.run(_assert_published_routes(app, release_id))
