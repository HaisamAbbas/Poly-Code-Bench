"""PolyCodeBench C language plugin.

Everything here is evidence plumbing: frozen recipes, plans that run pinned tools, and parsers that
turn what the tools actually printed into normalized observations. None of it decides a score; the
scorer does that from the evidence this package produces.
"""

from polycodebench_lang_c.identities import ImageIdentities, ImageRecord, load_identities
from polycodebench_lang_c.plugin import CAnalyzer, CLanguagePlugin
from polycodebench_lang_c.profile import (
    PROFILE_VERSION,
    CProfile,
    ItemResult,
    ProfileResult,
    load_profile,
)
from polycodebench_lang_c.recipe import BuildRecipe, recipe_document, resolve_recipe
from polycodebench_lang_c.taskspec import (
    COracle,
    CQualityPlan,
    SanitizerPolicy,
    WarningPolicy,
    discover_cases,
    validate_c_task,
)

__all__ = [
    "PROFILE_VERSION",
    "CAnalyzer",
    "CLanguagePlugin",
    "COracle",
    "CProfile",
    "CQualityPlan",
    "BuildRecipe",
    "ImageIdentities",
    "ImageRecord",
    "ItemResult",
    "ProfileResult",
    "SanitizerPolicy",
    "WarningPolicy",
    "discover_cases",
    "load_identities",
    "load_profile",
    "recipe_document",
    "resolve_recipe",
    "validate_c_task",
]