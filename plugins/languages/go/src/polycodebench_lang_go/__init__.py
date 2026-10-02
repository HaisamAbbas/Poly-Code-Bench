"""PolyCodeBench Go language plugin.

Delivers the pinned toolchains and evaluator identity (PCB-22-1), the plans, parsers, symbol index
and ``GoLanguagePlugin`` (PCB-22-2) and the diagnostic/idiom profile with its concurrency
applicability rules (PCB-22-3).
"""

from polycodebench_lang_go.identities import ImageIdentities, load_identities
from polycodebench_lang_go.locks import GoModule, LockError, module_digest, parse_module_files
from polycodebench_lang_go.plugin import GoLanguagePlugin
from polycodebench_lang_go.profile import PROFILE_VERSION, GoProfile
from polycodebench_lang_go.profile import load_profile as load_go_profile

__all__ = [
    "PROFILE_VERSION",
    "GoLanguagePlugin",
    "GoModule",
    "GoProfile",
    "ImageIdentities",
    "LockError",
    "load_go_profile",
    "load_identities",
    "module_digest",
    "parse_module_files",
]
