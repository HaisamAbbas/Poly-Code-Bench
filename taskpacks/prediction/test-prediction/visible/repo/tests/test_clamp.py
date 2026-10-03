"""Tests for the bounds helpers: the test this task reasons about."""

import pytest

from bounds import clamp


def test_clamp_accepts_ints() -> None:
    assert clamp(50) == 50


def test_clamp_rejects_non_int() -> None:
    with pytest.raises(TypeError) as caught:
        clamp("7", upper=100)
    assert "int" in str(caught.value)
