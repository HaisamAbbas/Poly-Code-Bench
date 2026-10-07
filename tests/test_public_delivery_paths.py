"""The CDN caches only public API routes, never owner or artifact endpoints."""

from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _cloudfront_public_patterns() -> set[str]:
    terraform = (ROOT / "infra/terraform/modules/public_delivery/main.tf").read_text(
        encoding="utf-8"
    )
    behavior = re.search(r"for_each\s*=\s*toset\(\[(.*?)\]\)", terraform, flags=re.DOTALL)
    assert behavior is not None, "public API cache behavior must use an explicit route allowlist"
    return set(re.findall(r'"(/v1/[^"\s]+)"', behavior.group(1)))


def test_cloudfront_public_cache_patterns_match_the_public_api_surface() -> None:
    snapshot = json.loads((ROOT / "packages/api/openapi.v1.json").read_text(encoding="utf-8"))
    api_paths = set(snapshot["paths"])
    patterns = _cloudfront_public_patterns()
    expected = {
        "/v1/compare",
        "/v1/languages/*",
        "/v1/leaderboard",
        "/v1/methodology/*",
        "/v1/models/*",
        "/v1/releases",
        "/v1/releases/*",
        "/v1/scorecards/*",
        "/v1/tasks",
        "/v1/tasks/*",
    }

    assert patterns == expected
    for route_pattern in patterns:
        matching_routes = {path for path in api_paths if fnmatch.fnmatchcase(path, route_pattern)}
        assert matching_routes, f"CloudFront pattern does not match an API route: {route_pattern}"
    for private_route in (
        "/v1/model-submissions",
        "/v1/model-submissions/00000000-0000-4000-8000-000000000000",
        "/v1/admin/model-submissions",
        "/v1/admin/model-endpoints",
    ):
        assert not any(fnmatch.fnmatchcase(private_route, pattern) for pattern in patterns)

    terraform = (ROOT / "infra/terraform/modules/public_delivery/main.tf").read_text(
        encoding="utf-8"
    )
    artifact_behavior = re.search(
        r'ordered_cache_behavior\s*\{\s*path_pattern\s*=\s*"/v1/artifacts/\*"'
        r"(?P<body>.*?)\n  \}",
        terraform,
        flags=re.DOTALL,
    )
    assert artifact_behavior is not None
    assert re.search(
        r"cache_policy_id\s*=\s*data\.aws_cloudfront_cache_policy\.disabled\.id",
        artifact_behavior.group("body"),
    )
    assert re.search(
        r"origin_request_policy_id\s*=\s*aws_cloudfront_origin_request_policy\.public_artifact\.id",
        artifact_behavior.group("body"),
    )
    assert re.search(
        r'resource\s+"aws_cloudfront_origin_request_policy"\s+"public_artifact"\s*\{.*?'
        r'query_string_behavior\s*=\s*"whitelist".*?"release",\s*"token"',
        terraform,
        flags=re.DOTALL,
    )
