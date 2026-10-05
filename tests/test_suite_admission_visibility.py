from polycodebench_evaluation.suite_admission import variant_files


def test_variant_reuses_public_starter_output_without_hidden_duplicate() -> None:
    header = b"struct PublicContract {};\n"
    source = b"int implementation() { return 1; }\n"
    package_files = {
        "visible/repo/include/top_words.hpp": header,
        "hidden/reference/src/top_words.cpp": source,
    }

    assert variant_files(
        package_files,
        "hidden/reference/src/top_words.cpp",
        ["include/top_words.hpp", "src/top_words.cpp"],
    ) == {
        "include/top_words.hpp": header,
        "src/top_words.cpp": source,
    }
