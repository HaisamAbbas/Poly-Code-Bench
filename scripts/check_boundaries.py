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
    "polycodebench_plugins_api": "plugins_api",
    "polycodebench_lang_python": "lang_python",
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
            (key for prefix, key in OWNERS.items() if path.parts[-2].startswith(prefix)), None
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
                        for item in OWNERS
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
