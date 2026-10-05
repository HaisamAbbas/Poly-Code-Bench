"""Prompt 31 public compare, evidence drilldown, privacy and frozen-method contracts."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from polycodebench_api.app import create_app
from polycodebench_api.dev_fixture import create_synthetic_release
from polycodebench_publication.projections import PublicTask
from polycodebench_publication.projections_query import ReleaseContent
from polycodebench_publication.releases import ReleasePrincipal, ReleaseStore
from pydantic import ValidationError


async def _assert_prompt31_routes(app: FastAPI, release_id: str) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        compatible = await client.get(
            "/v1/compare",
            params={"release": release_id, "models": ["synthetic-code-a", "synthetic-code-c"]},
        )
        assert compatible.status_code == 200
        comparison = compatible.json()["data"]
        assert comparison["common_tasks"] == 3
        assert comparison["common_independent_clusters"] is None
        assert comparison["incompatibilities"] == []
        assert {row["task_id"] for row in comparison["common_task_refs"]} == {
            "synthetic-task-example",
            "synthetic-task-rust",
            "synthetic-task-javascript",
        }
        python_delta = next(
            row
            for row in comparison["paired_task_deltas"]
            if row["task_id"] == "synthetic-task-example" and row["metric_id"] == "code_score"
        )
        assert python_delta["baseline_value"] == "92.000000"
        assert python_delta["candidate_value"] == "80.000000"
        assert python_delta["delta_value"] == "-12.000000"
        assert python_delta["baseline_scorecard_id"] == "synthetic-scorecard-a"
        assert python_delta["candidate_scorecard_id"] == "synthetic-scorecard-c"

        incompatible = await client.get(
            "/v1/compare",
            params={"release": release_id, "models": ["synthetic-code-a", "synthetic-code-b"]},
        )
        assert incompatible.status_code == 200
        incompatible_data = incompatible.json()["data"]
        assert incompatible_data["entries"] == []
        assert incompatible_data["paired_task_deltas"] == []
        assert {row["code"] for row in incompatible_data["incompatibilities"]} >= {
            "protocol_mismatch",
            "budget_mismatch",
        }

        task_list = await client.get("/v1/tasks", params={"release": release_id, "limit": 2})
        assert task_list.status_code == 200
        assert task_list.json()["meta"]["returned"] == 2
        assert task_list.json()["meta"]["next_cursor"]
        list_body = task_list.text
        assert "private-heldout" not in list_body
        assert "private/oracle-review" not in list_body

        content = await client.get(
            "/v1/tasks/synthetic-task-example/content", params={"release": release_id}
        )
        assert content.status_code == 200
        content_data = content.json()["data"]
        assert "<script>" in content_data["submitted_patches"][0]["diff_text"]
        assert "private/oracle-review" not in content.text
        assert "private-heldout" not in content.text

        unknown_task = await client.get(
            "/v1/tasks/private-heldout-candidate", params={"release": release_id}
        )
        unknown_card = await client.get(
            "/v1/scorecards/private-heldout-scorecard", params={"release": release_id}
        )
        assert unknown_task.status_code == unknown_card.status_code == 404
        assert unknown_task.json()["error"]["message"] == unknown_card.json()["error"]["message"]
        assert "private-heldout" not in unknown_task.text
        assert "private-heldout" not in unknown_card.text

        scorecard = await client.get(
            "/v1/scorecards/synthetic-scorecard-a", params={"release": release_id}
        )
        assert scorecard.status_code == 200
        card_data = scorecard.json()["data"]
        assert card_data["redacted_evidence_count"] == 1
        refs = [ref for row in card_data["contributions"] for ref in row["evidence_refs"]]
        assert refs == [
            "synthetic-source-python-v1",
            "synthetic-patch-a-python",
            "synthetic-finding-a-python",
        ]
        assert "private/oracle-review" not in scorecard.text
        assert "private-heldout" not in scorecard.text

        methods = await client.get(
            "/v1/methodology/synthetic-ui-fixture-v1", params={"release": release_id}
        )
        assert methods.status_code == 200
        assert methods.json()["data"]["version"] == "synthetic-ui-fixture-v1"
        assert methods.json()["meta"]["release_id"] == release_id
        wrong_version = await client.get(
            "/v1/methodology/another-version", params={"release": release_id}
        )
        assert wrong_version.status_code == 404


def test_prompt31_comparison_evidence_and_privacy_routes(tmp_path: Path) -> None:
    store = ReleaseStore(tmp_path / "prompt31.sqlite3")
    release_id = create_synthetic_release(store)
    app = create_app(store=store, cursor_key=b"prompt31-test-cursor-key-00000000")
    asyncio.run(_assert_prompt31_routes(app, release_id))


def test_prompt31_withdrawal_keeps_original_method_and_names_successor(tmp_path: Path) -> None:
    store = ReleaseStore(tmp_path / "prompt31-history.sqlite3")
    old_id = create_synthetic_release(store)
    new_id = create_synthetic_release(store)
    app = create_app(store=store, cursor_key=b"prompt31-history-cursor-key-0000")

    async def verify() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            listing_before = await client.get("/v1/releases")
            assert listing_before.headers["cache-control"] == "public, no-cache, must-revalidate"
            old_listing_etag = listing_before.headers["etag"]
            notice_before = await client.get(f"/v1/releases/{old_id}")
            assert notice_before.json()["data"]["state"] == "published"
            assert notice_before.headers["cache-control"] == "public, no-cache, must-revalidate"
            old_notice_etag = notice_before.headers["etag"]

            response = await client.get(f"/v1/methodology/synthetic-ui-fixture-v1?release={old_id}")
            assert response.status_code == 200
            assert response.json()["meta"]["release_id"] == old_id
            assert response.headers["cache-control"] == "public, max-age=31536000, immutable"

            old = store.get(old_id)
            pointer = store.current()
            store.withdraw(
                principal=ReleasePrincipal(
                    subject_id="prompt31-reviewer", roles=frozenset({"administrator"})
                ),
                release_id=old_id,
                reason="Successor corrects the public release record.",
                expected_version=int(old["version"]),
                expected_generation=int(pointer["generation"]),
                request_id="prompt31-withdraw",
                replacement_release_id=new_id,
            )

            listing_after = await client.get(
                "/v1/releases", headers={"If-None-Match": old_listing_etag}
            )
            assert listing_after.status_code == 200
            assert listing_after.headers["etag"] != old_listing_etag
            assert listing_after.headers["cache-control"] == "public, no-cache, must-revalidate"
            assert any(
                row["release_id"] == old_id and row["state"] == "withdrawn"
                for row in listing_after.json()["data"]
            )

            changed_notice = await client.get(
                f"/v1/releases/{old_id}", headers={"If-None-Match": old_notice_etag}
            )
            assert changed_notice.status_code == 200
            assert changed_notice.headers["etag"] != old_notice_etag
            assert changed_notice.headers["cache-control"] == "public, no-cache, must-revalidate"
            assert changed_notice.json()["data"]["state"] == "withdrawn"
            assert changed_notice.json()["data"]["replacement_release_id"] == new_id

            unchanged_notice = await client.get(
                f"/v1/releases/{old_id}", headers={"If-None-Match": changed_notice.headers["etag"]}
            )
            assert unchanged_notice.status_code == 304
            assert unchanged_notice.headers["cache-control"] == "public, no-cache, must-revalidate"

    asyncio.run(verify())


def test_private_task_cannot_enter_the_public_disclosure_projection() -> None:
    private_task = PublicTask(
        task_id="private-heldout-task",
        version=1,
        disclosed=False,
        language_id="python",
        family="algorithmic",
        difficulty="introductory",
        statement_summary="This private task must not enter public release content.",
        evidence_url="/v1/tasks/private-heldout-task",
    )
    with pytest.raises(ValidationError, match="cannot include a private task"):
        ReleaseContent(
            policy_digest="test-policy",
            formula_version="test-formula",
            disclosed_tasks=(private_task,),
        )
