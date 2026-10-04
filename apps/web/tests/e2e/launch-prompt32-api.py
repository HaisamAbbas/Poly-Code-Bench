"""Launch an isolated API with synthetic submitter identities for the browser suite."""

from __future__ import annotations

import json
from pathlib import Path

from polycodebench_api.app import create_app
from polycodebench_api.auth import ApiPrincipal, TokenDirectory


def main() -> None:
    clients = json.loads(Path(".cache/prompt32-e2e-client.json").read_text(encoding="utf-8"))
    tokens = TokenDirectory(
        {
            clients["accountToken"]: ApiPrincipal(
                subject_id="browser-submitter",
                roles=frozenset({"submitter"}),
                email="browser@example.org",
                email_verified=True,
            ),
            clients["secondAccountToken"]: ApiPrincipal(
                subject_id="other-browser-account",
                roles=frozenset({"submitter"}),
                email="other@example.org",
                email_verified=True,
            ),
        }
    )
    app = create_app(tokens=tokens)
    if app.state.services.tokens.resolve(clients["accountToken"]) is None:
        raise RuntimeError("Prompt 32 API lost the synthetic account before server startup")
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8132, log_level="warning")


if __name__ == "__main__":
    main()
