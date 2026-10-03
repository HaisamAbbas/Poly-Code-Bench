# Contributing to cfgkit

Conventions for code under `cfgkit/`:

1. Public functions take their options as keyword-only arguments after `*`.
2. All failures raise `cfgkit.errors.ConfigError` with one sentence naming the offending input.
3. New helpers belong in their own module under `cfgkit/`; keep `loader.py` about parsing.
4. No bare `except`: catch the specific failure or let it propagate.
5. No mutable default arguments.
6. Do not copy a helper into a second place; import it.
7. `legacy_report.py` is frozen for old callers; do not extend it.
