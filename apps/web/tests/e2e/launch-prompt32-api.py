"""Launch an isolated API for public-only browser boundary checks."""

from __future__ import annotations

from polycodebench_api.app import create_app
from polycodebench_api.auth import TokenDirectory


def main() -> None:
    app = create_app(tokens=TokenDirectory({}))
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8132, log_level="warning")


if __name__ == "__main__":
    main()
