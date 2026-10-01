import re


def top_words(lines, k):
    if k < 0:
        raise ValueError("negative k")
    tally = {}
    pattern = re.compile("[a-zA-Z]+")
    for line in lines:
        for match in pattern.finditer(line):
            word = match.group().lower()
            tally[word] = tally.get(word, 0) + 1
    ranked = sorted(tally.items(), key=lambda pair: (-pair[1], pair[0]))
    return ranked[:k] if k else []
