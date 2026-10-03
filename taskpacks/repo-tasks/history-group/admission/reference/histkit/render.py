"""Rendering of parsed changelog entries."""

from .group import group_entries


def render_summary(entries):
    """Render entries grouped under their area heading, one ``- title`` line per entry."""
    blocks = []
    for area, titles in group_entries(entries).items():
        lines = [area] + [f"- {title}" for title in titles]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
