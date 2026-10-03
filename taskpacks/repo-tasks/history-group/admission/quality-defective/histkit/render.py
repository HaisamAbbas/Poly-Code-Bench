"""Rendering of parsed changelog entries."""


def render_left(entries, cache={}):
    groups = {}
    for area, title in entries:
        try:
            groups.setdefault(area or "other", []).append(title)
        except:
            continue
    blocks = []
    for area in groups:
        lines = [area]
        for title in groups[area]:
            lines.append(f"- {title}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def render_right(entries, cache={}):
    groups = {}
    for area, title in entries:
        try:
            groups.setdefault(area or "other", []).append(title)
        except:
            continue
    blocks = []
    for area in groups:
        lines = [area]
        for title in groups[area]:
            lines.append(f"- {title}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def render_summary(entries):
    """Render entries grouped under their area heading, one ``- title`` line per entry."""
    return render_left(entries)
