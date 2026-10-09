"""Prompt 33 deployment identity, environment manifests, keyring, sweep and rehearsal logic."""

from __future__ import annotations

import ast
import base64
import copy
import json
import re
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
    text = text.replace("REQUIRED-guest-instance-type", "m7i.large")
    text = text.replace("lt-REQUIRED", "lt-0abc").replace("subnet-REQUIRED", "subnet-0abc")
    text = text.replace("sg-REQUIRED", "sg-0abc")
    document = json.loads(text)
    document["sandbox"]["lane_subnets"] = {
        "solve": "subnet-0a01",
        "grading": "subnet-0a02",
        "admission": "subnet-0a03",
    }
    document["sandbox"]["lane_security_groups"] = {
        "solve": "sg-0a01",
        "grading": "sg-0a02",
        "admission": "sg-0a03",
    }
    document["sandbox"]["control_security_group_id"] = "sg-0a04"
    document["sandbox"]["launch_templates"] = {
        "solve": "lt-0a01",
        "grading": "lt-0a02",
        "admission": "lt-0a03",
        "performance": "lt-0a04",
    }
    document["sandbox"]["launch_template_versions"] = {
        "solve": "4",
        "grading": "7",
        "admission": "3",
        "performance": "2",
    }
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


def test_staging_budget_example_requires_owner_supplied_threshold() -> None:
    example = (ROOT / "infra/terraform/environments/staging/terraform.tfvars.example").read_text(
        "utf-8"
    )
    assignment = next(
        line.split("#", 1)[0].strip()
        for line in example.splitlines()
        if line.strip().startswith("monthly_budget_usd")
    )
    assert assignment.split("=", 1)[1].strip() == "0"
    variable = (ROOT / "infra/terraform/modules/telemetry/variables.tf").read_text("utf-8")
    assert "condition     = var.monthly_budget_usd > 0" in variable


def test_production_example_keeps_unverified_services_and_schedules_disabled() -> None:
    example = (ROOT / "infra/terraform/environments/production/terraform.tfvars.example").read_text(
        "utf-8"
    )
    services, schedules = example.split("schedules =", maxsplit=1)
    counts = re.findall(r"^\s*desired_count\s*=\s*(-?\d+)\s*$", services, re.MULTILINE)
    assert len(counts) >= 8 and set(counts) == {"0"}
    assert schedules.strip() == "{}"

    control_services = (ROOT / "infra/terraform/modules/control_services/variables.tf").read_text(
        "utf-8"
    )
    assert "svc.desired_count >= 0" in control_services
    assert "svc.desired_count == floor(svc.desired_count)" in control_services
    assert "svc.desired_count == 0 ||" in control_services
    assert "enabled services require resolved image digests." in control_services


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
    verified = identity.verified_environment(ok)
    assert verified["PCB_SERVICE_IDENTITY"] == ok.principal == _caller(role="publisher").arn
    assert verified["PCB_VERIFIED_ISOLATION_TIER"] == "production"

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
    assert {"bucket", "signing", "service", "cloud", "VPC", "sandbox"} <= kinds


def test_deployed_aws_manifest_requires_positive_versions_for_all_lane_templates() -> None:
    document = _deployed_staging().model_dump(mode="json")
    document["sandbox"]["launch_template_versions"].pop("solve")
    with pytest.raises(ValueError, match="pinned version for every lane template"):
        EnvironmentManifest.model_validate(document)

    document["sandbox"]["launch_template_versions"]["solve"] = "0"
    with pytest.raises(ValueError, match="versions must be positive integers"):
        EnvironmentManifest.model_validate(document)


def test_deployed_aws_manifest_requires_distinct_guest_network_boundaries() -> None:
    document = _deployed_staging().model_dump(mode="json")
    document["sandbox"]["lane_security_groups"].pop("solve")
    with pytest.raises(ValueError, match="security group for every lane"):
        EnvironmentManifest.model_validate(document)

    document = _deployed_staging().model_dump(mode="json")
    document["sandbox"]["lane_security_groups"]["solve"] = document["sandbox"][
        "control_security_group_id"
    ]
    with pytest.raises(ValueError, match="require distinct security groups"):
        EnvironmentManifest.model_validate(document)

    document = _deployed_staging().model_dump(mode="json")
    document["sandbox"]["lane_subnets"]["grading"] = document["sandbox"]["lane_subnets"]["solve"]
    with pytest.raises(ValueError, match="distinct private subnets"):
        EnvironmentManifest.model_validate(document)

    document = _deployed_staging().model_dump(mode="json")
    document["sandbox"]["launch_templates"]["grading"] = document["sandbox"]["launch_templates"][
        "solve"
    ]
    with pytest.raises(ValueError, match="distinct launch templates"):
        EnvironmentManifest.model_validate(document)

    document = _deployed_staging().model_dump(mode="json")
    document["sandbox"]["control_identity_secret_refs"]["solve-supervisor"] = (
        "aws-sm:pcb/production/sandbox/control-identity-solve-supervisor"
    )
    with pytest.raises(ValueError, match="must be environment-scoped"):
        EnvironmentManifest.model_validate(document)

    document = _deployed_staging().model_dump(mode="json")
    document["sandbox"]["control_identity_secret_refs"].pop("solve-supervisor")
    with pytest.raises(ValueError, match="distinct guest-control identity"):
        EnvironmentManifest.model_validate(document)


def test_ranked_releases_refuse_development_tier_results() -> None:
    refuse_inadmissible_tiers("exploratory", ["development", "production"])
    refuse_inadmissible_tiers("ranked", ["production"])
    with pytest.raises(DeploymentRefused):
        refuse_inadmissible_tiers("ranked", ["production", "development"])


def test_reconcile_reports_drift_against_terraform_output() -> None:
    manifest = _deployed_staging()

    def secret_arn(reference: str, *, account: str = ACCOUNT, region: str = "eu-west-1") -> str:
        name = reference.removeprefix("aws-sm:")
        return f"arn:aws:secretsmanager:{region}:{account}:secret:{name}-AbCdEf"

    deployed = {
        "environment": "staging",
        "account_id": ACCOUNT,
        "region": manifest.identity.region,
        "service_role_arns": dict(manifest.identity.service_roles),
        "operator_role_arns": dict(manifest.identity.operator_roles),
        "bucket_names": {
            "hidden": manifest.object_store.bucket_hidden,
            "internal": manifest.object_store.bucket_internal,
            "public": manifest.object_store.bucket_public,
        },
        "object_store_endpoint": manifest.object_store.endpoint,
        "database_secret_arns": {
            role: secret_arn(reference)
            for role, reference in manifest.secrets.database_dsn_refs.items()
        },
        "signing_secret_arns": {
            key: secret_arn(reference)
            for key, reference in manifest.secrets.signing_key_refs.items()
        },
        "cursor_secret_arn": secret_arn(str(manifest.secrets.cursor_key_ref)),
        "secret_namespaces": {
            "model": manifest.secrets.model_namespace.removeprefix("aws-sm:"),
            "judge": manifest.secrets.judge_namespace.removeprefix("aws-sm:"),
        },
        "vpc_cidr": manifest.network.vpc_cidr,
        "approved_guest_ami_id": manifest.sandbox.approved_vm_image,
        "guest_instance_type": manifest.sandbox.guest_instance_type,
        "launch_template_ids": dict(manifest.sandbox.launch_templates),
        "launch_template_versions": {
            lane: int(version)
            for lane, version in manifest.sandbox.launch_template_versions.items()
        },
        "lane_subnet_ids": dict(manifest.sandbox.lane_subnets),
        "guest_security_group_ids": dict(manifest.sandbox.lane_security_groups),
        "control_security_group_id": manifest.sandbox.control_security_group_id,
        "sandbox_control_identity_secret_arns": {
            role: secret_arn(reference)
            for role, reference in manifest.sandbox.control_identity_secret_refs.items()
        },
        "hardware_class": manifest.capacity.performance_hardware_class,
    }
    assert environments.reconcile(manifest, {"deployment": {"value": deployed}}) == []
    deployed["service_role_arns"]["rogue"] = f"arn:aws:iam::{ACCOUNT}:role/rogue"
    deployed["operator_role_arns"]["release-approver"] = f"arn:aws:iam::{ACCOUNT}:role/rogue"
    deployed["bucket_names"]["hidden"] = "someone-elses-bucket"
    deployed["object_store_endpoint"] = "https://s3.us-east-2.amazonaws.com"
    deployed["database_secret_arns"]["api"] = secret_arn(
        manifest.secrets.database_dsn_refs["api"], account="999999999999"
    )
    signing_key_id = next(iter(manifest.secrets.signing_key_refs))
    deployed["signing_secret_arns"][signing_key_id] = secret_arn(
        "aws-sm:pcb/production/signing/rotated"
    )
    deployed["cursor_secret_arn"] = secret_arn("aws-sm:pcb/production/api/cursor-signing-key")
    deployed["secret_namespaces"]["model"] = "pcb/production/model/"
    deployed["vpc_cidr"] = "10.60.0.0/16"
    deployed["launch_template_versions"]["solve"] = 999
    deployed["lane_subnet_ids"]["solve"] = "subnet-0fff"
    deployed["guest_security_group_ids"]["grading"] = "sg-0fff"
    deployed["control_security_group_id"] = "sg-0ffe"
    deployed["sandbox_control_identity_secret_arns"]["solve-supervisor"] = secret_arn(
        "aws-sm:pcb/production/sandbox/control-identity-solve-supervisor"
    )
    deployed["approved_guest_ami_id"] = "ami-0fff"
    deployed["guest_instance_type"] = "m7i.xlarge"
    deployed["region"] = "us-east-2"
    differences = environments.reconcile(manifest, deployed)
    assert any("rogue" in item for item in differences)
    assert any("operator role release-approver" in item for item in differences)
    assert any("bucket hidden" in item for item in differences)
    assert any("object-store endpoint" in item for item in differences)
    assert any(
        "database secret api" in item and "outside the manifest AWS account/region" in item
        for item in differences
    )
    assert any("signing key" in item for item in differences)
    assert any("cursor signing key" in item for item in differences)
    assert any("model secret namespace" in item for item in differences)
    assert any("VPC CIDR" in item for item in differences)
    assert any("launch template version solve" in item for item in differences)
    assert any("guest subnet solve" in item for item in differences)
    assert any("guest security group grading" in item for item in differences)
    assert any("control security group" in item for item in differences)
    assert any("sandbox control identity solve-supervisor" in item for item in differences)
    assert any("approved guest AMI" in item for item in differences)
    assert any("guest instance type" in item for item in differences)
    assert any("region" in item for item in differences)


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
        "    op.execute('ALTER TABLE repair_run DROP CONSTRAINT fk_repair_run_attempt')\n"
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
    assert len(report.violations) == 4
    approved = {**policy, "approved_contract_migrations": {"c3": {"retention_plan": "RP-1"}}}
    assert migrations.check_expand_only(approved, tmp_path).violations == []


def _safe_fk_replacement_rule(**overrides: object) -> dict[str, object]:
    return {
        "constraint": "fk_child_parent",
        "table": "child",
        "referenced_table": "parent",
        "local_columns": ["parent_id"],
        "referenced_columns": ["id"],
        "previous_ondelete": "NO ACTION",
        "replacement_ondelete": "RESTRICT",
        "rationale": "Preserves immediate delete protection without changing data.",
        **overrides,
    }


def test_expand_only_check_allows_only_exact_safe_fk_action_replacement(tmp_path: Path) -> None:
    _write_revision(tmp_path, "a1", None, "    op.create_table('parent')")
    _write_revision(
        tmp_path,
        "b2",
        "a1",
        "    op.drop_constraint('fk_child_parent', 'child', type_='foreignkey')\n"
        "    op.create_foreign_key('fk_child_parent', 'child', 'parent', "
        "['parent_id'], ['id'], ondelete='RESTRICT')",
    )
    policy = {
        "schema_version": 1,
        "released_revision": "a1",
        "approved_contract_migrations": {},
        "safe_fk_action_replacements": {"b2": [_safe_fk_replacement_rule()]},
    }

    report = migrations.check_expand_only(policy, tmp_path)

    assert report.violations == []


@pytest.mark.parametrize(
    "create_call",
    [
        "op.create_foreign_key('fk_child_parent', 'child', 'parent', "
        "['parent_id'], ['id'], ondelete='CASCADE')",
        "op.create_foreign_key('fk_child_parent', 'child', 'parent', "
        "['other_id'], ['id'], ondelete='RESTRICT')",
    ],
)
def test_expand_only_check_rejects_weakened_or_mismatched_fk_replacement(
    tmp_path: Path, create_call: str
) -> None:
    _write_revision(tmp_path, "a1", None, "    op.create_table('parent')")
    _write_revision(
        tmp_path,
        "b2",
        "a1",
        "    op.drop_constraint('fk_child_parent', 'child', type_='foreignkey')\n"
        f"    {create_call}",
    )
    policy = {
        "schema_version": 1,
        "released_revision": "a1",
        "approved_contract_migrations": {},
        "safe_fk_action_replacements": {"b2": [_safe_fk_replacement_rule()]},
    }

    report = migrations.check_expand_only(policy, tmp_path)

    assert any("drops a constraint" in item for item in report.violations)
    assert any("not an exact pair" in item for item in report.violations)


def test_safe_fk_replacement_does_not_exempt_another_drop_on_the_same_line(
    tmp_path: Path,
) -> None:
    _write_revision(tmp_path, "a1", None, "    op.create_table('parent')")
    _write_revision(
        tmp_path,
        "b2",
        "a1",
        "    op.drop_constraint('fk_child_parent', 'child', type_='foreignkey'); "
        "op.drop_constraint('unlisted', 'scorecard', type_='check')\n"
        "    op.create_foreign_key('fk_child_parent', 'child', 'parent', "
        "['parent_id'], ['id'], ondelete='RESTRICT')",
    )
    policy = {
        "schema_version": 1,
        "released_revision": "a1",
        "approved_contract_migrations": {},
        "safe_fk_action_replacements": {"b2": [_safe_fk_replacement_rule()]},
        "provenance_tables": ["scorecard"],
    }

    report = migrations.check_expand_only(policy, tmp_path)

    assert len(report.violations) == 1
    assert "drops a constraint" in report.violations[0]


def test_committed_migration_policy_covers_the_exact_repair_fk_replacements() -> None:
    policy = migrations.load_policy()
    report = migrations.check_expand_only(policy)

    replacements = policy["safe_fk_action_replacements"]["d8f971ea2b34"]
    assert len(replacements) == 3
    # Pin the repair migration's position, not the head: later revisions are expected to follow it.
    chain = migrations.linear_chain(migrations.revisions(migrations.VERSIONS))
    assert chain.index("b390a26f17cd") <= chain.index(report.head)
    assert report.violations == []

    released_source = migrations.VERSIONS / "e5f6a7b8c9d0_repair_runs_and_rounds.py"
    tree = ast.parse(released_source.read_text(encoding="utf-8"))
    tables = {
        ast.literal_eval(node.args[0]): node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_table"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }
    for rule in replacements:
        table = tables[rule["table"]]
        columns = [
            node
            for node in ast.walk(table)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "Column"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == rule["local_columns"][0]
        ]
        foreign_keys = [
            foreign_key
            for column in columns
            for foreign_key in ast.walk(column)
            if isinstance(foreign_key, ast.Call)
            and isinstance(foreign_key.func, ast.Attribute)
            and foreign_key.func.attr == "ForeignKey"
            and foreign_key.args
            and ast.literal_eval(foreign_key.args[0])
            == f"{rule['referenced_table']}.{rule['referenced_columns'][0]}"
        ]
        assert len(foreign_keys) == 1
        assert not {"ondelete", "deferrable", "initially"} & {
            keyword.arg for keyword in foreign_keys[0].keywords
        }


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
