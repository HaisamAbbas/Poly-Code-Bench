import heapq
import re
from collections import Counter
from collections.abc import Iterable

_WORD = re.compile(r"[A-Za-z]+")


def top_words(lines: Iterable[str], k: int) -> list[tuple[str, int]]:
    if k < 0:
        raise ValueError("k must not be negative")
    counts: Counter[str] = Counter()
    for line in lines:
        counts.update(word.lower() for word in _WORD.findall(line))
    return heapq.nsmallest(k, counts.items(), key=lambda item: (-item[1], item[0]))
