"""Export the role-aware startup settings schema without resolving secrets."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/configuration/src"))

from polycodebench_configuration import StartupConfig  # noqa: E402
from polycodebench_configuration.settings import ROLE_REQUIRED_FIELDS  # noqa: E402

schema = StartupConfig.model_json_schema()
schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
schema["$id"] = "https://polycodebench.invalid/schemas/configuration/startup-config.v1.json"
schema["title"] = "PolyCodeBench role-specific startup configuration"
schema["allOf"] = [
    {
        "if": {"properties": {"role": {"const": role.value}}, "required": ["role"]},
        "then": {"required": required},
    }
    for role, required in ROLE_REQUIRED_FIELDS.items()
]
target = ROOT / "schemas/configuration/startup-config.v1.json"
rendered = json.dumps(schema, indent=2, sort_keys=True) + "\n"
parser = argparse.ArgumentParser()
parser.add_argument("--check", action="store_true")
if parser.parse_args().check:
    if not target.exists() or target.read_text(encoding="utf-8") != rendered:
        raise SystemExit(
            "startup config schema is stale; run python scripts/export_startup_schema.py"
        )
    print("Startup configuration schema: PASS")
else:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered, encoding="utf-8")
    print(f"Wrote {target.relative_to(ROOT)}")
