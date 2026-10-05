from scripts.go_conformance import TOKEN_ONLY


def test_inline_go_fixture_keeps_newline_rune_escaped() -> None:
    assert b"ReadString('\\n')" in TOKEN_ONLY
    assert b"ReadString('\n')" not in TOKEN_ONLY
