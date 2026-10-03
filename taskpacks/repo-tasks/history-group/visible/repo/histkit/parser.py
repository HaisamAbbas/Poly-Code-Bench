"""Parsing for HISTORY.md-style changelog text."""

from .errors import HistoryError


def parse_entries(text):
    """Return changelog entries in file order as ``(area, title)`` pairs.

    An entry line looks like ``- area: title`` or ``- title``. The area is the part before the
    first ``": "`` when it contains no spaces; otherwise the whole body is the title and the
    area is empty.
    """
    entries = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped.startswith("- "):
            if stripped and not stripped.startswith("#"):
                raise HistoryError(f"line {number}: expected an entry starting with '- '")
            continue
        body = stripped[2:].strip()
        head, sep, tail = body.partition(": ")
        if sep and " " not in head:
            entries.append((head, tail))
        else:
            entries.append(("", body))
    return entries
