"""Go guest script tests: scanner verdicts, event classification, the module audit, the runner.

These load by path the exact files that ship inside the pinned images, so what the host parsers
call is what the guest runs. Everything here is deterministic and offline: the scanner, the event
classifier and the module audit are pure line analysis over strings supplied by the test, and the
runner is invoked as a subprocess whose "tool" is the interpreter itself rather than a Go
toolchain.

Real-tool evidence for these scripts lives in ``tests/test_go_docker.py`` (opt-in,
``PCB_TEST_DOCKER=1``): the pinned Go image runs the acceptance gate, the analyzers and the race
detector, and the hung-tool deadline below is covered there because ``pcb_go_run.py`` stops a
process group with ``os.killpg``, which exists only on POSIX.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUEST = ROOT / "plugins" / "languages" / "go" / "src" / "polycodebench_lang_go" / "guest"
sys.path.insert(0, str(GUEST.parent))
from polycodebench_lang_go.guestmods import load_guest  # noqa: E402

scan = load_guest("pcb_go_scan")
report = load_guest("pcb_go_test_report")
audit = load_guest("pcb_go_mod_audit")
run = load_guest("pcb_go_run")

CLEAN = """package topwords

import "strings"

// TopWords returns the ranked words of text.
func TopWords(text string, k int) []Count {
\tcounts := make(map[string]int)
\tfor _, word := range strings.Fields(text) {
\t\tcounts[word]++
\t}
\tif len(counts) > k {
\t\tcounts = nil
\t}
\treturn nil
}
"""


def findings(text: str, path: str = "topwords.go") -> list[dict]:
    return scan.scan_text(path, text)


def rules(text: str) -> list[str]:
    return [item["rule"] for item in findings(text)]


def verdicts(text: str) -> dict[str, str]:
    return {item["rule"]: item["verdict"] for item in findings(text)}


def test_guest_command_runner_uses_only_a_contained_relative_working_directory() -> None:
    options, command = run._parse(
        [
            "pcb_go_run.py",
            "--name",
            "out/build",
            "--deadline",
            "10",
            "--cwd",
            "work",
            "--",
            "go",
            "build",
            "./...",
        ]
    )
    assert options["cwd"] == "work"
    assert command == ["go", "build", "./..."]
    with pytest.raises(SystemExit, match="2"):
        run._parse(
            [
                "pcb_go_run.py",
                "--name",
                "out/build",
                "--deadline",
                "10",
                "--cwd",
                "../outside",
                "--",
                "go",
                "build",
            ]
        )


# ------------------------------------------------------------------------------- scanner


def test_clean_go_code_produces_no_findings() -> None:
    assert findings(CLEAN) == []


def test_a_discarded_error_is_a_violation_only_when_the_call_returns_one() -> None:
    ignored = """package topwords

func record(counts map[string]int, word string) error { return nil }

func TopWords(text string, k int) []Count {
\tcounts := map[string]int{}
\t_ = record(counts, "a")
\tvalue := 3
\t_ = value
\t_ = k
\treturn nil
}
"""
    assert rules(ignored) == ["error-ignored-blank"]


def test_a_deferred_close_is_a_hint_not_a_violation() -> None:
    deferred = """package topwords

import "os"

func TopWords(text string, k int) []Count {
\tdefer os.Stdin.Close()
\t_ = text
\treturn nil
}
"""
    assert verdicts(deferred) == {"error-ignored-deferred": "hint"}


def test_a_wrapped_error_is_not_a_broken_chain() -> None:
    wrapped = """package topwords

import (
\t"errors"
\t"fmt"
)

func TopWords(text string, k int) []Count {
\tif _, err := read(); err != nil {
\t\treturn nil
\t}
\t_ = text
\treturn nil
}

func read() (int, error) { return 0, errors.New("x") }

var _ = fmt.Errorf("wrapped: %w", nil)
"""
    assert rules(wrapped) == []


def test_a_sentinel_compared_with_equality_is_a_violation() -> None:
    compared = """package topwords

import "io"

func TopWords(text string, k int) []Count {
\tif _, err := read(); err == io.EOF {
\t\treturn nil
\t}
\t_ = text
\treturn nil
}

func read() (int, error) { return 0, nil }
"""
    assert rules(compared) == ["error-sentinel-comparison"]


def test_err_equal_nil_is_correct_go_and_never_reported() -> None:
    compared = """package topwords

func TopWords(text string, k int) []Count {
\tif _, err := read(); err == nil {
\t\treturn nil
\t}
\t_ = text
\treturn nil
}

func read() (int, error) { return 0, nil }
"""
    assert rules(compared) == []


def test_a_replaced_context_is_a_violation_but_a_root_context_is_not() -> None:
    replaced = """package topwords

import "context"

func TopWords(ctx context.Context, text string, k int) []Count {
\tctx = context.Background()
\t_ = ctx
\treturn nil
}
"""
    assert rules(replaced) == ["context-replaced"]

    root = """package topwords

import "context"

func TopWords(text string, k int) []Count {
\tctx := context.Background()
\t_ = ctx
\treturn nil
}
"""
    assert verdicts(root) == {"context-created-at-entry": "benign_in_context"}


def test_a_context_that_is_never_cancelled_is_a_violation() -> None:
    leaked = """package topwords

import "context"

func TopWords(ctx context.Context, text string, k int) []Count {
\tchild, cancel := context.WithTimeout(ctx, 1)
\t_ = child
\t_ = cancel
\treturn nil
}
"""
    assert rules(leaked) == ["context-cancel-not-deferred"]


def test_a_blocking_function_that_ignores_cancellation_is_a_violation() -> None:
    blocking = """package topwords

import "context"

func TopWords(ctx context.Context, text string, k int) []Count {
\tfor _, word := range split(text) {
\t\t_ = word
\t}
\treturn nil
}

func split(text string) []string { return nil }
"""
    assert rules(blocking) == ["context-not-honoured"]

    honoured = """package topwords

import "context"

func TopWords(ctx context.Context, text string, k int) []Count {
\tfor _, word := range split(text) {
\t\tselect {
\t\tcase <-ctx.Done():
\t\t\treturn nil
\t\tdefault:
\t\t}
\t\t_ = word
\t}
\treturn nil
}

func split(text string) []string { return nil }
"""
    assert rules(honoured) == []


def test_an_unjoined_goroutine_is_a_violation_and_a_joined_one_is_not() -> None:
    unjoined = """package topwords

func TopWords(text string, k int) []Count {
\tgo func() {
\t\t_ = text
\t}()
\treturn nil
}
"""
    assert rules(unjoined) == ["goroutine-not-joined"]

    joined = """package topwords

import "sync"

func TopWords(text string, k int) []Count {
\tvar group sync.WaitGroup
\tgroup.Add(1)
\tgo func() {
\t\tdefer group.Done()
\t\t_ = text
\t}()
\tgroup.Wait()
\treturn nil
}
"""
    assert rules(joined) == []


def test_every_finding_names_its_enclosing_function() -> None:
    text = """package topwords

func helper(text string) error { return nil }

func TopWords(text string, k int) []Count {
\t_ = helper(text)
\treturn nil
}
"""
    located = findings(text)
    assert [item["symbol"] for item in located] == ["TopWords"]
    assert located[0]["line"] == 6


def test_scanner_reports_every_file_and_marks_an_unreadable_one_incomplete(tmp_path: Path) -> None:
    (tmp_path / "ok.go").write_text(CLEAN, encoding="utf-8")
    result = scan.scan(
        str(tmp_path), ["ok.go", "missing.go"], {"opportunities": {"error_handling"}}
    )
    assert result["schema"] == "pcb-go-scan-v1"
    assert result["complete"] is False
    assert [item["parsed"] for item in result["files"]] == [True, False]


def test_scanner_exit_codes_separate_clean_from_violations_and_incomplete(tmp_path: Path) -> None:
    good = tmp_path / "good.go"
    good.write_text(CLEAN, encoding="utf-8")
    bad = tmp_path / "bad.go"
    bad.write_text(
        "package topwords\n\nfunc f(text string) error { return nil }\n\nfunc g(t string) {\n"
        "\t_ = f(t)\n}\n",
        encoding="utf-8",
    )
    out = tmp_path / "report.json"

    def run(paths: list[str]) -> int:
        return subprocess.run(  # noqa: S603
            [
                sys.executable,
                str(GUEST / "pcb_go_scan.py"),
                "--root",
                str(tmp_path),
                "--output",
                str(out),
                *paths,
            ],
            capture_output=True,
            check=False,
        ).returncode

    assert run(["good.go"]) == 0
    assert run(["bad.go"]) == 1
    assert run(["absent.go"]) == 2


# ---------------------------------------------------------------------- test event records


def test_go_test_events_become_case_records() -> None:
    stream = "\n".join(
        [
            json.dumps({"Action": "run", "Test": "TestOne"}),
            json.dumps({"Action": "output", "Test": "TestOne", "Output": "ok\n"}),
            json.dumps({"Action": "pass", "Test": "TestOne", "Elapsed": 0.01}),
            json.dumps({"Action": "run", "Test": "TestTwo"}),
            json.dumps({"Action": "fail", "Test": "TestTwo", "Elapsed": 0.02}),
            json.dumps({"Action": "fail", "Package": "pcb.local/topwords", "Elapsed": 0.03}),
        ]
    )
    records = report.build_records(stream, "", 1, False)
    kinds = [item["kind"] for item in records]
    assert kinds == ["case_start", "case_start", "case", "case", "session_finish"]
    outcomes = {(item["name"], item["outcome"]) for item in records if item["kind"] == "case"}
    assert outcomes == {("TestOne", "pass"), ("TestTwo", "fail")}
    finish = records[-1]
    assert finish["summary"] is True
    assert finish["declared_tests"] == 2
    assert finish["build_ok"] is True


def test_a_stream_with_no_package_verdict_is_not_a_completed_run() -> None:
    records = report.build_records("", "", 1, False)
    assert records[-1]["summary"] is False
    assert records[-1]["declared_tests"] is None


def test_a_build_failure_is_recorded_as_such() -> None:
    stream = "\n".join(
        [
            json.dumps({"Action": "build-fail", "Package": "pcb.local/topwords"}),
            json.dumps({"Action": "fail", "Package": "pcb.local/topwords", "Elapsed": 0.01}),
        ]
    )
    finish = report.build_records(stream, "", 1, False)[-1]
    assert finish["build_ok"] is False
    assert finish["summary"] is True


def test_a_skipped_test_is_evidence_but_not_a_failure() -> None:
    stream = "\n".join(
        [
            json.dumps({"Action": "run", "Test": "TestSkip"}),
            json.dumps({"Action": "skip", "Test": "TestSkip", "Elapsed": 0.0}),
            json.dumps({"Action": "pass", "Package": "pcb.local/topwords", "Elapsed": 0.01}),
        ]
    )
    cases = [item for item in report.build_records(stream, "", 0, False) if item["kind"] == "case"]
    assert [item["outcome"] for item in cases] == ["skipped"]


def test_garbage_between_events_does_not_lose_the_records_that_parsed() -> None:
    stream = "not json\n" + json.dumps({"Action": "pass", "Package": "p", "Elapsed": 0.01})
    finish = report.build_records(stream, "", 0, False)[-1]
    assert finish["summary"] is True


# --------------------------------------------------------------------------- module audit

SNAPSHOT = {
    "schema": "pcb-advisory-snapshot-v1",
    "source": "test",
    "advisories": {
        "GO-2026-0001": {"module": "example.com/dep", "severity": "high", "fixed_in": "v1.4.3"}
    },
}


def test_the_audit_reports_an_advisory_below_its_fix(tmp_path: Path) -> None:
    mod = tmp_path / "go.mod"
    mod.write_bytes(b"module pcb.local/demo\n\ngo 1.26\n\nrequire example.com/dep v1.4.2\n")
    sums = tmp_path / "go.sum"
    sums.write_bytes(b"example.com/dep v1.4.2 h1:AAAA=\n")
    result = audit.audit(str(mod), str(sums), SNAPSHOT)
    assert result["findings"][0]["advisory"] == "GO-2026-0001"
    assert result["findings"][0]["fixed_in"] == "v1.4.3"


def test_the_audit_is_quiet_at_or_above_the_fixed_version(tmp_path: Path) -> None:
    mod = tmp_path / "go.mod"
    mod.write_bytes(b"module pcb.local/demo\n\ngo 1.26\n\nrequire example.com/dep v1.4.3\n")
    sums = tmp_path / "go.sum"
    sums.write_bytes(b"example.com/dep v1.4.3 h1:AAAA=\n")
    assert audit.audit(str(mod), str(sums), SNAPSHOT)["findings"] == []


def test_an_unpinned_module_is_reported_as_unpinned(tmp_path: Path) -> None:
    mod = tmp_path / "go.mod"
    mod.write_bytes(b"module pcb.local/demo\n\ngo 1.26\n\nrequire example.com/dep v1.4.2\n")
    result = audit.audit(str(mod), str(tmp_path / "absent.sum"), SNAPSHOT)
    assert result["unpinned"] == ["example.com/dep@1.4.2"]


def test_an_empty_advisory_snapshot_fails_closed(tmp_path: Path) -> None:
    mod = tmp_path / "go.mod"
    mod.write_bytes(b"module pcb.local/demo\n\ngo 1.26\n")
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps({"advisories": {}}), encoding="utf-8")
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(GUEST / "pcb_go_mod_audit.py"),
            "--module",
            str(mod),
            "--sum",
            str(tmp_path / "absent.sum"),
            "--advisories",
            str(snapshot),
            "--output",
            str(tmp_path / "out.json"),
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert b"empty" in result.stderr


# -------------------------------------------------------------------------------- runner


def _run_runner(tmp_path: Path, args: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(GUEST / "pcb_go_run.py"), *args],
        capture_output=True,
        check=False,
    )


def test_the_runner_passes_through_the_tool_status_and_records_it(tmp_path: Path) -> None:
    name = tmp_path / "out" / "run"
    result = _run_runner(
        tmp_path,
        [
            "--name",
            str(name),
            "--deadline",
            "30",
            "--",
            sys.executable,
            "-c",
            "import sys; print('hello'); sys.exit(7)",
        ],
    )
    assert result.returncode == 7
    record = json.loads((tmp_path / "out" / "run.run.json").read_text("utf-8"))
    assert record["schema"] == "pcb-go-run-v1"
    assert record["exit_code"] == 7
    assert record["timed_out"] is False
    assert b"hello" in (tmp_path / "out" / "run.out").read_bytes()


def test_the_runner_reports_a_missing_tool_as_127(tmp_path: Path) -> None:
    name = tmp_path / "out" / "run"
    result = _run_runner(
        tmp_path, ["--name", str(name), "--deadline", "30", "--", "no-such-go-tool"]
    )
    assert result.returncode == 127
    record = json.loads((tmp_path / "out" / "run.run.json").read_text("utf-8"))
    assert record["note"] == "not-found"


@pytest.mark.skipif(
    os.name == "nt",
    reason="the guest runner stops a hung tool with os.killpg, which only exists on POSIX; the "
    "deadline path is exercised in the Linux sandbox (tests/test_go_docker.py)",
)
def test_the_runner_stops_a_hung_tool_at_its_deadline(tmp_path: Path) -> None:
    name = tmp_path / "out" / "run"
    result = _run_runner(
        tmp_path,
        [
            "--name",
            str(name),
            "--deadline",
            "2",
            "--",
            sys.executable,
            "-c",
            "import time; print('started', flush=True); time.sleep(60)",
        ],
    )
    assert result.returncode == 124
    record = json.loads((tmp_path / "out" / "run.run.json").read_text("utf-8"))
    assert record["timed_out"] is True
    assert b"started" in (tmp_path / "out" / "run.out").read_bytes()


def test_the_runner_removes_the_cleanup_directory_on_every_path(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "entry").write_text("x", encoding="utf-8")
    name = tmp_path / "out" / "run"
    _run_runner(
        tmp_path,
        [
            "--name",
            str(name),
            "--deadline",
            "30",
            "--cleanup",
            str(cache),
            "--",
            sys.executable,
            "-c",
            "raise SystemExit(3)",
        ],
    )
    assert not cache.exists()


@pytest.mark.parametrize("argv", [[], ["--name", "x"], ["--name", "x", "--deadline", "5"]])
def test_the_runner_rejects_a_command_line_it_cannot_use(tmp_path: Path, argv: list[str]) -> None:
    assert _run_runner(tmp_path, argv).returncode == 2
