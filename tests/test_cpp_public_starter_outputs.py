from cpp_plugin_support import CppLanguagePlugin, draft, package_files


def test_public_starter_can_supply_unchanged_reference_header() -> None:
    files = package_files()
    assert "visible/repo/include/top_words.hpp" in files
    assert "hidden/reference/include/top_words.hpp" not in files

    report = CppLanguagePlugin().validate_task(draft(files=files))

    assert report.ok, [issue.model_dump() for issue in report.issues]
