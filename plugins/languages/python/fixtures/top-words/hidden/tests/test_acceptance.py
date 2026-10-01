import pytest
from solution import top_words


def test_ties_break_alphabetically():
    assert top_words(["pear apple fig apple pear fig kiwi"], 3) == [
        ("apple", 2),
        ("fig", 2),
        ("pear", 2),
    ]


def test_case_and_separators():
    lines = ["Hello, hello-WORLD!", "world... 42 world_hello"]
    assert top_words(lines, 5) == [("hello", 3), ("world", 3)]


def test_k_larger_than_distinct():
    assert top_words(["b a b"], 10) == [("b", 2), ("a", 1)]


def test_empty_input():
    assert top_words([], 3) == []
    assert top_words(["   ", "123 456"], 3) == []


def test_zero_and_negative_k():
    assert top_words(["a"], 0) == []
    with pytest.raises(ValueError):
        top_words(["a"], -1)


def test_accepts_one_shot_iterator():
    produced = iter(["x y x", "y x"])
    assert top_words(produced, 1) == [("x", 3)]
    assert list(produced) == []


def test_non_ascii_letters_are_separators():
    assert top_words(["café café cafe"], 3) == [("caf", 2), ("cafe", 1)]
