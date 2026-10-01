import re


def top_words(lines, k, stats={}):
    if k < 0:
        raise ValueError("negative k")
    counts = {}
    for line in list(lines):
        for word in re.findall("[A-Za-z]+", line):
            word = word.lower()
            counts[word] = counts.get(word, 0) + 1
    try:
        stats["calls"] = stats.get("calls", 0) + 1
    except:
        pass
    result = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return result[:k]
