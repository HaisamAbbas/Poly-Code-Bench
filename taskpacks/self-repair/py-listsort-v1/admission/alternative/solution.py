"""Alternative admission variant for py-listsort-v1: correct ``sort_names``.

A different decomposition from the reference: instead of a sort key function,
rows are ordered through an explicit pairwise comparator over case-folded
(last, first) pairs. The comparator returns 0 for equal keys, so the sort's
stability preserves the input order of ties.
"""

from functools import cmp_to_key


def _compare_rows(left, right):
    left_first, left_last = left.split(" ")
    right_first, right_last = right.split(" ")
    left_key = (left_last.casefold(), left_first.casefold())
    right_key = (right_last.casefold(), right_first.casefold())
    return (left_key > right_key) - (left_key < right_key)


def sort_names(rows):
    """Return the name rows sorted case-insensitively by last then first name.

    Rows with equal (last, first) keys keep their original relative order.
    A new list is returned; the input list is not modified.
    """
    return sorted(rows, key=cmp_to_key(_compare_rows))
