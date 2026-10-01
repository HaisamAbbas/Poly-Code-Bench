# Top words

Implement `top_words(lines, k)` in `solution.py`.

```python
def top_words(lines: Iterable[str], k: int) -> list[tuple[str, int]]: ...
```

Rules:

- A *word* is a maximal run of ASCII letters (`A-Z`, `a-z`). Words are compared
  case-insensitively and reported in lower case. Every other character separates words.
- Return at most `k` `(word, count)` pairs ordered by count, highest first. Words with equal
  counts are ordered alphabetically.
- `k == 0` returns an empty list. A negative `k` raises `ValueError`.
- `lines` can be a one-shot iterator holding far more text than fits comfortably in memory.
  Consume it once and do not keep the lines.
- Only the standard library is available. The program runs without network access.

Output contract: a single file, `solution.py`. Public example tests are in `tests/`.
