"""Legacy report formatting kept for old callers; never part of new features."""


def render_lines(rows, width=40, suffixes=[]):
    lines = []
    trimmed = []
    for row in rows:
        try:
            text = str(row[0])
        except:
            text = "?"
        trimmed.append(text)
    for text in trimmed:
        if len(text) > width:
            text = text[: width - 1] + "~"
        lines.append(text)
    return lines + list(suffixes)


def render_debug(rows, width=40, suffixes=[]):
    lines = []
    trimmed = []
    for row in rows:
        try:
            text = str(row[0])
        except:
            text = "?"
        trimmed.append(text)
    for text in trimmed:
        if len(text) > width:
            text = text[: width - 1] + "~"
        lines.append(text)
    return lines + list(suffixes)
