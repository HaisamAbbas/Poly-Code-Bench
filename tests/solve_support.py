"""Shared harness for solve-session integration tests.

EVIDENCE LABEL: database, artifact store, gateway, ledger and (where noted) Docker sandbox are real.
The model is a deterministic FIXTURE: ``ScriptedTransport`` replays scripted provider responses.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from model_gateway_support import ScriptedTransport, ok
from polycodebench_core.models import ProtocolConstraints, TaskOutputContract
from polycodebench_core.solve_contracts import (
    TOOL_NAMES,
    resolve_effective_protocol,
)
from polycodebench_orchestration.gateway.store import ArtifactResponseStore
from polycodebench_orchestration.solve.session import AgentSession, SingleShotSession, SolveResult
from polycodebench_orchestration.solve.types import SolveAssignment, public_groups_from_workspace
from polycodebench_persistence.artifacts import ArtifactRepository
from polycodebench_persistence.solve_state import PostgresSolveRepository
from polycodebench_runner.contracts import SandboxHandle, SandboxSpec
from polycodebench_runner.guest_tools import GuestToolbox
from polycodebench_runner.provider import LocalDockerSandboxProvider
from polycodebench_services.solve_protocols import load_protocol_directory
from sqlalchemy.engine import Engine
from test_model_gateway_postgres import World

ROOT = Path(__file__).resolve().parents[1]
PROTOCOLS = load_protocol_directory(ROOT / "config/protocols")
HIDDEN_MARKER = b"HIDDEN-ORACLE-7c1e4f"
IMAGE = (
    "docker.io/library/python:3.12-slim@sha256:"
    "44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b"
)
DIGEST = "sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b"

SOLUTION = "n = int(input())\nprint(n * 2)\n"
CREATE_SOLUTION = (
    "--- /dev/null\n+++ b/solution.py\n@@ -0,0 +1,2 @@\n+n = int(input())\n+print(n * 2)\n"
)
VISIBLE: dict[str, bytes] = {
    "task.md": b"# Double it\nWrite solution.py: read an integer from stdin, print twice that.\n",
    "repo/README.md": b"public readme\n",
    "public-tests.json": json.dumps(
        {
            "groups": [
                {
                    "id": "basic",
                    "argv": ["python", "-I", "-B", "-S", "test_public.py"],
                    "cwd": ".",
                    "timeout_seconds": 20,
                }
            ]
        }
    ).encode(),
    "test_public.py": (
        b"import subprocess, sys\n"
        b"out = subprocess.run([sys.executable, '-I', '-B', '-S', 'solution.py'],\n"
        b"                     input=b'21\\n', capture_output=True).stdout.strip()\n"
        b"sys.exit(0 if out == b'42' else 1)\n"
    ),
}
ONE_FILE = TaskOutputContract.model_validate(
    {
        "schema_version": 1,
        "kind": "task_output_contract",
        "submission_kind": "files",
        "allowed_paths": ["solution.py"],
        "maximum_artifact_bytes": 100_000,
        "maximum_file_bytes": 50_000,
        "maximum_files": 1,
        "findings_limit": None,
    },
    strict=False,
)


def constraints(protocol_id: str, **overrides: Any) -> ProtocolConstraints:
    values: dict[str, Any] = {
        "schema_version": 1,
        "kind": "protocol_constraints",
        "protocol_id": protocol_id,
        "allowed_tools": list(TOOL_NAMES),
        "public_test_feedback": True,
        "hidden_feedback": False,
        "network_policy": "disabled",
        "dependency_inventory_digest": None,
        "maximum_model_turns": 30,
        "maximum_tool_calls": 100,
        "maximum_wall_seconds": 600,
    }
    values.update(overrides)
    return ProtocolConstraints(**values)


def tool_call(
    call_id: str, name: str, arguments: Any = None, *, raw: str | None = None
) -> dict[str, Any]:
    text = raw if raw is not None else json.dumps(arguments if arguments is not None else {})
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": text}}


def reply(*calls: dict[str, Any], text: str = "", request_id: str = "req") -> Any:
    return ok(text, tool_calls=list(calls) or None, request_id=request_id)


def final(text: str = "All done.") -> Any:
    return ok(text)


def envelope_files(content: str = SOLUTION) -> str:
    return json.dumps({"files": [{"path": "solution.py", "content": content}]})


def sandbox_spec() -> SandboxSpec:
    return SandboxSpec(
        stage_id=f"solve-{uuid4().hex[:8]}",
        fence=1,
        lane="solve",
        image=IMAGE,
        image_digest=DIGEST,
        cpu_millis=1000,
        memory_bytes=256 * 1024**2,
        disk_bytes=64 * 1024**2,
        pids_limit=128,
        timeout_seconds=300,
        ttl_seconds=900,
    )


def docker_provider() -> LocalDockerSandboxProvider:
    return LocalDockerSandboxProvider(
        allowed_images={IMAGE: DIGEST},
        state_dir=Path.cwd() / ".cache" / f"solve-sessions-{uuid4().hex}",
        provider_id="solve-sessions",
        operation_timeout_seconds=90,
    )


@dataclass
class Harness:
    world: World
    engine: Engine
    artifacts: ArtifactRepository

    @property
    def repo(self) -> PostgresSolveRepository:
        return PostgresSolveRepository(self.engine)

    def store(self) -> ArtifactResponseStore:
        return ArtifactResponseStore(
            self.artifacts, owner="solve-tests", encryption_domain="solve-session"
        )

    def assignment(
        self,
        index: int,
        protocol_id: str,
        *,
        visible: dict[str, bytes] | None = None,
        contract: TaskOutputContract = ONE_FILE,
        required: list[str] | None = None,
        protected: list[str] | None = None,
        **constraint_overrides: Any,
    ) -> SolveAssignment:
        files = dict(VISIBLE if visible is None else visible)
        effective = resolve_effective_protocol(
            PROTOCOLS[protocol_id], constraints(protocol_id, **constraint_overrides)
        )
        return SolveAssignment(
            attempt_id=self.world.attempts[index],
            scope=self.world.scope(index),
            effective=effective,
            config=self.world.config,
            config_document_id=self.world.config_id,
            sample_seed=2**64 - 1,
            instructions=files["task.md"].decode(),
            contract=contract,
            required_outputs=["solution.py"] if required is None else required,
            protected_paths=["test_public.py", "public-tests.json"]
            if protected is None
            else protected,
            visible_files=files,
            base_digest="sha256:" + "b" * 64,
            public_groups=public_groups_from_workspace(files),
        )

    def common(
        self,
        assignment: SolveAssignment,
        transport: ScriptedTransport,
        **extra: Any,
    ) -> dict[str, Any]:
        return {
            "assignment": assignment,
            "repository": self.repo,
            "store": self.store(),
            "gateway": self.world.gateway(transport),
            **extra,
        }

    async def single_shot(
        self, assignment: SolveAssignment, transport: ScriptedTransport, **extra: Any
    ) -> SolveResult:
        return await SingleShotSession(**self.common(assignment, transport, **extra)).run()

    async def agent(
        self,
        assignment: SolveAssignment,
        transport: ScriptedTransport,
        sandbox: LocalDockerSandboxProvider,
        handle: SandboxHandle,
        *,
        session_class: type[AgentSession] = AgentSession,
        **extra: Any,
    ) -> SolveResult:
        session = session_class(
            toolbox=GuestToolbox(sandbox, handle), **self.common(assignment, transport, **extra)
        )
        return await session.run()

    def events(self, attempt_id: UUID) -> list[tuple[int, str, dict[str, Any]]]:
        store = self.store()
        return [
            (e.event_seq, e.kind, json.loads(store.get(e.payload_artifact_id)))
            for e in self.repo.events(attempt_id)
        ]

    def results(self, attempt_id: UUID) -> list[dict[str, Any]]:
        return [p for _, kind, p in self.events(attempt_id) if kind == "tool_result"]


def request_bodies(transport: ScriptedTransport) -> list[dict[str, Any]]:
    return [json.loads(item["body"]) for item in transport.sent]
