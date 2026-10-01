"""Guest-side Python tooling (context scanner, build check, capture) and test-evidence parsing."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import textwrap
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from polycodebench_lang_python import PythonLanguagePlugin
from polycodebench_plugins_api import DictArtifactReader
from polycodebench_plugins_api.testreport import (
    GroupControl,
    InventoryCase,
    InventoryGroup,
    TestCaseRecord,
    reconcile,
)
from python_plugin_support import ROOT, frozen

GUEST = ROOT / "plugins" / "languages" / "python" / "src" / "polycodebench_lang_python" / "guest"
plugin = PythonLanguagePlugin()


def load_guest(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, GUEST / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scanner = load_guest("pcb_context_scan")


def scan_source(tmp_path: Path, source: str, *, typing: str = "none", tags: tuple[str, ...] = ()):  # type: ignore[no-untyped-def]
    (tmp_path / "mod.py").write_text(textwrap.dedent(source), encoding="utf-8")
    return scanner.scan(str(tmp_path), ["mod.py"], {"typing": typing, "opportunities": set(tags)})[
        "findings"
    ]


def verdicts(findings: list[dict[str, Any]]) -> dict[str, str]:
    return {f["rule"]: f["verdict"] for f in findings}


# ------------------------------------------------------------- context scanner: defaults


def test_mutable_default_is_a_violation_only_when_mutated_or_escaping(tmp_path: Path) -> None:
    found = scan_source(
        tmp_path,
        """
        def mutated(x, acc=[]):
            acc.append(x)
            return len(acc)

        def escaped(x, cache={}):
            return cache

        def stored(self, items=[]):
            self.items = items

        def read_only(x, names=[]):
            return x in names

        def defensive(x, acc=[]):
            acc = list(acc)
            acc.append(x)
            return acc

        def sentinel(x, acc=None):
            acc = [] if acc is None else acc
            acc.append(x)
            return acc
    """,
    )
    by_symbol = {(f["symbol"], f["rule"]): f["verdict"] for f in found}
    assert by_symbol[("mutated", "mutable-default-shared")] == "violation"
    assert by_symbol[("escaped", "mutable-default-shared")] == "violation"
    assert by_symbol[("stored", "mutable-default-shared")] == "violation"
    assert by_symbol[("read_only", "mutable-default-benign")] == "benign_in_context"
    assert by_symbol[("defensive", "mutable-default-benign")] == "benign_in_context"
    assert not any(symbol == "sentinel" for symbol, _ in by_symbol)


def test_missing_annotations_count_only_when_the_task_expects_types(tmp_path: Path) -> None:
    source = """
        def public(a, b: int) -> int:
            return b

        def _private(a):
            return a

        class Thing:
            def method(self, x):
                return x

            def typed(self, x: int) -> int:
                return x

            def _skip(self, x):
                return x

        def outer() -> None:
            def inner(z):
                return z
    """
    expected = {
        f["symbol"]
        for f in scan_source(tmp_path, source, typing="required")
        if f["rule"] == "missing-annotation"
    }
    assert expected == {"public", "Thing.method"}
    assert not [
        f for f in scan_source(tmp_path, source, typing="none") if f["rule"] == "missing-annotation"
    ]


# ---------------------------------------------------------- context scanner: other rules


def test_index_loops_errors_and_resources_are_judged_by_how_they_are_used(tmp_path: Path) -> None:
    found = scan_source(
        tmp_path,
        """
        import threading

        def pure(items):
            for i in range(len(items)):
                print(items[i])

        def needs_index(items):
            for i in range(len(items)):
                print(i, items[i])

        def broad():
            try:
                work()
            except Exception:
                pass

        def narrow():
            try:
                work()
            except KeyError:
                pass

        def handled():
            try:
                work()
            except Exception as error:
                log(error)

        def reraise():
            try:
                work()
            except Exception:
                raise

        def leaks():
            handle = open("a")
            return handle.read()

        def closed():
            handle = open("a")
            try:
                return handle.read()
            finally:
                handle.close()

        def managed():
            with open("a") as handle:
                return handle.read()

        def guarded(lock):
            lock.acquire()
            try:
                work()
            finally:
                lock.release()

        def unguarded(lock):
            lock.acquire()
            work()

        def fail():
            raise Exception("bad")

        def check(value):
            assert value > 0
            return value
    """,
    )
    seen = {(f["symbol"], f["rule"]): f["verdict"] for f in found}
    assert seen[("pure", "range-len-index-loop")] == "violation"
    assert seen[("needs_index", "range-len-index-needed")] == "benign_in_context"
    assert seen[("broad", "swallowed-broad-exception")] == "violation"
    assert seen[("narrow", "swallowed-narrow-exception")] == "benign_in_context"
    assert not any(symbol in {"handled", "reraise"} for symbol, _ in seen)
    assert seen[("leaks", "open-without-context-manager")] == "violation"
    assert seen[("closed", "open-without-with-closed")] == "benign_in_context"
    assert not any(symbol in {"managed", "guarded"} for symbol, _ in seen)
    assert seen[("unguarded", "lock-acquire-without-release-guard")] == "violation"
    assert seen[("fail", "generic-exception-raised")] == "violation"
    assert seen[("check", "assert-validation")] == "violation"


def test_laziness_stdlib_and_performance_patterns(tmp_path: Path) -> None:
    source = """
        def f(lines, words, seen):
            total = sum([len(w) for w in words])
            ok = any([w for w in words])
            ordered = sorted([w for w in words])
            joined = ",".join([w for w in words])
            counts = {}
            for w in words:
                counts[w] = counts.get(w, 0) + 1
            text = ""
            for w in words:
                text += "x"
            acc = []
            for w in words:
                acc = acc + [w]
            names = list(words)
            for w in words:
                if w in names:
                    pass
            return lines.readlines()
    """
    found = scan_source(tmp_path, source, tags=("streaming",))
    rules = verdicts(found)
    assert rules["aggregate-over-list-comprehension"] == "violation"
    assert rules["manual-counter"] == "violation"
    assert rules["string-concat-in-loop"] == "violation"
    assert rules["sequence-concat-in-loop"] == "violation"
    assert rules["membership-in-list-inside-loop"] == "violation"
    assert rules["readlines-loads-everything"] == "violation"
    # any/sum/join over a list comprehension are flagged; sorted() legitimately needs a list
    assert [f["rule"] for f in found].count("aggregate-over-list-comprehension") == 3
    no_stream = verdicts(scan_source(tmp_path, source))
    assert no_stream["readlines-loads-everything"] == "hint"  # no streaming opportunity declared


def test_idiomatic_code_produces_no_false_positives(tmp_path: Path) -> None:
    found = scan_source(
        tmp_path,
        """
        from collections import Counter
        from dataclasses import dataclass, field


        @dataclass
        class Record:
            tags: list[str] = field(default_factory=list)


        def summarize(lines, k=3):
            counts = Counter(word for line in lines for word in line.split())
            total = sum(len(w) for w in counts)
            for index, (word, count) in enumerate(counts.most_common(k)):
                print(index, word, count)
            try:
                value = int(lines[0])
            except ValueError as error:
                raise RuntimeError("bad input") from error
            with open("out.txt", "w") as handle:
                handle.write(str(total + value))
            return [w for w, _ in counts.most_common(k)]
    """,
    )
    assert [f for f in found if f["verdict"] == "violation"] == []


def test_counting_an_iterable_by_hand_is_reported_as_a_manual_counter(tmp_path: Path) -> None:
    """``count = 0`` plus ``for _ in items: count += 1`` is ``len(items)`` (D-10-03)."""
    found = scan_source(
        tmp_path,
        """
        def counting(bucket):
            count = 0
            for _ in bucket:
                count += 1
            return count

        def uses_the_item(items):
            count = 0
            for item in items:
                count += len(item)
            return count

        def other_work(items):
            count = 0
            for item in items:
                count += 1
                print(item)
            return count

        def not_a_counter(items):
            total = 0
            for _ in items:
                total += 2
            return total

        def already_uses_len(items):
            return len(items)
    """,
    )
    by_symbol = {(f["symbol"], f["rule"]): f["verdict"] for f in found}
    assert by_symbol[("counting", "manual-counter")] == "violation"
    # The loop must discard its target and do nothing but increment; otherwise the count is real
    # work and reporting it would be a false positive.
    for symbol in ("uses_the_item", "other_work", "not_a_counter", "already_uses_len"):
        assert not any(name == symbol for name, _ in by_symbol), symbol


def test_string_accumulation_is_detected_for_every_provable_shape(tmp_path: Path) -> None:
    """``text += str(part) + "."`` is the same defect as ``text += "x"`` (D-10-03)."""
    found = scan_source(
        tmp_path,
        """
        def via_concat(parts):
            text = ""
            for part in parts:
                text += str(part) + "."
            return text

        def via_join(parts):
            text = ""
            for part in parts:
                text += "".join(part)
            return text

        def via_format(parts):
            text = ""
            for part in parts:
                text += f"{part}."
            return text

        def numeric(parts):
            total = 0
            for part in parts:
                total += part
            return total

        def outside_a_loop(parts):
            text = ""
            text += str(parts[0]) + "."
            return text
    """,
    )
    by_symbol = {(f["symbol"], f["rule"]): f["verdict"] for f in found}
    for symbol in ("via_concat", "via_join", "via_format"):
        assert by_symbol[(symbol, "string-concat-in-loop")] == "violation", symbol
    assert not any(name == "numeric" for name, _ in by_symbol)
    assert not any(name == "outside_a_loop" for name, _ in by_symbol)


def test_scanner_reports_unparsable_files_as_incomplete(tmp_path: Path) -> None:
    (tmp_path / "bad.py").write_text("def (:\n", encoding="utf-8")
    (tmp_path / "good.py").write_text("x = 1\n", encoding="utf-8")
    output = tmp_path / "out" / "context.json"
    code = scanner.main(
        ["scan", "--root", str(tmp_path), "--output", str(output), "bad.py", "good.py"]
    )
    document = json.loads(output.read_text(encoding="utf-8"))
    assert code == 2 and document["complete"] is False
    assert [f["parsed"] for f in document["files"]] == [False, True]


# ---------------------------------------------------------- build check and capture


def run_guest(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GUEST / script), *args], capture_output=True, text=True, check=False
    )


def test_syntax_check_distinguishes_candidate_syntax_errors_from_unreadable_input(
    tmp_path: Path,
) -> None:
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    out = tmp_path / "build.json"
    assert (
        run_guest(
            "pcb_syntax_check.py", "--root", str(tmp_path), "--output", str(out), "ok.py"
        ).returncode
        == 0
    )
    result = run_guest(
        "pcb_syntax_check.py", "--root", str(tmp_path), "--output", str(out), "ok.py", "bad.py"
    )
    assert result.returncode == 1  # candidate defect
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["files"][1]["ok"] is False and report["files"][1]["line"] == 1
    assert (
        run_guest(
            "pcb_syntax_check.py", "--root", str(tmp_path), "--output", str(out), "missing.py"
        ).returncode
        == 2
    )
    assert run_guest("pcb_syntax_check.py").returncode == 2


def test_capture_preserves_exit_codes_and_flags_missing_tools(tmp_path: Path) -> None:
    out, err = tmp_path / "o.txt", tmp_path / "e.txt"
    base = ["--stdout", str(out), "--stderr", str(err), "--"]
    ok = run_guest(
        "pcb_capture.py", *base, sys.executable, "-c", "print('hi'); raise SystemExit(1)"
    )
    assert ok.returncode == 1 and out.read_text(encoding="utf-8").strip() == "hi"
    missing = run_guest("pcb_capture.py", *base, "definitely-not-a-tool")
    assert missing.returncode == 127 and "tool not found" in err.read_text(encoding="utf-8")
    assert run_guest("pcb_capture.py", "--stdout", str(out)).returncode == 2
    big = run_guest(
        "pcb_capture.py", "--max-bytes", "10", *base, sys.executable, "-c", "print('x' * 100)"
    )
    assert big.returncode == 0 and out.stat().st_size == 10
    assert Path(str(out) + ".truncated").exists()


# ------------------------------------------------------------- pytest evidence parsing

GROUP = next(g for g in plugin.test_plan(frozen(plugin)).groups if g.group_id == "behaviour")
INVENTORY = next(g for g in plugin.inventory(frozen(plugin)) if g.group_id == "behaviour")
CASES = [c.case_id for c in INVENTORY.cases]


def _case(node: str, outcome: str = "pass", reason: str = "", **extra: Any) -> dict[str, Any]:
    return {
        "v": 1,
        "type": "case",
        "nodeid": node,
        "outcome": outcome,
        "phase": "call",
        "reason": reason,
        "duration_ms": 3,
        "stdout_digest": "sha256:" + "0" * 64,
        "stderr_digest": "sha256:" + "0" * 64,
        "markers": [],
        "resources": {"max_rss_kb": 1000},
        **extra,
    }


def _finish(status: int = 0) -> dict[str, Any]:
    return {
        "v": 1,
        "type": "session_finish",
        "exitstatus": status,
        "collected": len(CASES),
        "counts": {},
    }


def run_evidence(
    events: list[dict[str, Any]],
    *,
    exit_code: int | None = 0,
    timed_out: bool = False,
    report: bool = True,
    record: bool = True,
):  # type: ignore[no-untyped-def]
    files: dict[str, bytes] = {}
    if report:
        files["out/behaviour.jsonl"] = "\n".join(json.dumps(e) for e in events).encode()
    if record:
        files["_execution.json"] = json.dumps(
            {
                "schema_version": 1,
                "kind": "execution_record",
                "plan_id": GROUP.plan.plan_id,
                "exit_code": exit_code,
                "timed_out": timed_out,
                "duration_ms": 100,
                "stdout_digest": "sha256:" + "0" * 64,
                "stderr_digest": "sha256:" + "0" * 64,
                "stdout_tail": "",
                "stderr_tail": "",
                "isolation_tier": "development",
                "sandbox_id": "00000000-0000-4000-8000-000000000000",
            }
        ).encode()
    records, control = plugin.parse_test_group(
        GROUP, INVENTORY, DictArtifactReader(files), repetition=0
    )
    return records, control, reconcile([INVENTORY], records, [control])


def all_pass() -> list[dict[str, Any]]:
    return [*(_case(c) for c in CASES), _finish()]


def test_a_complete_passing_run_is_a_pass() -> None:
    records, control, verdict = run_evidence(all_pass())
    assert control.status == "finished" and len(records) == len(CASES)
    assert verdict.gate == "pass" and all(r.required for r in records)


def test_assertion_failures_and_case_timeouts_are_candidate_failures() -> None:
    events = [
        _case(CASES[0], "fail", "AssertionError"),
        *(_case(c) for c in CASES[1:-1]),
        _case(CASES[-1], "fail", "case_timeout"),
        _finish(1),
    ]
    _, control, verdict = run_evidence(events, exit_code=1)
    assert control.status == "finished" and verdict.gate == "fail"
    assert any("case_timeout" in reason for reason in verdict.reasons)


def test_hard_timeout_names_the_in_flight_case_and_is_a_candidate_failure() -> None:
    events = [_case(CASES[0]), {"type": "case_start", "nodeid": CASES[1]}]
    records, control, verdict = run_evidence(events, exit_code=None, timed_out=True)
    assert control.status == "candidate_timeout" and control.in_flight_case == CASES[1]
    assert verdict.gate == "fail" and len(records) == 1


def test_os_kill_during_a_case_is_a_candidate_failure() -> None:
    events = [{"type": "case_start", "nodeid": CASES[0]}]
    _, control, verdict = run_evidence(events, exit_code=137)
    assert control.status == "candidate_killed" and verdict.gate == "fail"


def test_missing_control_evidence_is_incomplete_never_a_pass() -> None:
    assert run_evidence([], report=False)[2].gate == "incomplete"  # no report at all
    assert (
        run_evidence([], record=False, report=False)[1].detail == "no supervisor execution record"
    )
    no_finish = [_case(c) for c in CASES]
    assert run_evidence(no_finish)[2].gate == "incomplete"  # harness died before finishing
    assert (
        run_evidence([*(_case(c) for c in CASES), _finish(3)], exit_code=3)[2].gate == "incomplete"
    )
    assert run_evidence([], exit_code=0)[2].gate == "incomplete"
    assert run_evidence([_finish(2)], exit_code=2)[2].gate == "incomplete"  # interrupted, no cause


def test_missing_skipped_or_xfailed_required_cases_never_count_as_passes() -> None:
    dropped = [*(_case(c) for c in CASES[1:]), _finish()]
    assert run_evidence(dropped)[2].gate == "incomplete"  # a required record is simply absent
    skipped = [_case(CASES[0], "skipped", "skipped"), *(_case(c) for c in CASES[1:]), _finish()]
    assert run_evidence(skipped)[2].gate == "fail"
    xpass = [_case(CASES[0], "error", "xpass"), *(_case(c) for c in CASES[1:]), _finish()]
    assert run_evidence(xpass)[2].gate == "fail"


def test_import_errors_are_attributed_to_candidate_or_harness_by_their_source() -> None:
    candidate = {
        "type": "collection_error",
        "nodeid": "tests/test_acceptance.py",
        "reason_tail": "work/solution.py:3: ModuleNotFoundError: No module named 'numpy'",
    }
    _, control, verdict = run_evidence([candidate, _finish(2)], exit_code=2)
    assert control.candidate_collection_errors == ("tests/test_acceptance.py",)
    assert verdict.gate == "fail"  # missing dependency in the candidate is its failure
    harness = {
        "type": "collection_error",
        "nodeid": "tests/test_acceptance.py",
        "reason_tail": "tests/test_acceptance.py:2: NameError",
    }
    _, control, verdict = run_evidence([harness, _finish(2)], exit_code=2)
    assert control.harness_collection_errors and verdict.gate == "incomplete"


def test_records_outside_the_declared_inventory_block_acceptance() -> None:
    extra = [*(_case(c) for c in CASES), _case("tests/test_acceptance.py::test_sneaked"), _finish()]
    records, control, verdict = run_evidence(extra)
    assert control.unexpected_cases == ("tests/test_acceptance.py::test_sneaked",)
    assert verdict.gate == "incomplete"


def test_parametrised_cases_aggregate_to_their_worst_outcome() -> None:
    events = [
        _case(f"{CASES[0]}[a]"),
        _case(f"{CASES[0]}[b]", "fail", "AssertionError"),
        *(_case(c) for c in CASES[1:]),
        _finish(1),
    ]
    records, _, verdict = run_evidence(events, exit_code=1)
    first = next(r for r in records if r.case_id == CASES[0])
    assert first.outcome == "fail" and verdict.gate == "fail"


def test_reconcile_requires_every_required_group() -> None:
    group = InventoryGroup(
        group_id="g", required=True, cases=(InventoryCase(case_id="t::a", required=True),)
    )
    optional = InventoryGroup(
        group_id="o", required=False, cases=(InventoryCase(case_id="t::b", required=True),)
    )
    ok = TestCaseRecord(
        group_id="g",
        case_id="t::a",
        required=True,
        outcome="pass",
        duration_ms=1,
        execution_identity="x",
    )
    control = GroupControl(group_id="g", repetition=0, status="finished")
    assert reconcile([group, optional], [ok], [control]).gate == "pass"  # optional group ignored
    assert reconcile([], [], []).gate == "incomplete"
    assert reconcile([group], [], []).gate == "incomplete"


@pytest.mark.skipif(
    sys.platform == "win32", reason="guest plugin uses POSIX resource/timers; covered in Docker"
)
def test_pytest_plugin_records_roundtrip_through_a_real_pytest_run(tmp_path: Path) -> None:
    """Runs the real reporting plugin under pytest (host interpreter) and parses its output."""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_sample.py").write_text(
        textwrap.dedent("""
        import pytest

        def test_pass():
            assert True

        def test_fail():
            assert 1 == 2

        def test_error():
            raise RuntimeError("boom")

        @pytest.mark.skip
        def test_skip():
            pass
    """),
        encoding="utf-8",
    )
    report = tmp_path / "out" / "report.jsonl"
    env = {"PYTHONPATH": str(GUEST), "PYTHONDONTWRITEBYTECODE": "1"}
    import os

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "pcb_pytest_report",
            "-p",
            "no:cacheprovider",
            "--rootdir",
            str(tmp_path),
            "--pcb-report",
            str(report),
            "--pcb-case-timeout",
            "5",
            str(tests / "test_sample.py"),
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={**os.environ, **env},
        check=False,
    )
    assert completed.returncode == 1
    events = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    cases = {e["nodeid"].split("::")[-1]: e["outcome"] for e in events if e["type"] == "case"}
    assert cases == {
        "test_pass": "pass",
        "test_fail": "fail",
        "test_error": "error",
        "test_skip": "skipped",
    }
    assert events[0]["type"] == "session_start" and events[-1]["type"] == "session_finish"
    assert events[-1]["exitstatus"] == 1


@pytest.mark.skipif(
    sys.platform == "win32", reason="guest plugin uses POSIX resource/timers; covered in Docker"
)
def test_pytest_plugin_interrupts_a_runaway_case(tmp_path: Path) -> None:
    import os

    (tmp_path / "test_loop.py").write_text(
        "def test_spin():\n    while True:\n        pass\n", encoding="utf-8"
    )
    report = tmp_path / "r.jsonl"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "pcb_pytest_report",
            "-p",
            "no:cacheprovider",
            "--pcb-report",
            str(report),
            "--pcb-case-timeout",
            "1",
            str(tmp_path / "test_loop.py"),
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=60,
        check=False,
        env={**os.environ, "PYTHONPATH": str(GUEST)},
    )
    events = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    case = next(e for e in events if e["type"] == "case")
    assert case["outcome"] == "fail" and case["reason"] == "case_timeout"
