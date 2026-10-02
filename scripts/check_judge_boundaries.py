"""Judge-scope package boundary check (Prompt 14 evidence).

The repository checker cannot run while the concurrent Prompt 13 session has plugin members
without boundary-map entries, so this reproduces the same rule for the judge files only.
"""

import ast
from pathlib import Path

OWNERS = {
    "polycodebench_core": "core",
    "polycodebench_services": "services",
    "polycodebench_persistence": "persistence",
    "polycodebench_orchestration": "orchestration",
    "polycodebench_runner": "runner",
    "polycodebench_evaluation": "evaluation",
    "polycodebench_scoring": "scoring",
    "polycodebench_publication": "publication",
    "polycodebench_configuration": "configuration",
    "polycodebench_plugins_api": "plugins_api",
    "polycodebench_lang_python": "lang_python",
    "polycodebench_lang_rust": "lang_rust",
}
ALLOWED = {
    "core": set(),
    "services": {"core"},
    "persistence": {"core"},
    "orchestration": {"core", "services", "persistence", "runner"},
    "runner": {"core"},
    "evaluation": {"core", "runner", "plugins_api"},
    "scoring": {"core"},
    "publication": {"core", "scoring"},
    "configuration": set(),
    "plugins_api": {"core"},
    "lang_python": {"core", "plugins_api"},
    "lang_rust": {"core", "plugins_api"},
}

FILES = [
    "packages/core/src/polycodebench_core/judge_contracts.py",
    "packages/core/src/polycodebench_core/judge_calibration.py",
    "packages/core/src/polycodebench_core/judge_prompts.py",
    "packages/services/src/polycodebench_services/judging.py",
    "packages/services/src/polycodebench_services/judging_calibration.py",
    "packages/persistence/src/polycodebench_persistence/judging.py",
    "packages/persistence/src/polycodebench_persistence/model_configs.py",
    "packages/persistence/src/polycodebench_persistence/models.py",
    "packages/orchestration/src/polycodebench_orchestration/judge/__init__.py",
    "packages/orchestration/src/polycodebench_orchestration/judge/protocol.py",
    "packages/orchestration/src/polycodebench_orchestration/judge/runner.py",
    "packages/orchestration/src/polycodebench_orchestration/judge/cli.py",
    "packages/evaluation/src/polycodebench_evaluation/judge_inputs.py",
]
OWNER_BY_PACKAGE = {
    "core": "core",
    "services": "services",
    "persistence": "persistence",
    "orchestration": "orchestration",
    "evaluation": "evaluation",
}
violations = []
for name in FILES:
    owner = OWNER_BY_PACKAGE[name.split("/")[1]]
    tree = ast.parse(Path(name).read_text(encoding="utf-8"), filename=name)
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for imported in names:
            imported_owner = next(
                (
                    OWNERS[key]
                    for key in OWNERS
                    if imported == key or imported.startswith(key + ".")
                ),
                None,
            )
            if imported_owner and imported_owner != owner and imported_owner not in ALLOWED[owner]:
                violations.append(f"{name}: {imported}")
print("Judge package boundaries:", "PASS" if not violations else violations)
