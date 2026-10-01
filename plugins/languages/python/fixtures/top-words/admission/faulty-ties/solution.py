import re
from collections import Counter


def top_words(lines, k):
    if k < 0:
        raise ValueError("k must not be negative")
    counts = Counter()
    for line in lines:
        counts.update(w.lower() for w in re.findall(r"[A-Za-z]+", line))
    return counts.most_common(k)
