"""Rendering of parsed changelog entries."""


def render_summary(entries):
    """Render entries grouped under their area heading, one ``- title`` line per entry."""
    groups = {}
    for area, title in entries:
        if not area:
            continue
        groups.setdefault(area, []).append(title)
    blocks = []
    for area in groups:
        lines = [area]
        for title in groups[area]:
            lines.append(f"- {title}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
