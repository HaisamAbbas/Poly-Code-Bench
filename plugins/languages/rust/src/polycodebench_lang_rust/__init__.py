"""PolyCodeBench Rust language plugin.

Scope note: this ticket (PCB-11-1) delivers the pinned toolchains and the evaluator identity, so
the package currently exports the identity and lock modules. ``RustLanguagePlugin`` and the plan
and parser modules land with PCB-11-2; until then the allowlist entry
``polycodebench_lang_rust.plugin:RustLanguagePlugin`` is a forward reference and loading it
correctly fails.
"""

from polycodebench_lang_rust.identities import ImageIdentities, load_identities
from polycodebench_lang_rust.locks import CargoLock, LockError, lock_digest, parse_cargo_lock

__all__ = [
    "CargoLock",
    "ImageIdentities",
    "LockError",
    "load_identities",
    "lock_digest",
    "parse_cargo_lock",
]
