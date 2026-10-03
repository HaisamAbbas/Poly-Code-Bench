"""Initial candidate scaffold for the py-listsort-v1 self-repair fixture.

Implement ``sort_names(rows)`` in this module. See ``visible/task.md`` for the
developer request and ``visible/tests_public/`` for the public tests.
"""


def sort_names(rows):
    """Return the name rows in sorted order as a new list.

    Rows are ``"<First> <Last>"`` strings. The caller wants them ordered from
    the smallest row to the largest; rows that compare equal keep their
    original order, and the input list is never modified.
    """
    ordered = list(rows)
    ordered.sort()
    return ordered
