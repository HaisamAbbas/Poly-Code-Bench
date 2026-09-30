"""Import every owned package and exercise fail-closed startup configuration."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT / "packages").glob("*/src"):
    sys.path.insert(0, str(source))

MODULES = (
    "polycodebench_core",
    "polycodebench_services",
    "polycodebench_persistence",
    "polycodebench_orchestration",
    "polycodebench_runner",
    "polycodebench_evaluation",
    "polycodebench_scoring",
    "polycodebench_publication",
    "polycodebench_configuration",
    "polycodebench_plugins_api",
)
for module in MODULES:
    importlib.import_module(module)

from polycodebench_configuration import load_startup_config  # noqa: E402

try:
    load_startup_config({})
except ValueError:
    pass
else:
    raise SystemExit("empty startup configuration was unexpectedly accepted")

valid_dev_api = {
    "PCB_ENVIRONMENT": "dev",
    "PCB_ROLE": "api",
    "PCB_SERVICE_IDENTITY": "smoke-api",
    "PCB_DATABASE_DSN_REF": "local-test-reference",
    "PCB_OIDC_ISSUER": "http://localhost:3000",
    "PCB_OIDC_AUDIENCE": "smoke",
}
config = load_startup_config(valid_dev_api)
assert config.role.value == "api"
print(f"Imported {len(MODULES)} packages; startup config failure/success checks: PASS")
