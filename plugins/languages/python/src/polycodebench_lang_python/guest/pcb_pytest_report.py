"""Trusted pytest plugin: structured per-case evidence plus control records.

Loaded with ``-p pcb_pytest_report`` from the evaluator image. It writes JSON lines so the
supervisor can tell a candidate failure (a ``case`` record with outcome fail/error, including a
case timeout) from a harness failure (no ``session_finish`` record, an unreadable report, or a
non-zero pytest status without matching case records).

Property-based tests are pinned: the Hypothesis profile is derandomized, has a fixed example
count, no example database and no wall-clock deadline, and its version and settings are recorded.
"""

import hashlib
import json
import os
import resource
import signal
import time

import pytest

RECORD_VERSION = 1
TAIL_BYTES = 2000


class CaseTimeout(BaseException):
    """Raised by the per-case alarm; derives from BaseException so ``except Exception`` cannot hide it."""


def pytest_addoption(parser):
    group = parser.getgroup("pcb")
    group.addoption("--pcb-report", dest="pcb_report", default=None)
    group.addoption("--pcb-case-timeout", dest="pcb_case_timeout", type=float, default=0.0)
    group.addoption("--pcb-hypothesis-examples", dest="pcb_examples", type=int, default=50)


class _Writer:
    def __init__(self, path):
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_APPEND, 0o600)

    def write(self, record):
        record["v"] = RECORD_VERSION
        line = json.dumps(record, sort_keys=True, ensure_ascii=True, separators=(",", ":")) + "\n"
        os.write(self._fd, line.encode("ascii"))


def _digest(text):
    return "sha256:" + hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _tail(text):
    data = text.encode("utf-8", errors="replace")
    return data[-TAIL_BYTES:].decode("utf-8", errors="replace")


def _rusage():
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "max_rss_kb": int(usage.ru_maxrss),
        "user_ms": int(usage.ru_utime * 1000),
        "system_ms": int(usage.ru_stime * 1000),
    }


def pytest_configure(config):
    path = config.getoption("pcb_report")
    config._pcb_writer = _Writer(path) if path else None
    config._pcb_counts = {"pass": 0, "fail": 0, "error": 0, "skipped": 0}
    config._pcb_seen = {}
    hypothesis_info = None
    try:
        import hypothesis
        from hypothesis import HealthCheck, settings

        settings.register_profile(
            "pcb",
            derandomize=True,
            max_examples=config.getoption("pcb_examples"),
            database=None,
            deadline=None,
            print_blob=False,
            suppress_health_check=[HealthCheck.too_slow],
        )
        settings.load_profile("pcb")
        hypothesis_info = {
            "version": hypothesis.__version__,
            "derandomize": True,
            "max_examples": config.getoption("pcb_examples"),
            "database": None,
            "deadline": None,
            "shrinking": "hypothesis-default",
        }
    except ImportError:
        pass
    config._pcb_hypothesis = hypothesis_info


def pytest_sessionstart(session):
    writer = session.config._pcb_writer
    if writer:
        import platform

        writer.write(
            {
                "type": "session_start",
                "pytest": pytest.__version__,
                "python": platform.python_version(),
                "hypothesis": session.config._pcb_hypothesis,
                "case_timeout": session.config.getoption("pcb_case_timeout"),
            }
        )


@pytest.hookimpl(hookwrapper=True)
def pytest_make_collect_report(collector):
    outcome = yield
    report = outcome.get_result()
    writer = getattr(collector.config, "_pcb_writer", None)
    if writer is not None and report.failed:
        text = str(report.longrepr)
        writer.write(
            {
                "type": "collection_error",
                "nodeid": report.nodeid,
                "reason_digest": _digest(text),
                "reason_tail": _tail(text),
            }
        )


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    # Written before the case runs so an overall timeout can name the in-flight case.
    writer = item.config._pcb_writer
    if writer:
        writer.write({"type": "case_start", "nodeid": item.nodeid})


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    timeout = item.config.getoption("pcb_case_timeout")
    previous = None
    if timeout and timeout > 0 and hasattr(signal, "setitimer"):

        def on_alarm(signum, frame):
            raise CaseTimeout("case exceeded %.3f seconds" % timeout)

        previous = signal.signal(signal.SIGALRM, on_alarm)
        signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        yield
    finally:
        if previous is not None:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    config = item.config
    writer = config._pcb_writer
    state = config._pcb_seen.setdefault(
        item.nodeid, {"outcome": "pass", "phase": "call", "reason": "", "duration": 0.0}
    )
    state["duration"] += report.duration
    reason = ""
    if report.failed:
        timed_out = call.excinfo is not None and call.excinfo.errisinstance(CaseTimeout)
        assertion = call.excinfo is not None and call.excinfo.errisinstance(AssertionError)
        if report.when == "call" and (assertion or timed_out):
            state["outcome"] = "fail"
        else:
            state["outcome"] = "error"
        state["phase"] = report.when
        reason = (
            "case_timeout" if timed_out else (call.excinfo.typename if call.excinfo else "failed")
        )
        state["reason"] = reason
        state["longrepr"] = str(report.longrepr)
    elif report.skipped and state["outcome"] == "pass":
        state["outcome"] = "skipped"
        state["phase"] = report.when
        state["reason"] = "xfail" if hasattr(report, "wasxfail") else "skipped"
    if hasattr(report, "wasxfail") and report.when == "call" and report.passed:
        state["outcome"] = "error"
        state["reason"] = "xpass"
    if report.when == "teardown":
        counts = config._pcb_counts
        counts[state["outcome"]] += 1
        if writer:
            stdout = "".join(content for name, content in report.sections if "stdout" in name)
            stderr = "".join(content for name, content in report.sections if "stderr" in name)
            longrepr = state.get("longrepr", "")
            writer.write(
                {
                    "type": "case",
                    "nodeid": item.nodeid,
                    "outcome": state["outcome"],
                    "phase": state["phase"],
                    "reason": state["reason"],
                    "duration_ms": int(state["duration"] * 1000),
                    "stdout_digest": _digest(stdout),
                    "stderr_digest": _digest(stderr),
                    "stdout_tail": _tail(stdout),
                    "stderr_tail": _tail(stderr),
                    "failure_digest": _digest(longrepr) if longrepr else None,
                    "failure_tail": _tail(longrepr) if longrepr else None,
                    "markers": sorted(mark.name for mark in item.iter_markers()),
                    "resources": _rusage(),
                }
            )


def pytest_sessionfinish(session, exitstatus):
    writer = session.config._pcb_writer
    if writer:
        writer.write(
            {
                "type": "session_finish",
                "exitstatus": int(exitstatus),
                "collected": session.testscollected,
                "counts": session.config._pcb_counts,
                "finished_at_ms": int(time.time() * 1000),
            }
        )
