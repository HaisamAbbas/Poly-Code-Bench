"""Grouping of parsed changelog entries by area."""


def group_entries(entries):
    """Group ``(area, title)`` pairs by area; entries without an area go under ``other``."""
    groups = {}
    for area, title in entries:
        groups.setdefault(area or "other", []).append(title)
    return groups
