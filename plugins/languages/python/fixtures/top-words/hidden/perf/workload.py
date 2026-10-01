import random
import re
from collections import Counter


def prepare(scale, seed):
    rng = random.Random(seed)
    vocabulary = ["w%s" % "".join(rng.choice("abcdefgh") for _ in range(3)) for _ in range(400)]
    vocabulary = [re.sub(r"[^a-z]", "", word) or "w" for word in vocabulary]
    return [" ".join(rng.choice(vocabulary) for _ in range(8)) for _ in range(scale)]


def run(module, data):
    return module.top_words(iter(data), 10)


def verify(data, result):
    counts = Counter(w for line in data for w in re.findall(r"[a-z]+", line))
    expected = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:10]
    return result == expected
