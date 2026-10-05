"""Launch an isolated API with synthetic submitter identities for the browser suite."""

from __future__ import annotations

import os

from polycodebench_api.app import create_app
from polycodebench_api.auth import TokenDirectory


def main() -> None:
    signing_key = os.environ.get("PCB_WEB_AUTH_SIGNING_KEY", "").encode("utf-8")
    if len(signing_key) < 32:
        raise RuntimeError("Prompt 32 test API requires its synthetic OIDC signing key")
    tokens = TokenDirectory({}, web_auth_signing_key=signing_key)
    app = create_app(tokens=tokens)
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8132, log_level="warning")


if __name__ == "__main__":
    main()
