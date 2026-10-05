"""Generate the REST OpenAPI snapshot and its shared web TypeScript schemas."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

OPENAPI_JSON = ROOT / "packages/api/openapi.v1.json"


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _openapi_document() -> dict[str, Any]:
    """Build OpenAPI from the app factory with isolated development-only dependencies."""
    variables = (
        "PCB_ENVIRONMENT",
        "PCB_PUBLIC_RELEASE_BACKEND",
        "PCB_RELEASE_STORE_PATH",
        "PCB_PUBLICATION_TARGET",
        "PCB_DATABASE_URL",
        "PCB_API_IDENTITY_FILE",
        "PCB_WEB_AUTH_SIGNING_KEY",
        "PCB_CURSOR_SIGNING_KEY",
    )
    previous = {name: os.environ.get(name) for name in variables}
    with tempfile.TemporaryDirectory(prefix="pcb-openapi-") as temporary:
        os.environ.update(
            {
                "PCB_ENVIRONMENT": "development",
                "PCB_PUBLIC_RELEASE_BACKEND": "sqlite",
                "PCB_RELEASE_STORE_PATH": str(Path(temporary) / "module-release.sqlite3"),
                "PCB_PUBLICATION_TARGET": "local:openapi",
            }
        )
        for name in variables[4:]:
            os.environ.pop(name, None)
        try:
            from polycodebench_api.app import create_app
            from polycodebench_api.auth import TokenDirectory
            from polycodebench_api.submissions import SubmissionStore
            from polycodebench_publication.releases import ReleaseStore

            app = create_app(
                store=ReleaseStore(Path(temporary) / "release.sqlite3"),
                submissions=SubmissionStore(Path(temporary) / "submissions.sqlite3"),
                tokens=TokenDirectory({}),
            )
            return app.openapi()
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def generated_outputs() -> dict[Path, str]:
    schema = _openapi_document()
    return {OPENAPI_JSON: _json_text(schema)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    outputs = generated_outputs()
    stale = [
        path.relative_to(ROOT).as_posix()
        for path, content in outputs.items()
        if not path.exists() or path.read_text(encoding="utf-8") != content
    ]
    if args.check:
        if stale:
            print("REST OpenAPI snapshot is stale: " + ", ".join(stale))
            return 1
        print(f"REST OpenAPI snapshot: PASS ({len(outputs)} generated file)")
        return 0
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    print(f"Generated REST OpenAPI snapshot ({len(outputs)} file)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
