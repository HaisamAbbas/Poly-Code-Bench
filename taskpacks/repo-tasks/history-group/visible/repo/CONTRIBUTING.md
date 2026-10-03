# Contributing to histkit

Conventions for code under `histkit/`:

1. Public functions keep their signatures; other tools import them directly.
2. All failures raise `histkit.errors.HistoryError` with one sentence naming the offending line.
3. New helpers belong in their own module under `histkit/`.
4. No bare `except`: catch the specific failure or let it propagate.
5. No mutable default arguments.
6. Do not copy a helper into a second place; import it.
