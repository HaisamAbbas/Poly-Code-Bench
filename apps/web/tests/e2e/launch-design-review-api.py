"""Combine existing synthetic public fixtures for visual review; no live sources."""

from pathlib import Path
from runpy import run_path

from polycodebench_api.app import create_app
from polycodebench_api.auth import TokenDirectory


def main() -> None:
    fixture_dir = Path(__file__).resolve().parent
    health = run_path(str(fixture_dir / "launch-prompt99-api.py"))["_PublicHealthFixture"]()
    attestation = run_path(str(fixture_dir / "launch-prompt100-api.py"))[
        "_PublicAttestationFixture"
    ]()
    app = create_app(
        tokens=TokenDirectory({}),
        public_benchmark_health=health,
        public_audit_attestations=attestation,
        attestation_trust_store=attestation.trust_store,
    )
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8151, log_level="warning")


if __name__ == "__main__":
    main()
