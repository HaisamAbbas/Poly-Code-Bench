"""Rendering of parsed changelog entries."""


def render_summary(entries):
    """Render entries as one ``- title`` line each, in the given order."""
    return "\n".join(f"- {title}" for _, title in entries)
