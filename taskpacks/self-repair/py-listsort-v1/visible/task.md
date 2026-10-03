# py-listsort-v1: sort name rows for the label pipeline

Developer request. The label-printing pipeline hands us a list of name rows and
needs them ordered before the labels are cut. Please implement `sort_names(rows)`
in `solution.py`.

## Request

Implement `sort_names(rows)` with exactly this behaviour:

- `rows` is a list of name rows. Each row is a single string of the form
  `"<First> <Last>"`: exactly two words separated by one ASCII space. Names are
  ASCII letters (mixed case); every input row is well-formed.
- Order the rows **case-insensitively by last name, then by first name**. For
  example `bob adams` sorts before `Alice smith` because `adams` precedes
  `smith`, regardless of the capitalisation of either row.
- Rows whose `(last, first)` names are equal case-insensitively are ties:
  they must keep their original relative order from the input (stable).
- Return a **new list**. Never mutate the input list.

## Tests and feedback

The public tests live in `visible/tests_public/`; you may run them against your
`solution.py`, for example:

    uv run python -m unittest discover -s visible/tests_public -t visible/tests_public

Public test failures are the only feedback available for repair: a round's
feedback reports the public case results of your previous submission and
nothing else. A submission that passes every public test is not thereby proven
correct, so keep the stated contract (case-insensitive last-then-first ordering,
stable ties, new list) even where the public cases happen to be lenient.
