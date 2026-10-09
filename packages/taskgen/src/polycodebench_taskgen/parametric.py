"""Parametric task instances with commit-reveal round seeds.

A reviewed template (a statement with named placeholders, plus a trusted reference solution kept
outside this package) can produce a fresh instance for each evaluation round. The only secret is
the round seed, so a held-out instance is a value nobody could have trained on before the round
opened, and an instance from a past round can be reproduced after reveal.

Protocol:

1. Before a round opens, the operator publishes ``RoundCommitment.commit(...)``, which is a digest
   over the round seed.
2. The round runs against instances derived from that seed.
3. After the round closes, the operator reveals the seed. Anyone can call ``verify`` against the
   published digest and re-derive every instance to check it was not chosen after the fact.

Small parameter spaces can be memorised or enumerated, so ``assert_minimum_space`` refuses a family
whose instance count is below the operator's floor.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import re
from collections.abc import Iterable
from string import Formatter

from pydantic import Field, model_validator

from polycodebench_taskgen.contracts import SLUG, TaskgenModel

_PARAMETER_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_MIN_SEED_BYTES = 32


class ParameterRange(TaskgenModel):
    name: str = Field(pattern=_PARAMETER_NAME.pattern)
    minimum: int
    maximum: int

    @model_validator(mode="after")
    def _ordered(self) -> ParameterRange:
        if self.minimum > self.maximum:
            raise ValueError("parameter minimum must not exceed maximum")
        return self

    @property
    def width(self) -> int:
        return self.maximum - self.minimum + 1


class ParametricInstance(TaskgenModel):
    round_id: str = Field(pattern=SLUG)
    family_id: str = Field(pattern=SLUG)
    index: int = Field(ge=0)
    parameters: tuple[tuple[str, int], ...]
    statement: str
    fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class RoundCommitment(TaskgenModel):
    round_id: str = Field(pattern=SLUG)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @classmethod
    def commit(cls, *, round_id: str, round_secret: bytes) -> RoundCommitment:
        return cls(round_id=round_id, digest=_round_digest(round_id, round_secret))

    def verify(self, round_secret: bytes) -> bool:
        return hmac.compare_digest(self.digest, _round_digest(self.round_id, round_secret))


def _round_digest(round_id: str, round_secret: bytes) -> str:
    if len(round_secret) < _MIN_SEED_BYTES:
        raise ValueError(f"round secret must be at least {_MIN_SEED_BYTES} bytes")
    payload = b"polycodebench-round-commit-v1\x00" + round_id.encode() + b"\x00" + round_secret
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def derive_instance_seed(
    round_secret: bytes, *, round_id: str, family_id: str, index: int
) -> bytes:
    """32 pseudo-random bytes for one instance. Changing any input changes the output."""
    if len(round_secret) < _MIN_SEED_BYTES:
        raise ValueError(f"round secret must be at least {_MIN_SEED_BYTES} bytes")
    message = f"instance-v1\x00{round_id}\x00{family_id}\x00{index}".encode()
    return hmac.new(round_secret, message, hashlib.sha256).digest()


class ParametricFamily(TaskgenModel):
    family_id: str = Field(pattern=SLUG)
    parameters: tuple[ParameterRange, ...] = Field(min_length=1, max_length=16)
    statement_template: str = Field(min_length=1, max_length=200_000)

    @model_validator(mode="after")
    def _template_uses_declared_parameters(self) -> ParametricFamily:
        declared = {parameter.name for parameter in self.parameters}
        if len(declared) != len(self.parameters):
            raise ValueError("parameter names must be unique")
        used = {
            field
            for _, field, _, _ in Formatter().parse(self.statement_template)
            if field is not None
        }
        if not used <= declared:
            raise ValueError(f"template uses undeclared parameters: {sorted(used - declared)}")
        return self

    def space_size(self) -> int:
        return math.prod(parameter.width for parameter in self.parameters)

    def assert_minimum_space(self, minimum: int) -> None:
        if self.space_size() < minimum:
            raise ValueError(
                f"parameter space {self.space_size()} is below the required minimum {minimum}"
            )

    def sample(self, *, round_secret: bytes, round_id: str, index: int) -> ParametricInstance:
        seed = derive_instance_seed(
            round_secret, round_id=round_id, family_id=self.family_id, index=index
        )
        values: list[tuple[str, int]] = []
        for parameter in self.parameters:
            draw = hmac.new(seed, parameter.name.encode(), hashlib.sha256).digest()
            offset = int.from_bytes(draw[:16], "big") % parameter.width
            values.append((parameter.name, parameter.minimum + offset))
        rendered = self.statement_template.format_map(dict(values))
        return ParametricInstance(
            round_id=round_id,
            family_id=self.family_id,
            index=index,
            parameters=tuple(values),
            statement=rendered,
            fingerprint=_fingerprint(values),
        )

    def sample_many(
        self, *, round_secret: bytes, round_id: str, count: int
    ) -> tuple[ParametricInstance, ...]:
        """Draw ``count`` instances and refuse the round if two parameter sets collide."""
        instances = tuple(
            self.sample(round_secret=round_secret, round_id=round_id, index=index)
            for index in range(count)
        )
        if len({item.fingerprint for item in instances}) != len(instances):
            raise ValueError("instance parameters collided within the round")
        return instances


def _fingerprint(values: Iterable[tuple[str, int]]) -> str:
    canonical = ";".join(f"{name}={value}" for name, value in sorted(values))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
