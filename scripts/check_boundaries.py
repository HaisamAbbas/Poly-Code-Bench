"""Enforce the initial Python package dependency direction by import scan."""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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
    "polycodebench_operations": "operations",
    "polycodebench_plugins_api": "plugins_api",
    "polycodebench_lang_python": "lang_python",
    "polycodebench_lang_rust": "lang_rust",
    "polycodebench_lang_c": "lang_c",
    "polycodebench_lang_cpp": "lang_cpp",
    "polycodebench_lang_javascript": "lang_javascript",
    "polycodebench_lang_java": "lang_java",
}
# Longest package name first: `polycodebench_lang_cpp` starts with `polycodebench_lang_c`, and a
# C++ module attributed to the C owner would be checked against the wrong allowlist.
_OWNERS_BY_LENGTH = sorted(OWNERS.items(), key=lambda item: -len(item[0]))
ALLOWED = {
    "core": set(),
    "services": {"core"},
    "persistence": {"core"},
    "orchestration": {"core", "services", "persistence", "runner"},
    "runner": {"core"},
    "evaluation": {"core", "runner", "plugins_api"},
    # Scoring reads the shared plugin contracts (FrozenTask, LanguageProfile) so the scorer consumes
    # exactly the same frozen task and profile documents the plugins publish. It still may not
    # depend on a layer that could execute code, call a model or reach a database.
    "scoring": {"core", "plugins_api"},
    "publication": {"core", "scoring"},
    "configuration": set(),
    # Operations verifies deployments and rehearses recovery over the persisted state, the
    # publication store, the pure scorer and the sandbox drivers. Nothing imports it back.
    "operations": {"core", "persistence", "publication", "scoring", "runner", "plugins_api"},
    "plugins_api": {"core"},
    # A language plugin is an adapter over the shared extension interfaces. It may depend on the
    # core contracts and the plugins API, and must never depend on a higher layer such as
    # services, evaluation or persistence.
    "lang_python": {"core", "plugins_api"},
    "lang_rust": {"core", "plugins_api"},
    "lang_c": {"core", "plugins_api"},
    "lang_cpp": {"core", "plugins_api"},
    "lang_javascript": {"core", "plugins_api"},
    "lang_java": {"core", "plugins_api"},
    "lang_go": {"core", "plugins_api"},
}
FORBIDDEN_IMPORTS = {
    "core": ("fastapi", "typer", "sqlalchemy", "alembic", "openai", "anthropic", "boto3"),
    "scoring": ("openai", "anthropic", "google", "httpx", "boto3"),
}


def main() -> int:
    violations: list[str] = []
    members = [(path, path.name.replace("-", "_")) for path in (ROOT / "packages").glob("*")] + [
        (path, "lang_" + path.name) for path in (ROOT / "plugins" / "languages").glob("*")
    ]
    distributions = {
        metadata["project"]["name"].replace("-", "_"): owner
        for path, owner in members
        if (path / "pyproject.toml").exists()
        for metadata in [tomllib.loads((path / "pyproject.toml").read_text(encoding="utf-8"))]
    }
    for path, declaring_package_owner in members:
        metadata_path = path / "pyproject.toml"
        if not metadata_path.exists():
            continue
        metadata = tomllib.loads(metadata_path.read_text(encoding="utf-8"))
        dependencies = metadata.get("project", {}).get("dependencies", [])
        for dependency in dependencies:
            distribution = re.split(r"[<>=!~; ]", dependency, maxsplit=1)[0].replace("-", "_")
            dependency_owner = distributions.get(distribution)
            if dependency_owner and dependency_owner not in ALLOWED[declaring_package_owner]:
                violations.append(
                    f"{metadata_path.relative_to(ROOT)} declares prohibited "
                    f"dependency {distribution}"
                )
    sources = [*(ROOT / "packages").rglob("*.py"), *(ROOT / "plugins").rglob("*.py")]
    for path in sources:
        module_owner = next(
            (key for prefix, key in _OWNERS_BY_LENGTH if path.parts[-2].startswith(prefix)),
            None,
        )
        if module_owner is None:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as error:
            violations.append(f"{path.relative_to(ROOT)}:{error.lineno}: syntax error")
            continue
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if any(
                    name == bad or name.startswith(bad + ".")
                    for bad in FORBIDDEN_IMPORTS.get(module_owner, ())
                ):
                    violations.append(f"{path.relative_to(ROOT)} imports prohibited {name}")
                imported_owner = next(
                    (
                        OWNERS[item]
                        for item, _ in _OWNERS_BY_LENGTH
                        if name == item or name.startswith(item + ".")
                    ),
                    None,
                )
                if (
                    imported_owner
                    and imported_owner != module_owner
                    and imported_owner not in ALLOWED[module_owner]
                ):
                    violations.append(f"{path.relative_to(ROOT)} imports prohibited {name}")
    web_sources = list((ROOT / "apps/web/src").rglob("*.ts")) + list(
        (ROOT / "apps/web/src").rglob("*.tsx")
    )
    for path in web_sources:
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if re.search(r"(?:hidden[_/-]?task|schemas/hidden)", line, re.IGNORECASE):
                violations.append(
                    f"{path.relative_to(ROOT)}:{line_no} references a hidden-task schema"
                )
    if violations:
        print("\n".join(violations))
        return 1
    print("Package dependency boundaries: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
