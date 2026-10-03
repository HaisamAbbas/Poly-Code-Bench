"""Reference admission variant for py-listsort-v1: correct ``sort_names``.

Case-insensitive sort by last name then first name; ``sorted`` is stable, so
rows with equal keys keep their input order. Returns a new list and never
touches the input.
"""


def _row_key(row):
    first, last = row.split(" ")
    return (last.lower(), first.lower())


def sort_names(rows):
    """Return the name rows sorted case-insensitively by last then first name.

    Rows with equal (last, first) keys keep their original relative order.
    A new list is returned; the input list is not modified.
    """
    return sorted(rows, key=_row_key)
