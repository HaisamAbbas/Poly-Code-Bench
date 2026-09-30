"""Guest tool helper: path safety, bounded reads, search and the unified-diff engine.

Evidence level: unit, against a temporary directory on the host (no sandbox). The same source is
exercised inside a real container in ``tests/test_solve_guest_docker.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from polycodebench_runner import guest_helper as helper
from polycodebench_runner.guest_helper import ToolFailure


@pytest.fixture
def root(tmp_path: Path) -> str:
    return str(tmp_path)


def write(root: str, rel: str, data: str | bytes) -> None:
    path = Path(root, *rel.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)


def read(root: str, rel: str) -> str:
    return Path(root, *rel.split("/")).read_text(encoding="utf-8")


def fails(code: str):  # type: ignore[no-untyped-def]
    return _expect(code)


class _expect:
    def __init__(self, code: str) -> None:
        self.code = code

    def __enter__(self) -> _expect:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:  # type: ignore[no-untyped-def]
        assert exc_type is ToolFailure, f"expected ToolFailure({self.code}), got {exc_type}"
        assert exc.code == self.code, (exc.code, exc.message)
        return True


# -------------------------------------------------------------------------------- paths


@pytest.mark.parametrize(
    "raw",
    ["", "/etc/passwd", "../x", "a/../../x", "a\\b", ".pcb_inbox/x", "a/.pcb_x", "a\x00b", "a\nb"],
)
def test_clean_path_rejects_escapes_and_reserved_names(raw: str) -> None:
    with fails("path_forbidden"):
        helper.clean_path(raw)


def test_clean_path_normalizes_and_allows_root_only_when_asked() -> None:
    assert helper.clean_path("./a//b/./c.py") == "a/b/c.py"
    assert helper.clean_path(".", allow_root=True) == "."
    with fails("path_forbidden"):
        helper.clean_path(".")


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_symlinks_are_never_followed(root: str) -> None:
    write(root, "real/secret.txt", "s")
    try:
        os.symlink(os.path.join(root, "real"), os.path.join(root, "link"))
    except OSError:
        pytest.skip("symlink creation needs privileges on this host")
    with fails("path_forbidden"):
        helper.op_read_file(root, {"path": "link/secret.txt"})
    with fails("path_forbidden"):
        helper.op_list_files(root, {"path": "link"})


# ------------------------------------------------------------------------------ listing


def test_list_files_is_sorted_paged_depth_limited_and_hides_harness_paths(root: str) -> None:
    for index in range(2500):
        write(root, f"d/f{index:04d}.txt", "x")
    write(root, "top.txt", "x")
    write(root, ".pcb_inbox/leftover.patch", "x")
    first = helper.op_list_files(root, {"path": ".", "depth": 1})
    assert [e["path"] for e in first["entries"]] == ["d", "top.txt"]
    deep = helper.op_list_files(root, {"path": "d", "depth": 1})
    assert len(deep["entries"]) == 1000 and deep["next_cursor"] == deep["entries"][-1]["path"]
    page2 = helper.op_list_files(root, {"path": "d", "depth": 1, "cursor": deep["next_cursor"]})
    assert page2["entries"][0]["path"] == "d/f1000.txt" and len(page2["entries"]) == 1000
    page3 = helper.op_list_files(root, {"path": "d", "depth": 1, "cursor": page2["next_cursor"]})
    assert len(page3["entries"]) == 500 and page3["next_cursor"] is None
    tree = helper.op_list_files(root, {"path": ".", "depth": 2})
    assert "d/f0000.txt" in {e["path"] for e in tree["entries"]}
    assert not any(".pcb_" in e["path"] for e in tree["entries"])
    with fails("not_a_directory"):
        helper.op_list_files(root, {"path": "top.txt"})
    with fails("not_found"):
        helper.op_list_files(root, {"path": "missing"})


# ------------------------------------------------------------------------------- reading


def test_read_file_numbers_lines_and_reports_truncation(root: str) -> None:
    write(root, "a.txt", "\n".join(f"line {n}" for n in range(1, 1001)) + "\n")
    page = helper.op_read_file(root, {"path": "a.txt", "start_line": 1, "max_lines": 400})
    assert page["text"].splitlines()[0] == "1\tline 1" and page["end_line"] == 400
    assert page["truncated"] is True and page["next_start_line"] == 401
    assert page["total_lines"] == 1000 and page["sha256"].startswith("sha256:")
    tail = helper.op_read_file(root, {"path": "a.txt", "start_line": 990, "max_lines": 400})
    assert tail["end_line"] == 1000 and tail["truncated"] is False
    assert tail["next_start_line"] is None


def test_read_file_bounds_bytes_and_returns_metadata_for_binary(root: str) -> None:
    write(root, "wide.txt", "\n".join("x" * 1000 for _ in range(200)) + "\n")
    wide = helper.op_read_file(root, {"path": "wide.txt", "start_line": 1, "max_lines": 400})
    assert len(wide["text"].encode()) <= helper.READ_MAX_BYTES and wide["truncated"] is True
    write(root, "blob.bin", b"\x00\x01\x02" * 100)
    blob = helper.op_read_file(root, {"path": "blob.bin", "start_line": 1, "max_lines": 10})
    assert blob["binary"] is True and "text" not in blob and blob["size"] == 300
    write(root, "latin.txt", b"caf\xe9\n")
    assert helper.op_read_file(root, {"path": "latin.txt"})["binary"] is True
    os.makedirs(os.path.join(root, "dir"))
    with fails("not_a_file"):
        helper.op_read_file(root, {"path": "dir"})


# ------------------------------------------------------------------------------- search


def test_search_literal_regex_glob_and_cursor(root: str) -> None:
    for index in range(300):
        write(root, f"src/m{index:03d}.py", f"needle {index}\nother\n")
    write(root, "docs/readme.md", "needle in docs\n")
    write(root, "blob.bin", b"needle\x00binary")
    page = helper._search_core(root, {"pattern": "needle", "path_glob": "src/*.py"})
    assert len(page["matches"]) == 200 and page["next_cursor"]
    rest = helper._search_core(
        root,
        {"pattern": "needle", "path_glob": "src/*.py", "cursor": page["next_cursor"]},
    )
    assert len(rest["matches"]) == 100 and rest["next_cursor"] is None
    assert rest["matches"][0]["path"] == "src/m200.py"
    every = helper._search_core(root, {"pattern": r"needle \d+5$", "regex": True, "path_glob": "*"})
    assert {m["path"] for m in every["matches"]} >= {"src/m015.py", "src/m025.py"}
    assert all("blob.bin" != m["path"] for m in every["matches"])  # binary files are skipped
    none = helper._search_core(root, {"pattern": "absent", "path_glob": "*"})
    assert none["matches"] == []


# ----------------------------------------------------------------------------- patching


def patch_for(name: str, old: str, new: str) -> str:
    return f"--- a/{name}\n+++ b/{name}\n{old}{new}"


def apply(root: str, diff: str, protected: list[str] | None = None) -> dict:  # type: ignore[type-arg]
    write(root, ".pcb_inbox/t.patch", diff)
    return helper.op_apply_patch(
        root,
        {"patch_file": ".pcb_inbox/t.patch", "protected": protected or [], "max_file_bytes": 10**6},
    )


def test_patch_modifies_with_offset_tolerance_and_reports_counts(root: str) -> None:
    write(root, "a.py", "import os\n\n\ndef f():\n    return 1\n\n\ndef g():\n    return 2\n")
    diff = "--- a/a.py\n+++ b/a.py\n@@ -40,3 +40,3 @@\n def g():\n-    return 2\n+    return 3\n \n"
    # declared line 40 is far off; the exact context block is found at the nearest position
    with fails("patch_rejected"):
        apply(root, diff)  # trailing context line does not exist -> no change
    assert read(root, "a.py").endswith("return 2\n")
    good = "--- a/a.py\n+++ b/a.py\n@@ -40,2 +40,2 @@\n def g():\n-    return 2\n+    return 3\n"
    result = apply(root, good)
    assert result["files"] == [
        {"path": "a.py", "action": "modify", "hunks": 1, "added": 1, "removed": 1}
    ]
    assert read(root, "a.py").endswith("def g():\n    return 3\n")
    assert not os.path.exists(os.path.join(root, ".pcb_inbox", "t.patch"))  # inbox consumed


def test_patch_is_atomic_across_files(root: str) -> None:
    write(root, "one.txt", "a\nb\n")
    write(root, "two.txt", "x\ny\n")
    diff = (
        "--- a/one.txt\n+++ b/one.txt\n@@ -1,2 +1,2 @@\n a\n-b\n+B\n"
        "--- a/two.txt\n+++ b/two.txt\n@@ -1,2 +1,2 @@\n nomatch\n-y\n+Y\n"
    )
    with fails("patch_rejected"):
        apply(root, diff)
    assert read(root, "one.txt") == "a\nb\n" and read(root, "two.txt") == "x\ny\n"


def test_patch_creates_nested_files_deletes_and_keeps_no_newline_semantics(root: str) -> None:
    write(root, "old.txt", "gone\n")
    write(root, "tail.txt", "last")
    diff = (
        "--- /dev/null\n+++ b/pkg/new.py\n@@ -0,0 +1,2 @@\n+x = 1\n+y = 2\n"
        "--- a/old.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-gone\n"
        "--- a/tail.txt\n+++ b/tail.txt\n@@ -1 +1 @@\n-last\n\\ No newline at end of file\n"
        "+final\n\\ No newline at end of file\n"
    )
    result = apply(root, diff)
    assert {f["action"] for f in result["files"]} == {"create", "delete", "modify"}
    assert read(root, "pkg/new.py") == "x = 1\ny = 2\n"
    assert not os.path.exists(os.path.join(root, "old.txt"))
    assert read(root, "tail.txt") == "final"


@pytest.mark.parametrize(
    ("diff", "code"),
    [
        ("--- a/../../etc/passwd\n+++ b/../../etc/passwd\n@@ -1 +1 @@\n-a\n+b\n", "path_forbidden"),
        ("--- a/.pcb_x\n+++ b/.pcb_x\n@@ -1 +1 @@\n-a\n+b\n", "path_forbidden"),
        ("--- a/x.txt\n+++ b/y.txt\n@@ -1 +1 @@\n-a\n+b\n", "patch_rejected"),
        ("rename from a\nrename to b\n", "patch_rejected"),
        ("old mode 100644\nnew mode 100755\n", "patch_rejected"),
        ("GIT binary patch\nliteral 1\n", "patch_rejected"),
        ("just some prose\n", "patch_rejected"),
        ("--- a/x.txt\n+++ b/x.txt\n@@ -1 +1 @@\n-a\n", "patch_rejected"),
    ],
)
def test_patch_rejects_unsafe_or_malformed_diffs(root: str, diff: str, code: str) -> None:
    write(root, "x.txt", "a\n")
    with fails(code):
        apply(root, diff)
    assert read(root, "x.txt") == "a\n"


def test_patch_honours_protected_paths_and_existing_file_rules(root: str) -> None:
    write(root, "tests/test_a.py", "assert True\n")
    write(root, "src.py", "a\n")
    edit_protected = (
        "--- a/tests/test_a.py\n+++ b/tests/test_a.py\n@@ -1 +1 @@\n-assert True\n+assert False\n"
    )
    with fails("protected_path"):
        apply(root, edit_protected, protected=["tests"])
    assert read(root, "tests/test_a.py") == "assert True\n"
    recreate = "--- /dev/null\n+++ b/src.py\n@@ -0,0 +1 @@\n+b\n"
    with fails("patch_rejected"):
        apply(root, recreate)
    partial_delete = "--- a/src.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-wrong\n"
    with fails("patch_rejected"):
        apply(root, partial_delete)
    big = "--- /dev/null\n+++ b/big.txt\n@@ -0,0 +1,2000 @@\n" + "+" + "x" * 600 + "\n"
    big = "--- /dev/null\n+++ b/big.txt\n@@ -0,0 +1,2000 @@\n" + ("+" + "x" * 600 + "\n") * 2000
    write(root, ".pcb_inbox/t.patch", big)
    with fails("patch_rejected"):
        helper.op_apply_patch(
            root,
            {"patch_file": ".pcb_inbox/t.patch", "protected": [], "max_file_bytes": 100_000},
        )
    assert not os.path.exists(os.path.join(root, "big.txt"))


def test_patch_file_must_live_in_the_harness_inbox(root: str) -> None:
    write(root, "evil.patch", "--- a/x\n+++ b/x\n")
    with fails("path_forbidden"):
        helper.op_apply_patch(root, {"patch_file": "evil.patch", "protected": []})
    with fails("path_forbidden"):
        helper.op_apply_patch(root, {"patch_file": ".pcb_inbox/../evil.patch", "protected": []})


def test_hunks_apply_in_order_with_running_offset() -> None:
    original = "".join(f"{n}\n" for n in range(1, 21))
    hunks = [
        (2, 3, [(" ", "2", False), ("-", "3", False), (" ", "4", False)]),
        (
            14,
            3,
            [(" ", "14", False), ("+", "14.5", False), (" ", "15", False), (" ", "16", False)],
        ),
    ]
    updated = helper.apply_hunks(original, hunks, "f").split("\n")
    assert "3" not in updated[:4] and updated[updated.index("14") + 1] == "14.5"
    # a hunk whose context exists nowhere is refused rather than guessed at
    with pytest.raises(ToolFailure):
        helper.apply_hunks(original, [(1, 1, [("-", "nope", False)])], "f")
