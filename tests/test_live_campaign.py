"""Offline tests for scripts/live_campaign.py (no database, object store or provider calls)."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import uuid4

import pytest
from polycodebench_core.model_planning import cost_bound, effective_capabilities
from polycodebench_core.models import TaskSet

ROOT = Path(__file__).resolve().parents[1]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "live_campaign_under_test", ROOT / "scripts" / "live_campaign.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


lc = _load()


def _digest(seed: str) -> str:
    return "sha256:" + (seed * 64)[:64]


# ----------------------------------------------------------------------------- money cap


def test_max_usd_defaults_and_hard_limits() -> None:
    assert lc.parse_max_usd(lc.DEFAULT_MAX_USD) == 2_000_000
    assert lc.parse_max_usd("0.0000011") == 2  # rounded up, never under-counted
    assert lc.parse_max_usd("5") == 5_000_000
    for bad in ("0", "-1", "abc", "NaN", "10.01", "100"):
        with pytest.raises(lc.StepError):
            lc.parse_max_usd(bad)
    with pytest.raises(lc.StepError, match="at most 5.00"):
        lc.parse_max_usd("6.00")


def test_main_refuses_cap_above_ten_before_any_io(tmp_path: Path) -> None:
    code = lc.main(["run", "--max-usd", "10.50", "--state-dir", str(tmp_path / "state")])
    assert code == 2
    assert not (tmp_path / "state").exists()


# ----------------------------------------------------------------------------- packs


def _pack(root: Path, *names: str) -> Path:
    for name in names:
        (root / name).mkdir(parents=True)
        (root / name / "manifest.yaml").write_text("kind: x\n", encoding="utf-8")
    (root / "not-a-pack").mkdir()
    return root


def test_select_packages_limit_and_slugs(tmp_path: Path) -> None:
    pack = _pack(tmp_path / "python-pilot-arm64", "b-task", "a-task", "c-task")
    assert [p.name for p in lc.select_packages(pack)] == ["a-task", "b-task", "c-task"]
    assert [p.name for p in lc.select_packages(pack, limit=2)] == ["a-task", "b-task"]
    assert [p.name for p in lc.select_packages(pack, slugs=["c-task", "a-task"])] == [
        "a-task",
        "c-task",
    ]
    with pytest.raises(lc.StepError, match="unknown task package"):
        lc.select_packages(pack, slugs=["missing"])
    with pytest.raises(lc.StepError):
        lc.select_packages(pack, limit=0)
    with pytest.raises(lc.StepError):
        lc.select_packages(tmp_path / "absent")


# ----------------------------------------------------------------------------- documents


@pytest.mark.parametrize("count", [1, 3, 7, 12])
def test_task_set_document_is_a_valid_fixture_task_set(count: int) -> None:
    weights = lc.split_weights(count)
    assert sum(weights) == 10_000 and max(weights) - min(weights) <= 1
    members = [
        {
            "task_digest": _digest(format(index, "x")),
            "cluster_id": f"cluster-{index}",
            "stratum_id": "python-pilot",
            "earliest_public_at": None,
            "exposure_confidence": "estimated",
        }
        for index in range(count)
    ]
    document = lc.build_task_set_document(
        name="live-python-pilot-abc",
        members=members,
        split_seed="42",
        scoring_policy_digest=_digest("f"),
    )
    parsed = TaskSet.model_validate(document)
    assert parsed.split == "fixture"
    assert len(parsed.members) == count


def test_run_config_carries_fields_the_run_repository_and_solve_loader_read() -> None:
    document = lc.build_run_config_document(
        task_set_digest=_digest("a"),
        model_config_digest=_digest("b"),
        protocol_id="single-shot-v1",
        protocol_digest=_digest("c"),
        budget_profile="single-shot-small-v1",
        samples_per_task=1,
        master_seed="123",
        temperature="0.200000",
        seed_policy="omit",
        evaluation_policy_digest=_digest("d"),
        hardware_class="local-docker-development",
    )
    # PostgresRunRepository._create_run_and_attempts
    assert document["kind"] == "run_config"
    assert document["sampling"]["task_set_digest"] == _digest("a")
    assert document["sampling"]["samples_per_task"] == 1
    assert document["sampling"]["master_seed"] == "123"
    # DatabaseAssignmentLoader
    assert document["model_config_digest"] == _digest("b")
    assert document["protocol_id"] == "single-shot-v1"
    assert document["budget_profile"] == "single-shot-small-v1"


def test_run_token_limits_cover_byte_bound_and_retries() -> None:
    assert lc.run_token_limits(attempts=2, input_context_tokens=32_000, output_tokens=8_000) == (
        2 * 4 * 32_000 * 3,
        2 * 8_000 * 3,
    )


# ----------------------------------------------------------------------------- models config


def test_shipped_models_are_strictly_capped_and_plan_under_default_cap() -> None:
    from polycodebench_orchestration.gateway.adapters.openai_compatible import (
        OpenAICompatibleAdapter,
    )
    from polycodebench_orchestration.gateway.plan import plan_model_run
    from polycodebench_services.solve_protocols import load_protocol_directory

    models = lc.load_models(lc.DEFAULT_MODELS)
    assert {"openrouter-glm-4.6", "zhipu-glm-4.6"} <= set(models)
    protocol = load_protocol_directory(lc.DEFAULT_PROTOCOL_DIRECTORY)["single-shot-v1"]
    adapter = OpenAICompatibleAdapter()
    for key, spec in models.items():
        config = lc.build_model_config(spec, str(uuid4()))
        caps = effective_capabilities(adapter.capabilities(), config.declared_capabilities)
        assert cost_bound(config, caps, request_bytes=1).strict_cap_eligible, key
        plan = plan_model_run(
            config=config,
            protocol=protocol.to_definition(),
            adapter=adapter,
            tasks=12,
            samples_per_task=1,
            max_request_bytes=4 * protocol.context.max_input_context_tokens,
        )
        assert plan.compatible, (key, plan.blockers)
        assert plan.worst_case_money_micro_usd is not None
        assert plan.worst_case_money_micro_usd <= lc.parse_max_usd(lc.DEFAULT_MAX_USD), key


def test_zhipu_base_url_matches_the_known_chat_completions_url() -> None:
    from polycodebench_core.endpoint_policy import (
        EndpointNetworkPolicy,
        NetworkPolicyKind,
        parse_endpoint_url,
    )

    spec = lc.load_models(lc.DEFAULT_MODELS)["zhipu-glm-4.6"]
    policy = EndpointNetworkPolicy(
        kind=NetworkPolicyKind.PUBLIC_ALLOWLIST, allowed_hosts=tuple(spec["allowed_hosts"])
    )
    parsed = parse_endpoint_url(spec["base_url"], policy)
    # OpenAICompatibleAdapter posts to {base}/chat/completions (scripts/glm_generation_batch.py).
    assert parsed.url + "/chat/completions" == "https://api.z.ai/api/paas/v4/chat/completions"


def test_models_config_rejects_unsafe_entries(tmp_path: Path) -> None:
    base = json.loads(lc.DEFAULT_MODELS.read_text(encoding="utf-8"))
    entry = dict(base["models"]["zhipu-glm-4.6"], base_url="http://api.z.ai/api/paas/v4")
    path = tmp_path / "models.json"
    path.write_text(json.dumps({**base, "models": {"x": entry}}), encoding="utf-8")
    with pytest.raises(lc.StepError, match="HTTPS"):
        lc.load_models(path)


def test_secret_env_names_match_the_local_resolver() -> None:
    assert lc.secret_env_name("secret://models/openrouter") == "PCBSECRET__MODELS__OPENROUTER"
    assert lc.secret_env_name("secret://models/zhipu") == "PCBSECRET__MODELS__ZHIPU"
    with pytest.raises(lc.StepError):
        lc.secret_env_name("secret://judge/zhipu")


# ----------------------------------------------------------------------------- env / CLI glue


def test_scoped_env_sets_flags_only_for_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PCB_WORKER_DISPATCH_ENABLED", "false")
    monkeypatch.delenv("PCB_LOCAL_SCORING_ENABLED", raising=False)
    seen: dict[str, str | None] = {}

    def fake_main(argv: list[str]) -> int:
        seen["dispatch"] = os.environ.get("PCB_WORKER_DISPATCH_ENABLED")
        seen["scoring"] = os.environ.get("PCB_LOCAL_SCORING_ENABLED")
        print("log line that is not json")
        print(json.dumps({"claimed": True, "argv": argv}))
        return 0

    code, output = lc.call_cli(
        "fake",
        fake_main,
        ["local-run"],
        env={"PCB_WORKER_DISPATCH_ENABLED": "true", "PCB_LOCAL_SCORING_ENABLED": "true"},
    )
    assert code == 0 and output == [{"claimed": True, "argv": ["local-run"]}]
    assert seen == {"dispatch": "true", "scoring": "true"}
    assert os.environ["PCB_WORKER_DISPATCH_ENABLED"] == "false"
    assert "PCB_LOCAL_SCORING_ENABLED" not in os.environ


def test_call_cli_raises_on_failure_and_restores_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PCB_LOCAL_WORKER_SETUP_ENABLED", raising=False)
    with pytest.raises(lc.StepError, match="exited with status 2"):
        lc.call_cli("fake", lambda argv: 2, [], env={"PCB_LOCAL_WORKER_SETUP_ENABLED": "true"})
    assert "PCB_LOCAL_WORKER_SETUP_ENABLED" not in os.environ
    code, output = lc.call_cli("fake", lambda argv: 2, [], allowed_codes=frozenset({0, 2}))
    assert (code, output) == (2, [])


def test_parse_json_output_accepts_pretty_documents() -> None:
    assert lc.parse_json_output(json.dumps({"a": {"b": 1}}, indent=2)) == [{"a": {"b": 1}}]
    assert lc.parse_json_output("") == []


def test_env_file_does_not_override_and_maps_object_store_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("PCB_X_ONE", "PCB_X_TWO", "AWS_ACCESS_KEY_ID", "PCB_LOCAL_S3_ACCESS_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PCB_X_TWO", "kept")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nPCB_X_ONE=1\nPCB_X_TWO=replaced\nPCB_LOCAL_S3_ACCESS_KEY=k\n", "utf-8"
    )
    loaded = lc.load_env_file(env_file)
    assert os.environ["PCB_X_ONE"] == "1" and os.environ["PCB_X_TWO"] == "kept"
    assert os.environ["AWS_ACCESS_KEY_ID"] == "k"
    assert "PCB_X_TWO" not in loaded
    for name in ("PCB_X_ONE", "AWS_ACCESS_KEY_ID", "PCB_LOCAL_S3_ACCESS_KEY"):
        monkeypatch.delenv(name, raising=False)


# ----------------------------------------------------------------------------- solve loop


def test_solve_loop_claims_until_no_pending_job() -> None:
    pending = [3]
    sleeps: list[float] = []

    def claim() -> bool:
        pending[0] -= 1
        return True

    claims = lc.solve_until_done(
        pending_count=lambda: pending[0],
        claim_once=claim,
        budget_exhausted=lambda: False,
        sleep=sleeps.append,
    )
    assert claims == 3 and sleeps == []


def test_solve_loop_waits_for_retry_then_times_out() -> None:
    sleeps: list[float] = []
    with pytest.raises(lc.StepError, match="none became claimable"):
        lc.solve_until_done(
            pending_count=lambda: 1,
            claim_once=lambda: False,
            budget_exhausted=lambda: False,
            sleep=sleeps.append,
            poll_seconds=5,
            idle_timeout_seconds=12,
        )
    assert sleeps == [5, 5, 5]


def test_solve_loop_stops_when_the_run_budget_is_used() -> None:
    claims: list[int] = []
    with pytest.raises(lc.StepError, match="budget is exhausted"):
        lc.solve_until_done(
            pending_count=lambda: 2,
            claim_once=lambda: claims.append(1) or True,  # type: ignore[func-returns-value]
            budget_exhausted=lambda: True,
        )
    assert claims == []


# ----------------------------------------------------------------------------- state / steps


def test_state_round_trips_for_resume(tmp_path: Path) -> None:
    state = lc.State(tmp_path / "s")
    state.section("run").update(run_id="r1", attempt_ids=["a"])
    state.save()
    again = lc.State(tmp_path / "s")
    assert again.section("run") == {"run_id": "r1", "attempt_ids": ["a"]}
    assert not (tmp_path / "s" / "state.tmp").exists()


def test_all_excludes_publish_unless_requested() -> None:
    assert lc.steps_for("all", publish=False) == [
        "tasks",
        "endpoint",
        "run",
        "solve",
        "grade",
        "score",
        "summary",
    ]
    assert lc.steps_for("all", publish=True)[-1] == "publish"
    assert lc.steps_for("grade", publish=False) == ["grade"]


def _context(tmp_path: Path, *argv: str) -> Any:
    args = lc._parser().parse_args([*argv, "--state-dir", str(tmp_path)])
    return lc.Context(args)


def test_run_step_requires_frozen_tasks_and_endpoint(tmp_path: Path) -> None:
    ctx = _context(tmp_path, "run")
    with pytest.raises(lc.StepError, match="frozen task set"):
        lc.step_run(ctx)
    ctx.state.section("task_set").update(status="frozen", members=["t@1"], digest=_digest("a"))
    with pytest.raises(lc.StepError, match="approved endpoint"):
        lc.step_run(ctx)


def test_spend_steps_require_explicit_opt_in(tmp_path: Path) -> None:
    ctx = _context(tmp_path, "solve")
    with pytest.raises(lc.StepError, match="--allow-spend"):
        ctx.require_spend("solving")
    with pytest.raises(lc.StepError, match="created run"):
        lc.step_solve(ctx)
    with pytest.raises(lc.StepError, match="created run"):
        lc.step_grade(ctx)


def test_publish_is_skipped_until_the_script_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(lc, "PUBLISH_SCRIPT", lc.ROOT / "scripts" / "does-not-exist.py")
    lc.step_publish(_context(tmp_path, "publish"))
    assert "skipped" in capsys.readouterr().out
    command = lc.publish_command(script=Path("p.py"), run_id="r", python="py")
    # publish_live_release.py reads every attempt of the run; it has no per-evaluation filter.
    assert command == ["py", "p.py", "--run-id", "r"]


# ----------------------------------------------------------------------------- solve worker


def test_runtime_image_resolution_and_resource_spec(tmp_path: Path) -> None:
    from polycodebench_orchestration.solve.worker_runtime import SolveWorkerResourceSpec

    digest = _digest("1")
    reference = f"pcb-python-runtime@{digest}"
    (tmp_path / "python-v1.json").write_text(
        json.dumps({"images": {"runtime": {"digest": digest, "reference": reference}}}), "utf-8"
    )
    assert lc.runtime_image_reference(digest, tmp_path) == reference
    with pytest.raises(lc.StepError):
        lc.runtime_image_reference(_digest("2"), tmp_path)
    template = json.loads(lc.DEFAULT_SOLVE_RESOURCE_TEMPLATE.read_text(encoding="utf-8"))
    spec = lc.solve_resource_spec(
        template, resource_class="local-development-small", image=reference, image_digest=digest
    )
    parsed = SolveWorkerResourceSpec.model_validate_json(json.dumps(spec), strict=True)
    assert parsed.resource_class == "local-development-small"


def test_local_register_honours_an_explicit_image_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Library change: ``pcb-worker local-register --image-allowlist``."""
    from polycodebench_orchestration import worker_cli

    digest = _digest("1")
    image = f"pcb-python-runtime@{digest}"
    template = json.loads(lc.DEFAULT_SOLVE_RESOURCE_TEMPLATE.read_text(encoding="utf-8"))
    resource = tmp_path / "resource.json"
    resource.write_text(
        json.dumps(
            lc.solve_resource_spec(
                template, resource_class="local-development-small", image=image, image_digest=digest
            )
        ),
        "utf-8",
    )
    allowed = tmp_path / "allowed.json"
    allowed.write_text(json.dumps({image: digest}), "utf-8")
    other = tmp_path / "other.json"
    other.write_text(json.dumps({f"other@{_digest('2')}": _digest("2")}), "utf-8")
    args = worker_cli._parser().parse_args(
        ["local-register", "--resource-spec", str(resource), "--image-allowlist", str(allowed)]
    )
    assert args.image_allowlist == allowed
    assert (
        worker_cli._parser().parse_args(["local-register"]).image_allowlist
        == worker_cli.DEFAULT_IMAGE_ALLOWLIST
    )
    monkeypatch.setenv("PCB_ENVIRONMENT", "dev")
    monkeypatch.setenv("PCB_LOCAL_WORKER_SETUP_ENABLED", "true")
    monkeypatch.delenv("PCB_MIGRATION_DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="pinned local allowlist"):
        worker_cli._register_local(resource, slots=1, workload_identity="w", image_allowlist=other)
    # The default allowlist does not pin the task runtime image either.
    with pytest.raises(ValueError, match="pinned local allowlist"):
        worker_cli._register_local(resource, slots=1, workload_identity="w")
    # The explicit allowlist passes the image gate and stops at the next precondition.
    with pytest.raises(ValueError, match="PCB_MIGRATION_DATABASE_URL"):
        worker_cli._register_local(
            resource, slots=1, workload_identity="w", image_allowlist=allowed
        )


# ----------------------------------------------------------------------------- task admission


def test_task_set_split_follows_the_members_admission_tier() -> None:
    assert lc.task_set_split(["local_fixture", "local_fixture"]) == "fixture"
    assert lc.task_set_split(["development_sandbox"]) == "public_development"
    with pytest.raises(lc.StepError):
        lc.task_set_split(["local_fixture", "development_sandbox"])
    with pytest.raises(lc.StepError):
        lc.task_set_split(["production_worker"])


def test_suite_admission_report_is_reused_only_for_the_exact_passing_snapshot(
    tmp_path: Path,
) -> None:
    package = tmp_path / "packs" / "rate-limiter"
    reports = tmp_path / "reports"
    reports.mkdir()
    target = tmp_path / "state" / "admission" / "rate-limiter.json"
    body = {
        "kind": "suite_admission_report",
        "package_digest": _digest("a"),
        "executable_admission_passed": True,
    }
    assert not lc.suite_admission_report(package, _digest("a"), reports, target)
    (reports / "rate-limiter.json").write_text(json.dumps(body), encoding="utf-8")
    assert not lc.suite_admission_report(package, _digest("b"), reports, target)
    assert not target.exists()
    assert lc.suite_admission_report(package, _digest("a"), reports, target)
    assert json.loads(target.read_text(encoding="utf-8")) == body
    target.unlink()
    failed = {**body, "executable_admission_passed": False}
    (reports / "rate-limiter.json").write_text(json.dumps(failed), encoding="utf-8")
    assert not lc.suite_admission_report(package, _digest("a"), reports, target)
