"""PolyCodeBench JavaScript and TypeScript language plugin.

One runtime implementation, two plugin identities (Prompt 19, PCB-19-1..4):

* ``config/images/{javascript,typescript}-v1.json`` record the pinned Node images, the resolved
  dependency closure, the advisory snapshot and the test runner each image actually ships;
* ``identities`` / ``locks`` publish the tool identity of every plan, including the task's
  ``package-lock.json`` digest;
* ``taskspec`` / ``symbols`` / ``plans`` / ``parsers`` / ``testparse`` describe the work and turn the
  recorded bytes back into evidence without executing anything;
* ``profile`` applies the frozen applicability, ownership and token-vs-scanner rules.

``JavaScriptLanguagePlugin`` and ``TypeScriptLanguagePlugin`` share every module above. They are two
separate identities because Technical Spec 18.2 requires two separate profiles, not because they
behave differently at run time.
"""

from polycodebench_lang_javascript.identities import (
    ABSENT_TOOL,
    ImageIdentities,
    ImageRecord,
    Recipe,
    load_identities,
)
from polycodebench_lang_javascript.locks import LockError, PackageLock, lock_digest
from polycodebench_lang_javascript.plugin import (
    JavaScriptLanguagePlugin,
    JsAnalyzer,
    TypeScriptLanguagePlugin,
)
from polycodebench_lang_javascript.profile import PROFILE_VERSIONS, JsProfile, load_profile

__all__ = [
    "PROFILE_VERSIONS",
    "ABSENT_TOOL",
    "ImageIdentities",
    "ImageRecord",
    "JavaScriptLanguagePlugin",
    "JsAnalyzer",
    "JsProfile",
    "LockError",
    "PackageLock",
    "Recipe",
    "TypeScriptLanguagePlugin",
    "load_identities",
    "load_profile",
    "lock_digest",
]