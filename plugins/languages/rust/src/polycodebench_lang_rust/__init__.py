"""PolyCodeBench Rust language plugin.

Scope note: PCB-11-1 delivers the pinned toolchains and the evaluator identity and PCB-11-3 the
diagnostic/idiom profile, so the package currently exports identity, lock and profile modules.
``RustLanguagePlugin`` and the plan and parser modules land with PCB-11-2; until then the
allowlist entry ``polycodebench_lang_rust.plugin:RustLanguagePlugin`` is a forward reference and
loading it correctly fails.
"""

from polycodebench_lang_rust.identities import ImageIdentities, load_identities
from polycodebench_lang_rust.locks import CargoLock, LockError, lock_digest, parse_cargo_lock
from polycodebench_lang_rust.profile import (
    PROFILE_VERSION,
    RustProfile,
)
from polycodebench_lang_rust.profile import (
    load_profile as load_rust_profile,
)

__all__ = [
    "PROFILE_VERSION",
    "RustProfile",
    "load_rust_profile",
    "CargoLock",
    "ImageIdentities",
    "LockError",
    "load_identities",
    "lock_digest",
    "parse_cargo_lock",
]
