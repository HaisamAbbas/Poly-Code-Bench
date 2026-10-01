from solution import top_words


def test_counts_case_insensitively():
    assert top_words(["The cat and THE hat"], 2) == [("the", 2), ("and", 1)]


def test_zero_returns_empty():
    assert top_words(["a b c"], 0) == []
