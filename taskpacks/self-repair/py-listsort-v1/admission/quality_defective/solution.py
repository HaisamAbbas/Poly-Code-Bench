"""Quality-defective admission variant for py-listsort-v1.

Functionally correct on every public and hidden case. The defects are
convention-level on purpose and must be reported by a convention scan as the
families ``convention`` and ``duplication``:

* a bare ``except:`` in ``sort_names``,
* a mutable default argument ``cache={}`` on ``sort_names``,
* one normalized statement block copy-pasted into the two identical functions
  ``_sort_rows_primary`` and ``_sort_rows_backup``.
"""


def _sort_rows_primary(rows):
    decorated = []
    for row in rows:
        words = row.split(" ")
        key = (words[1].lower(), words[0].lower())
        decorated.append((key, row))
    decorated.sort(key=lambda item: item[0])
    ordered = [row for _, row in decorated]
    checked = list(ordered)
    return checked


def _sort_rows_backup(rows):
    decorated = []
    for row in rows:
        words = row.split(" ")
        key = (words[1].lower(), words[0].lower())
        decorated.append((key, row))
    decorated.sort(key=lambda item: item[0])
    ordered = [row for _, row in decorated]
    checked = list(ordered)
    return checked


def sort_names(rows, cache={}):
    try:
        cached = cache.get(tuple(rows))
    except:
        cached = None
    if cached is not None:
        return list(cached)
    ordered = _sort_rows_primary(rows)
    cache[tuple(rows)] = list(ordered)
    return ordered
