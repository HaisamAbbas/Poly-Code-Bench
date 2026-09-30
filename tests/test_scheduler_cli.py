"""Operator command error handling and periodic reaping."""

from types import SimpleNamespace

import pytest
from polycodebench_core.application_errors import LeaseLost
from polycodebench_orchestration import cli
from sqlalchemy.exc import SQLAlchemyError


def test_cli_requires_verified_service_configuration(monkeypatch, capsys):
    monkeypatch.delenv("PCB_DATABASE_URL", raising=False)
    monkeypatch.delenv("PCB_SERVICE_IDENTITY", raising=False)
    assert cli.main(["reap"]) == 2
    assert "PCB_SERVICE_IDENTITY" in capsys.readouterr().err


def _configure(monkeypatch, reap):
    disposed = []
    database = SimpleNamespace(engine=object(), dispose=lambda: disposed.append(True))
    monkeypatch.setenv("PCB_DATABASE_URL", "postgresql://local-test")
    monkeypatch.setenv("PCB_SERVICE_IDENTITY", "verified-local-scheduler")
    monkeypatch.setattr(cli, "Database", lambda url: database)
    monkeypatch.setattr(
        cli, "PostgresJobRepository", lambda engine: SimpleNamespace(reap_expired=reap)
    )
    return disposed


def test_watch_reaper_runs_at_specified_interval_and_disposes_on_interrupt(monkeypatch, capsys):
    batches = []
    disposed = _configure(monkeypatch, lambda *, limit: batches.append(limit) or ())
    intervals = []

    def wait(seconds):
        intervals.append(seconds)
        if len(intervals) == 2:
            raise KeyboardInterrupt()

    monkeypatch.setattr(cli.time, "sleep", wait)
    assert cli.main(["reap", "--watch", "--limit", "25"]) == 130
    assert batches == [25, 25] and intervals == [30, 30]
    assert disposed == [True]
    assert capsys.readouterr().out == "[]\n[]\n"


def test_one_shot_reaper_does_not_wait(monkeypatch, capsys):
    disposed = _configure(monkeypatch, lambda *, limit: ())
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: pytest.fail("one-shot reaper waited"))
    assert cli.main(["reap"]) == 0
    assert disposed == [True] and capsys.readouterr().out == "[]\n"


@pytest.mark.parametrize(
    "error,code,message",
    [
        (ValueError("reaper batch size must be in [1,1000]"), 2, "invalid scheduler configuration"),
        (LeaseLost(), 1, "LEASE_LOST"),
        (SQLAlchemyError("connection contained secret-value"), 1, "DEPENDENCY_UNAVAILABLE"),
    ],
)
def test_reaper_errors_return_actionable_codes_without_database_details(
    monkeypatch, capsys, error, code, message
):
    def reap(*, limit):
        raise error

    disposed = _configure(monkeypatch, reap)
    assert cli.main(["reap"]) == code
    output = capsys.readouterr().err
    assert message in output and "secret-value" not in output
    assert disposed == [True]
