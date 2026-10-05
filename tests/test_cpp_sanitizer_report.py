"""Symbolized C++ sanitizer frames retain their candidate source locations."""

from __future__ import annotations

from polycodebench_lang_cpp.guest.pcb_sanitizer_report import classify, diagnostic_locations


def test_asan_parses_paths_after_cpp_function_names_with_spaces() -> None:
    report = """ERROR: AddressSanitizer: heap-use-after-free
#0 0x1234 in (anonymous namespace)::read_released_marker() /workspace/work/src/top_words.cpp:22:37
#1 0x1235 in top_words(std::string const&, unsigned long) /workspace/work/src/top_words.cpp:35:5
#2 0x1236 in main /workspace/work/tests/behaviour.cpp:33:29
SUMMARY: AddressSanitizer: heap-use-after-free
"""

    locations = diagnostic_locations(report, "asan")

    assert ("heap-use-after-free", "/workspace/work/src/top_words.cpp", 22, 37) in locations
    assert classify(report, 1, False, "asan") == "candidate-defect"
