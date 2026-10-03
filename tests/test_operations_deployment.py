"""Prompt 33 deployment identity, environment manifests, keyring, sweep and rehearsal logic."""

from __future__ import annotations

import base64
import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from polycodebench_core.deployment import (
    CallerPrincipal,
    DeploymentRefused,
    EnvironmentManifest,
    refuse_inadmissible_tiers,
    resolve_deployment,
    separation_violations,
)
from polycodebench_operations import environments, identity, migrations, orphans
from polycodebench_operations.cli import check_alert_rules
from polycodebench_operations.cli import main as ops_main
from polycodebench_operations.rehearsal_data import (
    ScorecardRow,
    rehearsal_projection,
    select_stratified,
)
from polycodebench_publication.keyring import Keyring, KeyringError
from polycodebench_publication.releases import SigningKey, canonical_bytes

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = "123456789012"


def _deployed_staging() -> EnvironmentManifest:
    document = yaml.safe_load((ROOT / "config/environments/staging.yaml").read_text("utf-8"))
    text = json.dumps(document)
    text = text.replace("REQUIRED-account", ACCOUNT).replace("REQUIRED-region", "eu-west-1")
    text = text.replace("REQUIRED-prefix", "pcbtest").replace("ami-REQUIRED", "ami-0abc")
    text = text.replace("lt-REQUIRED", "lt-0abc").replace("subnet-REQUIRED", "subnet-0abc")
    document = json.loads(text)
    document["status"] = "deployed"
    return EnvironmentManifest.model_validate(document)


def _caller(
    role: str = "scheduler", env: str = "staging", account: str = ACCOUNT
) -> CallerPrincipal:
    return CallerPrincipal(
        account_id=account,
        arn=f"arn:aws:sts::{account}:assumed-role/pcb-{env}-{role}/ecs-task-1",
        verified_by="aws-sts",
    )


def test_committed_manifests_validate_and_are_separated() -> None:
    reports, violations = environments.validate_all()
    assert violations == []
    by_env = {report.environment: report for report in reports}
    assert by_env["dev"].deployable and by_env["integration"].deployable
    # Staging/production are honest templates until the owner supplies deployment inputs.
    assert not by_env["staging"].deployable and by_env["staging"].unresolved_inputs
    assert not by_env["production"].deployable


def test_verified_principal_not_a_string_decides_the_environment() -> None:
    manifest = _deployed_staging()
    deployment = resolve_deployment(
        manifest, claimed_environment="staging", claimed_role="scheduler", caller=_caller()
    )
    assert deployment.environment == "staging" and deployment.isolation_tier == "production"
    assert deployment.ranked_release_allowed is False
    with pytest.raises(DeploymentRefused, match="does not match"):
        resolve_deployment(
            manifest, claimed_environment="production", claimed_role="scheduler", caller=_caller()
        )
    with pytest.raises(DeploymentRefused, match="requires a verified cloud principal"):
        resolve_deployment(
            manifest, claimed_environment="staging", claimed_role="scheduler", caller=None
        )
    with pytest.raises(DeploymentRefused, match="account"):
        resolve_deployment(
            manifest,
            claimed_environment="staging",
            claimed_role="scheduler",
            caller=_caller(account="999999999999"),
        )
    # A production task role presented to the staging manifest is refused, and a role cannot
    # borrow another role's identity even within the environment.
    with pytest.raises(DeploymentRefused, match="is not the staging"):
        resolve_deployment(
            manifest,
            claimed_environment="staging",
            claimed_role="scheduler",
            caller=_caller(env="production"),
        )
    with pytest.raises(DeploymentRefused, match="is not the staging publisher"):
        resolve_deployment(
            manifest,
            claimed_environment="staging",
            claimed_role="publisher",
            caller=_caller(role="scheduler"),
        )


def test_templates_and_development_manifests_cannot_grant_trust() -> None:
    template = environments.load_manifest(environments.manifest_path("staging"))
    with pytest.raises(DeploymentRefused, match="template"):
        resolve_deployment(
            template, claimed_environment="staging", claimed_role="scheduler", caller=_caller()
        )
    dev = environments.load_manifest(environments.manifest_path("dev"))
    local = resolve_deployment(dev, claimed_environment="dev", claimed_role="api", caller=None)
    assert local.isolation_tier == "development" and not local.ranked_release_allowed
    with pytest.raises(DeploymentRefused, match="development-only"):
        resolve_deployment(dev, claimed_environment="dev", claimed_role="api", caller=_caller())


def test_identity_verify_uses_the_trusted_source_and_fails_closed() -> None:
    manifest = _deployed_staging()
    ok = identity.verify(
        manifest,
        claimed_environment="staging",
        claimed_role="publisher",
        caller_source=lambda: _caller(role="publisher"),
    )
    assert identity.verified_environment(ok)["PCB_VERIFIED_ISOLATION_TIER"] == "production"

    def unavailable() -> CallerPrincipal:
        raise ConnectionError("sts unreachable")

    with pytest.raises(DeploymentRefused, match="could not verify"):
        identity.verify(
            manifest,
            claimed_environment="staging",
            claimed_role="publisher",
            caller_source=unavailable,
        )


def test_cli_identity_verify_refuses_template_with_exit_4(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("PCB_ENVIRONMENT", "staging")
    monkeypatch.setenv("PCB_ROLE", "scheduler")
    monkeypatch.delenv("PCB_ENV_MANIFEST", raising=False)
    monkeypatch.setattr(identity, "sts_caller", lambda **_: _caller())
    assert ops_main(["identity", "verify"]) == 4
    assert "template" in capsys.readouterr().err


def test_manifest_rules_reject_unsafe_environment_policy() -> None:
    base = _deployed_staging().model_dump(mode="json")
    for path, value, message in (
        (("policy", "fake_transports_allowed"), True, "fake model transports"),
        (("policy", "ranked_release_allowed"), True, "only production"),
        (("sandbox", "provider"), "local_docker", "disposable VM"),
        (("isolation_tier",), "development", "isolation_tier"),
    ):
        document = copy.deepcopy(base)
        target = document
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        with pytest.raises(ValueError, match=message):
            EnvironmentManifest.model_validate(document)


def test_shared_resources_across_environments_are_violations() -> None:
    staging = _deployed_staging()
    production_doc = staging.model_dump(mode="json")
    production_doc.update(environment="production", status="template")
    production_doc["policy"]["ranked_release_allowed"] = True
    production = EnvironmentManifest.model_validate(production_doc)
    violations = separation_violations([staging, production])
    kinds = {violation.split(" ")[0] for violation in violations}
    assert {"bucket", "signing", "service", "cloud", "VPC"} <= kinds


def test_ranked_releases_refuse_development_tier_results() -> None:
    refuse_inadmissible_tiers("exploratory", ["development", "production"])
    refuse_inadmissible_tiers("ranked", ["production"])
    with pytest.raises(DeploymentRefused):
        refuse_inadmissible_tiers("ranked", ["production", "development"])


def test_reconcile_reports_drift_against_terraform_output() -> None:
    manifest = _deployed_staging()
    deployed = {
        "environment": "staging",
        "account_id": ACCOUNT,
        "service_role_arns": dict(manifest.identity.service_roles),
        "bucket_names": {
            "hidden": manifest.object_store.bucket_hidden,
            "internal": manifest.object_store.bucket_internal,
            "public": manifest.object_store.bucket_public,
        },
        "signing_secret_arns": {key: "arn" for key in manifest.secrets.signing_key_refs},
        "launch_template_ids": dict(manifest.sandbox.launch_templates),
        "hardware_class": manifest.capacity.performance_hardware_class,
    }
    assert environments.reconcile(manifest, {"deployment": {"value": deployed}}) == []
    deployed["service_role_arns"]["rogue"] = f"arn:aws:iam::{ACCOUNT}:role/rogue"
    deployed["bucket_names"]["hidden"] = "someone-elses-bucket"
    differences = environments.reconcile(manifest, deployed)
    assert any("rogue" in item for item in differences)
    assert any("bucket hidden" in item for item in differences)


# ----------------------------------------------------------------------------- migrations


def _write_revision(directory: Path, revision: str, down: str | None, upgrade_body: str) -> None:
    (directory / f"{revision}_x.py").write_text(
        f'revision = "{revision}"\ndown_revision = {down!r}\n\n'
        f"def upgrade():\n{upgrade_body}\n\ndef downgrade():\n    raise NotImplementedError\n",
        encoding="utf-8",
    )


def test_expand_only_check_flags_destructive_changes(tmp_path: Path) -> None:
    _write_revision(tmp_path, "a1", None, "    op.create_table('t')")
    _write_revision(tmp_path, "b2", "a1", "    op.add_column('t', sa.Column('c'))")
    _write_revision(
        tmp_path,
        "c3",
        "b2",
        "    op.drop_column('scorecard', 'composite')\n"
        "    op.execute('TRUNCATE audit_event')\n"
        "    op.alter_column('t', 'c', nullable=False)",
    )
    policy = {
        "schema_version": 1,
        "released_revision": "a1",
        "approved_contract_migrations": {},
        "provenance_tables": ["scorecard"],
    }
    report = migrations.check_expand_only(policy, tmp_path)
    assert report.checked == ["b2", "c3"] and report.head == "c3"
    assert len(report.violations) == 3
    approved = {**policy, "approved_contract_migrations": {"c3": {"retention_plan": "RP-1"}}}
    assert migrations.check_expand_only(approved, tmp_path).violations == []


def test_branched_history_is_refused(tmp_path: Path) -> None:
    _write_revision(tmp_path, "a1", None, "    pass")
    _write_revision(tmp_path, "b2", "a1", "    pass")
    _write_revision(tmp_path, "c3", "a1", "    pass")
    with pytest.raises(ValueError, match="branches"):
        migrations.linear_chain(migrations.revisions(tmp_path))


def test_committed_policy_points_into_the_migration_chain() -> None:
    policy = migrations.load_policy()
    found = migrations.revisions()
    assert policy["released_revision"] in found and policy["previous_revision"] in found
    assert found[policy["released_revision"]].down_revision == policy["previous_revision"]


# ----------------------------------------------------------------------------- keyring


def _signed(key: SigningKey, body: dict[str, object]) -> dict[str, object]:
    manifest = {**body, "algorithm": "Ed25519", "key_id": key.key_id}
    manifest["signature"] = base64.b64encode(
        key.private_key.sign(canonical_bytes(manifest))
    ).decode("ascii")
    return manifest


def test_rotation_keeps_old_releases_verifiable_and_revocation_fails_closed() -> None:
    first = SigningKey("key-one", Ed25519PrivateKey.generate())
    second = SigningKey("key-two", Ed25519PrivateKey.generate())
    ring = Keyring().rotate(first, at="2026-10-01T00:00:00Z")
    old_release = _signed(first, {"release_id": "r1"})
    ring = ring.rotate(second, at="2026-10-02T00:00:00Z")
    new_release = _signed(second, {"release_id": "r2"})
    assert ring.verify(old_release) == (True, "retired")
    assert ring.verify(new_release) == (True, "active")
    restored = Keyring.from_document(json.loads(json.dumps(ring.document())))
    assert restored.verify(old_release) == (True, "retired")
    tampered = {**old_release, "release_id": "r1-edited"}
    assert ring.verify(tampered) == (False, "signature invalid")
    revoked = ring.revoke("key-one", at="2026-10-03T00:00:00Z", reason="compromise drill")
    assert revoked.verify(old_release) == (False, "signing key revoked")
    with pytest.raises(KeyringError):
        ring.rotate(first, at="2026-10-04T00:00:00Z")  # key IDs are never reused
    document = ring.document()
    document["keys"][0]["state"] = "active"
    with pytest.raises(KeyringError, match="digest"):
        Keyring.from_document(document)


# ----------------------------------------------------------------------------- orphans


class _FakeEc2:
    def __init__(self, instances: list[dict[str, object]]) -> None:
        self.instances = instances
        self.terminated: list[str] = []

    def get_paginator(self, name: str):  # type: ignore[no-untyped-def]
        assert name == "describe_instances"
        fake = self

        class _Paginator:
            def paginate(self, Filters):  # type: ignore[no-untyped-def]  # noqa: N803
                env = next(f["Values"][0] for f in Filters if f["Name"] == "tag:pcb:environment")
                live = [
                    i
                    for i in fake.instances
                    if i["InstanceId"] not in fake.terminated
                    and {"Key": "pcb:environment", "Value": env} in i["Tags"]
                ]
                return [{"Reservations": [{"Instances": live}]}]

        return _Paginator()

    def terminate_instances(self, InstanceIds):  # type: ignore[no-untyped-def]  # noqa: N803
        self.terminated.extend(InstanceIds)


def _instance(instance_id: str, lane: str, expires: str, env: str = "staging") -> dict[str, object]:
    return {
        "InstanceId": instance_id,
        "Tags": [
            {"Key": "pcb:owner", "Value": "polycodebench"},
            {"Key": "pcb:environment", "Value": env},
            {"Key": "pcb:lane", "Value": lane},
            {"Key": "pcb:expires", "Value": expires},
        ],
    }


def test_ec2_sweep_reclaims_every_expired_lane_but_not_live_or_foreign() -> None:
    from polycodebench_core.telemetry import MetricsRegistry

    now = 1_800_000_000
    ec2 = _FakeEc2(
        [
            _instance("i-live", "solve", str(now + 60)),
            _instance("i-expired-perf", "performance", str(now - 30)),
            _instance("i-no-ttl", "grading", "not-a-number"),
            _instance("i-other-env", "solve", str(now - 5000), env="production"),
        ]
    )
    registry = MetricsRegistry()
    report = orphans.sweep_ec2(ec2, environment="staging", registry=registry, clock=lambda: now)
    assert sorted(report.reclaimed) == ["i-expired-perf", "i-no-ttl"]
    assert "i-live" not in ec2.terminated and "i-other-env" not in ec2.terminated
    assert report.as_dict()["clean"]
    assert registry.value("pcb_orphan_guests_over_alert_threshold", provider="ec2_vm") == 0.0
    assert registry.value("pcb_orphans_reclaimed_total", provider="ec2_vm") == 2.0


def test_ec2_dry_run_reports_and_raises_the_alert_metric() -> None:
    from polycodebench_core.telemetry import MetricsRegistry

    now = 1_800_000_000
    ec2 = _FakeEc2([_instance("i-old", "solve", str(now - 3600))])
    registry = MetricsRegistry()
    report = orphans.sweep_ec2(
        ec2, environment="staging", registry=registry, clock=lambda: now, dry_run=True
    )
    assert ec2.terminated == [] and report.over_alert_threshold == 1
    assert registry.value("pcb_orphan_guests_over_alert_threshold", provider="ec2_vm") == 1.0


# ----------------------------------------------------------------------------- rehearsal


def _rows() -> list[ScorecardRow]:
    rows = []
    for stratum_index in range(11):
        for sample in range(2):
            rows.append(
                ScorecardRow(
                    f"{stratum_index:02d}-{sample}",
                    f"s{stratum_index:02d}",
                    "pass" if stratum_index % 3 else "fail",
                    None if stratum_index == 4 else Decimal("0.9"),
                )
            )
    return rows


def test_stratified_selection_is_deterministic_and_spreads_strata() -> None:
    rows = _rows()
    first = select_stratified(rows, count=10, seed="s")
    assert first == select_stratified(list(reversed(rows)), count=10, seed="s")
    assert len(first) == 10 and len({row.stratum for row in first}) == 10
    assert len(select_stratified(rows, count=15, seed="s")) == 15


def test_projection_is_order_independent_and_tracks_coverage() -> None:
    rows = _rows()
    cohort = {"rehearsal": "x"}
    projection = rehearsal_projection(rows, cohort)
    assert projection == rehearsal_projection(list(reversed(rows)), cohort)
    metrics = {item["metric_id"]: item for item in projection["metrics"]}
    assert metrics["rehearsal.mean_composite"]["coverage"] == "0.909091"
    assert projection["fixture_kind"] == "synthetic_internal"


def test_alert_rules_reference_catalogued_metrics_and_existing_runbooks() -> None:
    assert check_alert_rules() == []


def test_lifecycle_prefixes_cannot_be_used_as_evidence_domains() -> None:
    from polycodebench_persistence.object_store import S3ArtifactStore

    for reserved in ("provisional", "debug", "cancelled-logs"):
        with pytest.raises(ValueError, match="lifecycle"):
            S3ArtifactStore._validate_domain(reserved)
    S3ArtifactStore._validate_domain("ops-rehearsal")
