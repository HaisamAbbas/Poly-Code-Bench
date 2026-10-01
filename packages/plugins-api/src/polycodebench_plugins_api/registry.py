"""Administrator-managed allowlist of plugin entry points and OCI image digests (Spec 18.1)."""

from __future__ import annotations

import json
from collections.abc import Iterable
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from polycodebench_core.models import Digest, Slug
from pydantic import BaseModel, ConfigDict, Field

from polycodebench_plugins_api.contracts import API_VERSION, ExecutionPlan
from polycodebench_plugins_api.protocols import LanguagePlugin

LANGUAGE_GROUP = "polycodebench.language_plugins"


class RegistryError(RuntimeError):
    """The requested plugin or image is not on the administrator allowlist."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class AllowedPlugin(_Strict):
    plugin_id: Slug
    entry_point: str = Field(pattern=r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")
    api_version: int = Field(ge=1)
    plugin_version: str
    image_digests: tuple[Digest, ...] = Field(min_length=1)


class PluginAllowlist(_Strict):
    schema_version: int
    kind: str
    plugins: tuple[AllowedPlugin, ...]

    def get(self, plugin_id: str) -> AllowedPlugin:
        for item in self.plugins:
            if item.plugin_id == plugin_id:
                return item
        raise RegistryError(f"plugin {plugin_id!r} is not allowlisted")


def load_allowlist(path: Path) -> PluginAllowlist:
    document: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    return PluginAllowlist.model_validate_json(json.dumps(document))


def _find_entry_point(allowed: AllowedPlugin, candidates: Iterable[EntryPoint]) -> EntryPoint:
    for entry in candidates:
        if entry.name == allowed.plugin_id and entry.value == allowed.entry_point:
            return entry
    raise RegistryError(
        f"no installed entry point matches the allowlist entry for {allowed.plugin_id!r}"
    )


def load_language_plugin(allowlist: PluginAllowlist, plugin_id: str) -> LanguagePlugin:
    """Instantiate an allowlisted plugin; an unlisted or altered entry point is refused."""
    allowed = allowlist.get(plugin_id)
    entry = _find_entry_point(allowed, entry_points(group=LANGUAGE_GROUP))
    plugin = entry.load()()
    if plugin.api_version != allowed.api_version or plugin.api_version != API_VERSION:
        raise RegistryError("plugin API version does not match the allowlist")
    if plugin.language_id != plugin_id:
        raise RegistryError("plugin reports a different language identifier")
    return plugin  # type: ignore[no-any-return]


def assert_plan_allowed(allowlist: PluginAllowlist, plugin_id: str, plan: ExecutionPlan) -> None:
    """A plan may only name an image digest the administrator approved for this plugin."""
    if plan.image_digest not in allowlist.get(plugin_id).image_digests:
        raise RegistryError(f"image {plan.image_digest} is not approved for plugin {plugin_id!r}")
