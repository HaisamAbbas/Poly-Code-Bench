import tracemalloc

from solution import top_words


def stream(count):
    def generate():
        for _ in range(count):
            yield "alpha beta gamma " + "x" * 40

    return generate()


def test_large_stream_is_not_materialised():
    tracemalloc.start()
    try:
        result = top_words(stream(100_000), 2)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result == [("alpha", 100_000), ("beta", 100_000)]
    assert peak < 4 * 1024 * 1024
