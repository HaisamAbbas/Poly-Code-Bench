import re
from collections import Counter

from hypothesis import given
from hypothesis import strategies as st
from solution import top_words

text = st.lists(
    st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=40), max_size=20
)


def model(lines, k):
    counts = Counter(w.lower() for line in lines for w in re.findall(r"[A-Za-z]+", line))
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:k]


@given(text, st.integers(min_value=0, max_value=12))
def test_matches_independent_model(lines, k):
    assert top_words(iter(lines), k) == model(lines, k)


@given(text, st.integers(min_value=0, max_value=12))
def test_result_shape(lines, k):
    result = top_words(iter(lines), k)
    assert len(result) <= k
    counts = [count for _, count in result]
    assert counts == sorted(counts, reverse=True)
    assert len({word for word, _ in result}) == len(result)
