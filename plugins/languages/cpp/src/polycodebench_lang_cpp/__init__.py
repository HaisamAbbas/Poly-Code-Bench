"""PolyCodeBench C++ language plugin: pinned recipes, plans, parsers and the C++ profile.

C++ is a first-class language identity here. It shares the extension contracts in
``polycodebench_plugins_api`` with every other language and shares nothing else: its weights come
from ``profiles.cpp``, its rule mappings live in ``config/languages/cpp-profile-v1.yaml``, its
toolchain is pinned in ``config/languages/cpp-toolchain-v1.json`` and its analyzers are
clang-tidy, cppcheck, the context scanner and the sanitizers.
"""

from polycodebench_lang_cpp.identities import ImageIdentities, load_identities
from polycodebench_lang_cpp.locks import LockError, ToolchainLock, load_lock, lock_digest
from polycodebench_lang_cpp.plugin import CppAnalyzer, CppLanguagePlugin
from polycodebench_lang_cpp.profile import PROFILE_VERSION, CppProfile, load_profile

__all__ = [
    "PROFILE_VERSION",
    "CppAnalyzer",
    "CppLanguagePlugin",
    "CppProfile",
    "ImageIdentities",
    "LockError",
    "ToolchainLock",
    "load_identities",
    "load_lock",
    "load_profile",
    "lock_digest",
]
