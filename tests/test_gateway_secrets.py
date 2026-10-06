from __future__ import annotations

import pytest
from polycodebench_core.model_contracts import EndpointPolicyViolation
from polycodebench_orchestration.gateway.secrets import (
    EnvironmentSecretResolver,
    SecretsManagerSecretResolver,
    configured_secret_resolver,
)


class FakeSecretsManager:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values
        self.requests: list[str] = []

    def get_secret_value(self, *, SecretId: str) -> dict[str, str]:
        self.requests.append(SecretId)
        try:
            return {"SecretString": self.values[SecretId]}
        except KeyError:
            raise LookupError("secret missing") from None


def test_secrets_manager_resolver_uses_only_its_configured_namespace() -> None:
    client = FakeSecretsManager({"pcb/staging/model/openai-main": "provider-token-123"})
    resolver = SecretsManagerSecretResolver("models", "pcb/staging/model/", client=client)

    value = resolver.resolve("secret://models/openai-main")
    assert value is not None
    assert value.reveal() == "provider-token-123"
    assert repr(value) == "Secret(****)"
    assert client.requests == ["pcb/staging/model/openai-main"]

    with pytest.raises(EndpointPolicyViolation, match="outside the gateway namespace"):
        resolver.resolve("secret://judges/judge-main")
    assert client.requests == ["pcb/staging/model/openai-main"]


def test_secrets_manager_resolver_fails_closed_without_exposing_errors() -> None:
    client = FakeSecretsManager({})
    resolver = SecretsManagerSecretResolver("models", "pcb/staging/model/", client=client)

    with pytest.raises(EndpointPolicyViolation, match="not provisioned") as error:
        resolver.resolve("secret://models/missing")
    assert "secret missing" not in str(error.value)
    assert resolver.resolve("none") is None


def test_secret_resolver_selection_is_bound_to_verified_service_role() -> None:
    model_client = FakeSecretsManager({"pcb/staging/model/model-key": "model-token-123"})
    model = configured_secret_resolver(
        "models",
        environ={
            "PCB_ENVIRONMENT": "staging",
            "PCB_VERIFIED_ENVIRONMENT": "staging",
            "PCB_VERIFIED_ROLE": "solve-supervisor",
            "PCB_VERIFIED_BY": "aws-sts",
        },
        secretsmanager_client=model_client,
    )
    assert model.resolve("secret://models/model-key").reveal() == "model-token-123"  # type: ignore[union-attr]

    judge_client = FakeSecretsManager({"pcb/production/judge/judge-key": "judge-token-123"})
    judge = configured_secret_resolver(
        "models",
        environ={
            "PCB_ENVIRONMENT": "production",
            "PCB_VERIFIED_ENVIRONMENT": "production",
            "PCB_VERIFIED_ROLE": "judge-gateway",
            "PCB_VERIFIED_BY": "aws-sts",
        },
        secretsmanager_client=judge_client,
    )
    assert judge.resolve("secret://models/judge-key").reveal() == "judge-token-123"  # type: ignore[union-attr]

    local = configured_secret_resolver(
        "models",
        environ={"PCB_ENVIRONMENT": "dev", "PCBSECRET__MODELS__LOCAL_KEY": "local-token-123"},
    )
    assert isinstance(local, EnvironmentSecretResolver)
    assert local.resolve("secret://models/local-key").reveal() == "local-token-123"  # type: ignore[union-attr]

    with pytest.raises(EndpointPolicyViolation, match="verified gateway role"):
        configured_secret_resolver(
            "models",
            environ={
                "PCB_ENVIRONMENT": "staging",
                "PCB_VERIFIED_ENVIRONMENT": "staging",
                "PCB_VERIFIED_ROLE": "api",
                "PCB_VERIFIED_BY": "aws-sts",
            },
            secretsmanager_client=FakeSecretsManager({}),
        )
    with pytest.raises(EndpointPolicyViolation, match="verified gateway role"):
        configured_secret_resolver(
            "models",
            environ={
                "PCB_ENVIRONMENT": "staging",
                "PCB_VERIFIED_ENVIRONMENT": "staging",
                "PCB_VERIFIED_ROLE": "solve-supervisor",
            },
            secretsmanager_client=FakeSecretsManager({}),
        )
    with pytest.raises(EndpointPolicyViolation, match="verified service environment"):
        configured_secret_resolver(
            "models",
            environ={
                "PCB_ENVIRONMENT": "staging",
                "PCB_VERIFIED_ENVIRONMENT": "production",
                "PCB_VERIFIED_ROLE": "solve-supervisor",
                "PCB_VERIFIED_BY": "aws-sts",
            },
            secretsmanager_client=FakeSecretsManager({}),
        )


@pytest.mark.parametrize(
    ("namespace", "prefix"),
    [
        ("models", "pcb/production/judge/"),
        ("judges", "pcb/staging/model/"),
        ("models", "pcb/staging/model/../judge/"),
        ("models", "arn:aws:secretsmanager:us-east-1:123456789012:secret:pcb/staging/model/"),
    ],
)
def test_secrets_manager_resolver_rejects_unscoped_prefixes(namespace: str, prefix: str) -> None:
    with pytest.raises(ValueError):
        SecretsManagerSecretResolver(namespace, prefix, client=FakeSecretsManager({}))
