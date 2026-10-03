# Task: group release notes by area

Hi — our HISTORY.md entries are written as `- core: fix parsing` or `- cli: add flag`, but the
summary we publish for releases should group them. Could you make `render_summary()` group
entries under their area?

What I need:

- Entries with an area (`- area: title`) render under a heading line with just the area, each
  entry as `- title` below it.
- Entries without an area render under a heading `other`.
- Groups appear in the order the areas first occur in the input, and entries keep their order
  within a group.
- Groups are separated by one blank line; an empty input renders as the empty string.
- `parse_entries()` keeps its current behaviour exactly, including `HistoryError` on lines that
  are neither headings nor entries — other tools import it directly.

Please follow `CONTRIBUTING.md`, and keep the change limited to what this needs.
