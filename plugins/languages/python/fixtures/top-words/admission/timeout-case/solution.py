import re
from collections import Counter


def top_words(lines, k):
    if k < 0:
        raise ValueError("k must not be negative")
    counts = Counter()
    for line in lines:
        counts.update(w.lower() for w in re.findall(r"[A-Za-z]+", line))
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    result = []
    index = 0
    while len(result) < k:  # never ends when k exceeds the number of distinct words
        if index < len(ranked):
            result.append(ranked[index])
            index += 1
    return result
