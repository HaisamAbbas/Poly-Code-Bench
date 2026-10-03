"""Known-fault admission variant for py-listsort-v1: still-wrong ``sort_names``.

This variant repairs the initial scaffold's case-sensitive whole-row ordering:
comparison is now case-insensitive by last name then first name. The remaining
known defect is stability: rows with equal (last, first) keys fall back to the
raw row text as a tie-break instead of keeping input order, so the hidden
stability case still fails (``test_hidden_stability``).
"""


def _row_key(row):
    first, last = row.split(" ")
    return (last.casefold(), first.casefold(), row)


def sort_names(rows):
    """Return the name rows sorted case-insensitively by last then first name.

    KNOWN DEFECT: ties are ordered by the raw row text, not by input order.
    """
    return sorted(rows, key=_row_key)
