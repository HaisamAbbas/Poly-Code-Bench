"""Operator driver for one real, budget-capped live benchmark evaluation (development stack).

    uv run --locked --all-packages python scripts/live_campaign.py <step> [options]

Steps (each is resumable; IDs persist in ``<state-dir>/state.json``):

  tasks     import, admit, upload artifacts and freeze the selected task packages, then create
            and freeze one task set. Authored stdin/stdout fixtures are admitted with
            ``pcb task validate`` and go into a ``fixture`` set; language-plugin suite tasks
            (the pilots) reuse a matching ``--admission-reports`` report or run the plugin's
            executable admission, are replayed by ``pcb task freeze`` and go into a
            ``public_development`` set.
  endpoint  register the model's openai_compatible endpoint, run the static check and (with
            ``--allow-spend``) the live conformance probes, approve it with that report and
            register the resolved model configuration.
  run       plan cost/compatibility, open a capped campaign, store the run configuration and
            create the run through ``RunCreationService``; open attempt budget accounts.
  solve     register a local solve worker for the tasks' frozen runtime image and claim this
            run's solve jobs one at a time until none is pending (``--allow-spend`` required).
  grade     register the local grading worker, enqueue each completed attempt and run its job.
  score     score each completed evaluation with the dedicated local scorer login.
  summary   print tasks, attempts, pass/fail, scorecards and ledger spend.
  publish   shell to ``scripts/publish_live_release.py`` when that script exists.
  all       tasks, endpoint, run, solve, grade, score, summary (and publish with ``--publish``).

Nothing here bypasses a service gate: task freeze replays admission, endpoint approval needs a
passing conformance report, the run is created by ``RunCreationService`` against an approved
strictly-capped model configuration, and every model call goes through the worker's gateway and
ledger. The money cap is enforced twice: by ``--max-usd`` (default 2.00, hard refusal above
10.00) and by the run/campaign budget accounts. Secret values are never read or printed here
except by the gateway components that send them.

Environment: load the local ``.env`` (done automatically with ``--env-file``, default ``.env``),
``PCB_ENVIRONMENT=dev``; ``PCB_MIGRATION_DATABASE_URL`` (operator DSN, override with
``--database-url-env``), ``PCB_WORKER_DATABASE_URL``, ``PCB_SCORER_DATABASE_URL``,
``PCB_SERVICE_IDENTITY``, object-store settings, and the provider key in
``PCBSECRET__MODELS__OPENROUTER`` or ``PCBSECRET__MODELS__ZHIPU``.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.util
import io
import json
import os
import secrets as _random
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACK_DIR = ROOT / ".protected" / "taskpacks" / "python-pilot"
DEFAULT_ADMISSION_REPORTS = ROOT / ".protected" / "reports"
DEFAULT_STATE_DIR = ROOT / ".local" / "live-campaign"
DEFAULT_MODELS = ROOT / "config" / "live" / "models.json"
DEFAULT_PROTOCOL_DIRECTORY = ROOT / "config" / "protocols"
DEFAULT_BUDGET_PROFILES = ROOT / "config" / "budgets" / "pilot-v1.yaml"
DEFAULT_SCORING_POLICY = ROOT / "config" / "scoring" / "pilot-v1.yaml"
DEFAULT_SOLVE_RESOURCE_TEMPLATE = ROOT / "config" / "worker" / "local-small-resource.json"
IMAGE_IDENTITY_DIRECTORY = ROOT / "config" / "images"
PUBLISH_SCRIPT = ROOT / "scripts" / "publish_live_release.py"

DEFAULT_MAX_USD = "2.00"
HARD_MAX_USD = Decimal("10.00")
# RunCreateRequest.max_cost_micro_usd and the run repository both refuse more than USD 5.
SERVICE_MAX_MICRO_USD = 5_000_000
MAX_DELIVERIES = 3
TASK_ARTIFACT_DOMAIN = "task-packages"
TASK_SET_DOMAIN = "task-sets"
RUN_CONFIG_DOMAIN = "run-config"
QUOTAS: tuple[tuple[str, str, int], ...] = (
    ("internal", TASK_ARTIFACT_DOMAIN, 1024**3),
    ("hidden", TASK_ARTIFACT_DOMAIN, 1024**3),
    ("internal", TASK_SET_DOMAIN, 64 * 1024**2),
    ("internal", RUN_CONFIG_DOMAIN, 64 * 1024**2),
    ("internal", "model-config", 64 * 1024**2),
)
REQUIRED_ROLES = ("administrator", "curator", "operator")
PENDING_JOB_STATES = frozenset({"blocked", "queued", "leased", "retry_wait"})
STEPS = ("tasks", "endpoint", "run", "solve", "grade", "score", "summary", "publish")


class StepError(RuntimeError):
    """An operator-facing refusal or failed precondition; the message is safe to print."""


# --------------------------------------------------------------------------- pure helpers


def parse_max_usd(text: str) -> int:
    """Return the cap in integer micro-USD or refuse unsafe values."""
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise StepError("--max-usd must be a decimal USD amount") from None
    if not amount.is_finite() or amount <= 0:
        raise StepError("--max-usd must be positive")
    if amount > HARD_MAX_USD:
        raise StepError(f"refusing --max-usd above {HARD_MAX_USD} USD")
    micro = int((amount * 1_000_000).to_integral_value(rounding=ROUND_CEILING))
    if micro > SERVICE_MAX_MICRO_USD:
        raise StepError(
            "RunCreationService accepts at most 5.00 USD per run "
            "(polycodebench_services/runs.py max_cost_micro_usd); lower --max-usd"
        )
    return micro


def select_packages(
    pack_dir: Path, *, limit: int | None = None, slugs: Sequence[str] | None = None
) -> list[Path]:
    """Task package directories under ``pack_dir`` (by directory name), optionally filtered."""
    if not pack_dir.is_dir():
        raise StepError(f"pack directory does not exist: {pack_dir}")
    packages = sorted(
        path for path in pack_dir.iterdir() if path.is_dir() and (path / "manifest.yaml").is_file()
    )
    if slugs:
        wanted = list(dict.fromkeys(slugs))
        by_name = {path.name: path for path in packages}
        missing = [slug for slug in wanted if slug not in by_name]
        if missing:
            raise StepError("unknown task package(s): " + ", ".join(missing))
        packages = [by_name[slug] for slug in sorted(wanted)]
    if limit is not None:
        if limit < 1:
            raise StepError("--limit-tasks must be at least 1")
        packages = packages[:limit]
    if not packages:
        raise StepError(f"no task packages found under {pack_dir}")
    return packages


def split_weights(count: int) -> list[int]:
    """Basis-point sampling weights summing to exactly 10 000."""
    if not 1 <= count <= 10_000:
        raise StepError("a task set needs between 1 and 10000 members")
    base, remainder = divmod(10_000, count)
    return [base + (1 if index < remainder else 0) for index in range(count)]


def task_set_split(admission_tiers: Sequence[str]) -> str:
    """The only split the members' admission evidence may enter (see PostgresTaskRepository).

    Authored-fixture evidence is fixture-only. Development-sandbox suite admission (quality
    admission still pending) is unscored development data, never scored or held out.
    """
    tiers = set(admission_tiers)
    if tiers == {"local_fixture"}:
        return "fixture"
    if tiers == {"development_sandbox"}:
        return "public_development"
    raise StepError(
        "one task set cannot mix admission tiers or use unsupported ones: "
        + ", ".join(sorted(tiers))
    )


def build_task_set_document(
    *,
    name: str,
    members: Sequence[Mapping[str, Any]],
    split_seed: str,
    scoring_policy_digest: str,
    split: str = "fixture",
) -> dict[str, Any]:
    ordered = sorted(members, key=lambda item: str(item["task_digest"]))
    weights = split_weights(len(ordered))
    return {
        "schema_version": 1,
        "kind": "task_set",
        "task_set_id": name,
        "version": 1,
        "split": split,
        "split_seed": split_seed,
        "scoring_policy_digest": scoring_policy_digest,
        "method_deviation_ids": [],
        "members": [
            {
                "schema_version": 1,
                "kind": "task_set_member",
                "task_digest": item["task_digest"],
                "cluster_id": item["cluster_id"],
                "stratum_id": item["stratum_id"],
                "sampling_weight_bp": weight,
                "earliest_public_at": item["earliest_public_at"],
                "exposure_confidence": item["exposure_confidence"],
            }
            for item, weight in zip(ordered, weights, strict=True)
        ],
        "model_cutoffs": [],
    }


def build_run_config_document(
    *,
    task_set_digest: str,
    model_config_digest: str,
    protocol_id: str,
    protocol_digest: str,
    budget_profile: str,
    samples_per_task: int,
    master_seed: str,
    temperature: str | None,
    seed_policy: str,
    evaluation_policy_digest: str,
    hardware_class: str,
) -> dict[str, Any]:
    """The run configuration the run repository and the solve loader read.

    ``sampling.task_set_digest/samples_per_task/master_seed`` are what
    ``PostgresRunRepository`` matches against the request; ``protocol_id``,
    ``budget_profile`` and ``model_config_digest`` are what the solve loader resolves.
    """
    sampling: dict[str, Any] = {
        "kind": "sampling_config",
        "task_set_digest": task_set_digest,
        "samples_per_task": samples_per_task,
        "master_seed": master_seed,
        "provider_seed_policy": seed_policy,
    }
    if temperature is not None:
        sampling["temperature"] = temperature
    return {
        "schema_version": 1,
        "kind": "run_config",
        "task_set_digest": task_set_digest,
        "model_config_digest": model_config_digest,
        "harness_digest": protocol_digest,
        "protocol_id": protocol_id,
        "sampling": sampling,
        "budget_profile": budget_profile,
        "evaluation_policy_digest": evaluation_policy_digest,
        "hardware_class": hardware_class,
        "judge_panel_digest": None,
        "split": "fixture",
    }


def run_token_limits(
    *, attempts: int, input_context_tokens: int, output_tokens: int
) -> tuple[int, int]:
    """Run-level token ceilings covering every delivery of every single-shot attempt.

    The gateway reserves input as a provable byte bound (one token per byte), and the protocol
    admits up to four bytes per counted token, so each call may reserve 4x its token budget.
    """
    per_call_input = 4 * input_context_tokens
    return (
        attempts * per_call_input * MAX_DELIVERIES,
        attempts * output_tokens * MAX_DELIVERIES,
    )


def load_models(path: Path) -> dict[str, dict[str, Any]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("kind") != "live_campaign_models" or not isinstance(
        document.get("models"), dict
    ):
        raise StepError(f"{path} is not a live_campaign_models document")
    required = {
        "provider_kind",
        "base_url",
        "secret_ref",
        "model",
        "immutable_revision",
        "temperature",
        "seed_policy",
        "max_output_tokens",
        "capabilities",
        "price",
    }
    models: dict[str, dict[str, Any]] = {}
    for key, entry in document["models"].items():
        if not isinstance(entry, dict) or not required <= set(entry):
            raise StepError(f"model entry {key!r} is incomplete")
        kind = entry["provider_kind"]
        if kind == "openai_compatible":
            if not str(entry["base_url"]).startswith("https://"):
                raise StepError(f"model entry {key!r}: hosted endpoints must use HTTPS")
            if not entry.get("allowed_hosts"):
                raise StepError(f"model entry {key!r}: hosted endpoints need allowed_hosts")
        elif kind == "local":
            # Self-hosted server (e.g. Ollama) reached only inside the allowed CIDRs; no fee.
            if not entry.get("allowed_cidrs"):
                raise StepError(f"model entry {key!r}: local endpoints need allowed_cidrs")
        else:
            raise StepError(
                f"model entry {key!r}: provider_kind must be openai_compatible or local"
            )
        if not str(entry["secret_ref"]).startswith("secret://models/"):
            raise StepError(f"model entry {key!r}: secret_ref must be secret://models/<name>")
        models[key] = entry
    return models


def secret_env_name(secret_ref: str, namespace: str = "models") -> str:
    """Name of the environment variable the local resolver reads (never its value)."""
    prefix = f"secret://{namespace}/"
    if not secret_ref.startswith(prefix):
        raise StepError("secret reference is outside the model namespace")
    name = secret_ref[len(prefix) :]
    return f"PCBSECRET__{namespace.upper()}__{name.upper().replace('-', '_')}"


def parse_json_output(text: str) -> list[dict[str, Any]]:
    """JSON objects printed by a CLI: one pretty document or one object per line."""
    stripped = text.strip()
    if not stripped:
        return []
    try:
        value = json.loads(stripped)
        return [value] if isinstance(value, dict) else []
    except json.JSONDecodeError:
        pass
    found: list[dict[str, Any]] = []
    for line in stripped.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                found.append(value)
    return found


@contextlib.contextmanager
def scoped_env(values: Mapping[str, str]) -> Iterator[None]:
    """Set opt-in flags only for the duration of one step, then restore the prior values."""
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def load_env_file(path: Path) -> list[str]:
    """Load KEY=VALUE lines without overriding the caller's environment; return keys set."""
    if not path.is_file():
        return []
    loaded: list[str] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        entry = raw.strip()
        if not entry or entry.startswith("#"):
            continue
        name, separator, value = entry.partition("=")
        if not separator or not name.replace("_", "").isalnum() or not name[0].isalpha():
            raise StepError(f"invalid entry on line {number} of {path.name}")
        if name not in os.environ:
            os.environ[name] = value
            loaded.append(name)
    for target, source in (
        ("AWS_ACCESS_KEY_ID", "PCB_LOCAL_S3_ACCESS_KEY"),
        ("AWS_SECRET_ACCESS_KEY", "PCB_LOCAL_S3_SECRET_KEY"),
    ):
        if target not in os.environ and os.environ.get(source):
            os.environ[target] = os.environ[source]
            loaded.append(target)
    return loaded


def call_cli(
    label: str,
    main: Callable[[list[str]], int],
    argv: list[str],
    *,
    env: Mapping[str, str] | None = None,
    allowed_codes: frozenset[int] = frozenset({0}),
) -> tuple[int, list[dict[str, Any]]]:
    """Run an existing CLI entry point in-process and return its exit code and JSON output."""
    buffer = io.StringIO()
    with scoped_env(env or {}), contextlib.redirect_stdout(buffer):
        code = main(argv)
    if code not in allowed_codes:
        raise StepError(f"{label} exited with status {code}")
    return code, parse_json_output(buffer.getvalue())


def solve_until_done(
    *,
    pending_count: Callable[[], int],
    claim_once: Callable[[], bool],
    budget_exhausted: Callable[[], bool],
    sleep: Callable[[float], None] = time.sleep,
    poll_seconds: float = 5.0,
    idle_timeout_seconds: float = 600.0,
    max_claims: int = 10_000,
) -> int:
    """Claim this run's solve jobs until none is pending; return the number of claims."""
    claims = 0
    idle = 0.0
    while True:
        if pending_count() == 0:
            return claims
        if budget_exhausted():
            raise StepError("run budget is exhausted; remaining solve jobs were not claimed")
        if claims >= max_claims:
            raise StepError("solve claim limit reached")
        if claim_once():
            claims += 1
            idle = 0.0
            continue
        if idle >= idle_timeout_seconds:
            raise StepError("solve jobs are pending but none became claimable before the timeout")
        sleep(poll_seconds)
        idle += poll_seconds


def format_table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    cells = [[str(item) for item in headers], *[[str(item) for item in row] for row in rows]]
    widths = [max(len(row[index]) for row in cells) for index in range(len(headers))]
    lines = []
    for position, row in enumerate(cells):
        lines.append(
            "  ".join(value.ljust(width) for value, width in zip(row, widths, strict=True))
        )
        if position == 0:
            lines.append("  ".join("-" * width for width in widths))
    return "\n".join(lines)


def micro_usd(value: int | None) -> str:
    return "n/a" if value is None else f"{Decimal(value) / Decimal(1_000_000):.6f}"


def publish_command(*, script: Path, run_id: str, python: str = sys.executable) -> list[str]:
    """The release builder reads every attempt of the run, scored or model-failure zero."""
    return [python, str(script), "--run-id", run_id]


def runtime_image_reference(image_digest: str, directory: Path = IMAGE_IDENTITY_DIRECTORY) -> str:
    """Digest-pinned local reference of a frozen task runtime image from the image identities."""
    for path in sorted(directory.glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        images = document.get("images") if isinstance(document, dict) else None
        if not isinstance(images, dict):
            continue
        for image in images.values():
            if (
                isinstance(image, dict)
                and image.get("digest") == image_digest
                and isinstance(image.get("reference"), str)
            ):
                return str(image["reference"])
    raise StepError(f"no image identity under {directory} declares runtime {image_digest}")


def solve_resource_spec(
    template: Mapping[str, Any], *, resource_class: str, image: str, image_digest: str
) -> dict[str, Any]:
    spec = dict(template)
    spec.update(resource_class=resource_class, image=image, image_digest=image_digest)
    return spec


# --------------------------------------------------------------------------- state


class State:
    """Resumable step state; written atomically after every durable action."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / "state.json"
        if self.path.is_file():
            self.data: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self.data = {"schema_version": 1, "kind": "live_campaign_state"}

    def section(self, name: str) -> dict[str, Any]:
        value = self.data.setdefault(name, {})
        if not isinstance(value, dict):
            raise StepError(f"state section {name!r} is corrupt")
        return value

    def save(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, indent=2, sort_keys=True) + "\n", "utf-8")
        temporary.replace(self.path)


# --------------------------------------------------------------------------- runtime context


def _load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"_live_{name}", ROOT / "scripts" / f"{name}.py")
    if spec is None or spec.loader is None:
        raise StepError(f"cannot load scripts/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Context:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.state = State(args.state_dir)
        self._database: Any = None
        self._artifacts: Any = None

    # -- infrastructure -----------------------------------------------------------------

    @property
    def database_url(self) -> str:
        url = os.environ.get(self.args.database_url_env)
        if not url:
            raise StepError(f"{self.args.database_url_env} is required (operator database DSN)")
        return url

    @property
    def engine(self) -> Any:
        if self._database is None:
            from polycodebench_persistence.database import Database

            self._database = Database(self.database_url)
        return self._database.engine

    def object_store(self) -> Any:
        from polycodebench_persistence.object_store import S3ArtifactStore

        endpoint = os.environ.get("PCB_OBJECT_STORE_ENDPOINT")
        buckets = {
            "hidden": os.environ.get("PCB_BUCKET_HIDDEN"),
            "internal": os.environ.get("PCB_BUCKET_INTERNAL"),
            "public": os.environ.get("PCB_BUCKET_PUBLIC"),
        }
        if not endpoint or any(not value for value in buckets.values()):
            raise StepError("object-store endpoint and bucket names are required")
        return S3ArtifactStore.from_environment(
            endpoint_url=endpoint,
            buckets={key: str(value) for key, value in buckets.items()},
            region_name="us-east-1",
        )

    @property
    def artifacts(self) -> Any:
        if self._artifacts is None:
            from polycodebench_persistence.artifacts import ArtifactRepository

            self._artifacts = ArtifactRepository(
                self.engine, self.object_store(), max_upload_bytes=512 * 1024**2
            )
        return self._artifacts

    def close(self) -> None:
        if self._database is not None:
            self._database.dispose()
            self._database = None

    # -- identity -------------------------------------------------------------------------

    @property
    def subject(self) -> str:
        subject = (
            self.args.subject
            or os.environ.get("PCB_CLI_SUBJECT")
            or os.environ.get("PCB_SERVICE_IDENTITY")
        )
        if not subject:
            raise StepError("--subject, PCB_CLI_SUBJECT or PCB_SERVICE_IDENTITY is required")
        return subject

    def principal(self, needed: Sequence[str]) -> Any:
        from polycodebench_persistence.identities import PostgresIdentityRepository
        from polycodebench_services.rbac import Principal, Role

        identities = PostgresIdentityRepository(self.engine)
        roles = set(identities.roles_for_subject(self.subject))
        missing = [role for role in needed if role not in roles]
        if missing and self.args.grant_local_roles:
            if os.environ.get("PCB_ENVIRONMENT") != "dev":
                raise StepError("--grant-local-roles is restricted to PCB_ENVIRONMENT=dev")
            for role in missing:
                identities.grant_role(
                    subject_id=self.subject,
                    role=role,
                    actor_subject=self.subject,
                    request_id=f"live-campaign-grant-{uuid4()}",
                )
            roles = set(identities.roles_for_subject(self.subject))
            missing = [role for role in needed if role not in roles]
        if missing:
            raise StepError(
                f"subject {self.subject!r} lacks role(s) {', '.join(missing)}; "
                "grant them or pass --grant-local-roles on the local dev stack"
            )
        known = {item.value for item in Role}
        return Principal(
            subject_id=self.subject,
            roles=frozenset(Role(item) for item in roles if item in known),
        )

    # -- shared helpers ------------------------------------------------------------------------

    def ensure_quotas(self) -> None:
        from polycodebench_persistence.models import artifact_quota
        from sqlalchemy.dialects.postgresql import insert as postgres_insert

        with self.engine.begin() as connection:
            for visibility, domain, max_bytes in QUOTAS:
                connection.execute(
                    postgres_insert(artifact_quota)
                    .values(visibility=visibility, encryption_domain=domain, max_bytes=max_bytes)
                    .on_conflict_do_nothing(
                        index_elements=[
                            artifact_quota.c.visibility,
                            artifact_quota.c.encryption_domain,
                        ]
                    )
                )

    def upload(self, *, visibility: str, domain: str, body: bytes, media_type: str) -> str:
        from polycodebench_core.canonical import sha256_bytes

        owner = "live-campaign"
        upload_id = self.artifacts.begin_upload(
            owner=owner,
            visibility=visibility,
            encryption_domain=domain,
            expected_digest=sha256_bytes(body),
            expected_size=len(body),
            media_type=media_type,
        )
        self.artifacts.upload(upload_id=upload_id, owner=owner, body=body)
        return str(self.artifacts.finalize(upload_id=upload_id, owner=owner))

    def model_key(self) -> str:
        return str(self.args.model)

    def model_spec(self) -> dict[str, Any]:
        models = load_models(self.args.models)
        if self.args.model not in models:
            raise StepError(
                f"unknown model {self.args.model!r}; choose one of: {', '.join(sorted(models))}"
            )
        return models[self.args.model]

    def worker_main(self) -> Callable[[list[str]], int]:
        from polycodebench_orchestration.worker_cli import main

        return main

    def require_spend(self, what: str) -> None:
        if not self.args.allow_spend:
            raise StepError(f"{what} contacts the paid provider; rerun with --allow-spend")


# --------------------------------------------------------------------------- step: tasks


def _existing_task_version(engine: Any, slug: str, version: int) -> dict[str, Any] | None:
    from polycodebench_persistence.models import task, task_version
    from sqlalchemy import select

    with engine.connect() as connection:
        row = (
            connection.execute(
                select(
                    task_version.c.id,
                    task_version.c.digest,
                    task_version.c.document,
                    task_version.c.cluster_id,
                    task_version.c.stratum_id,
                )
                .join(task, task.c.id == task_version.c.task_id)
                .where(task.c.slug == slug, task_version.c.version == version)
            )
            .mappings()
            .one_or_none()
        )
    return dict(row) if row is not None else None


def _record_task(entry: dict[str, Any], row: Mapping[str, Any]) -> None:
    document = row["document"]
    source = document["source"]
    runtime = document["runtime"]
    entry.update(
        task_version_id=str(row["id"]),
        task_digest=str(row["digest"]),
        cluster_id=str(row["cluster_id"]),
        stratum_id=str(row["stratum_id"]),
        earliest_public_at=source.get("first_public_at"),
        exposure_confidence=source.get("date_confidence"),
        runtime_image_digest=runtime["image_digest"],
        resource_class=runtime["resource_class"],
        admission_tier=document["admission_report"]["execution_tier"],
    )


def suite_admission_report(
    package: Path, package_digest: str, reports_dir: Path, target: Path
) -> bool:
    """Copy the stored executable-admission report for this exact package snapshot, if any.

    ``pcb task freeze`` still replays the admission; this only avoids running it twice.
    """
    candidate = reports_dir / f"{package.name}.json"
    if not candidate.is_file():
        return False
    body = candidate.read_bytes()
    try:
        document = json.loads(body)
    except ValueError:
        return False
    if (
        not isinstance(document, dict)
        or document.get("kind") != "suite_admission_report"
        or document.get("package_digest") != package_digest
        or document.get("executable_admission_passed") is not True
    ):
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    return True


def step_tasks(ctx: Context) -> None:
    from polycodebench_core.canonical import canonical_document_bytes, canonical_document_digest
    from polycodebench_core.models import TaskSet
    from polycodebench_persistence.models import task_set
    from polycodebench_scoring.loader import load_scoring_policy
    from polycodebench_services.task_packages import TaskPackageImporter
    from sqlalchemy import select

    args = ctx.args
    packages = select_packages(args.pack_dir, limit=args.limit_tasks, slugs=args.tasks)
    ctx.principal(("curator",))
    ctx.ensure_quotas()
    pcb = _load_script("pcb")
    importer = TaskPackageImporter()
    tasks = ctx.state.section("tasks")
    pcb_env = {"PCB_DATABASE_URL": ctx.database_url, "PCB_CLI_SUBJECT": ctx.subject}
    reports = args.state_dir / "admission"
    selected: list[str] = []
    for package in packages:
        imported = importer.import_package(package)
        manifest = imported.manifest.task
        key = f"{manifest.task_id}@{manifest.version}"
        selected.append(key)
        entry = tasks.setdefault(key, {})
        entry.update(package=str(package), package_digest=imported.package_digest)
        existing = _existing_task_version(ctx.engine, manifest.task_id, manifest.version)
        if existing is not None:
            document = existing["document"]
            if (
                document["visible_bundle"]["digest"] != imported.visible_digest
                or document["hidden_bundle"]["digest"] != imported.hidden_digest
            ):
                raise StepError(
                    f"{key} is already frozen with different bundle bytes (another pack variant "
                    "with the same task_id/version?); bump the task version to freeze this one"
                )
            _record_task(entry, existing)
            ctx.state.save()
            print(f"tasks: {key} already frozen ({entry['task_version_id']})")
            continue
        report = reports / f"{package.name}-{imported.package_digest[7:19]}.json"
        suite = imported.manifest.source.source_kind != "authored_fixture"
        if suite and not report.is_file():
            if suite_admission_report(
                package, imported.package_digest, args.admission_reports, report
            ):
                print(f"tasks: {key} reuses its stored executable admission report")
            else:
                plugin_id = imported.manifest.runtime.language_plugin_id
                print(f"tasks: admitting {key} ({plugin_id} suite in Docker)...", flush=True)
                tool = _load_script(f"{plugin_id}_task_tool")
                if asyncio.run(tool.admit(package, report)) != 0:
                    raise StepError(f"{key} failed executable admission; see {report}")
        if not report.is_file():
            print(f"tasks: admitting {key} (authored fixtures in Docker)...", flush=True)
            code, _ = call_cli(
                f"pcb task validate {package.name}",
                pcb.main,
                ["task", "validate", str(package), "--report", str(report)],
                allowed_codes=frozenset({0, 2}),
            )
            if code != 0:
                raise StepError(f"{key} failed admission; see {report}")
        if not entry.get("visible_artifact_id"):
            entry["manifest_artifact_id"] = ctx.upload(
                visibility="internal",
                domain=TASK_ARTIFACT_DOMAIN,
                body=(imported.source_root / "manifest.yaml").read_bytes(),
                media_type="application/yaml",
            )
            entry["hidden_artifact_id"] = ctx.upload(
                visibility="hidden",
                domain=TASK_ARTIFACT_DOMAIN,
                body=imported.hidden_archive,
                media_type="application/zip",
            )
            entry["visible_artifact_id"] = ctx.upload(
                visibility="internal",
                domain=TASK_ARTIFACT_DOMAIN,
                body=imported.visible_archive,
                media_type="application/zip",
            )
            ctx.state.save()
        print(f"tasks: freezing {key} (admission is replayed)...", flush=True)
        call_cli(
            f"pcb task freeze {package.name}",
            pcb.main,
            [
                "task",
                "freeze",
                "--package",
                str(package),
                "--report",
                str(report),
                "--manifest-artifact-id",
                entry["manifest_artifact_id"],
                "--visible-artifact-id",
                entry["visible_artifact_id"],
                "--hidden-artifact-id",
                entry["hidden_artifact_id"],
            ],
            env=pcb_env,
        )
        frozen = _existing_task_version(ctx.engine, manifest.task_id, manifest.version)
        if frozen is None:
            raise StepError(f"{key} freeze reported success but no task version is visible")
        _record_task(entry, frozen)
        ctx.state.save()

    members = [tasks[key] for key in selected]
    split = task_set_split([str(item.get("admission_tier", "local_fixture")) for item in members])
    policy_digest = canonical_document_digest(
        load_scoring_policy(args.scoring_policy or DEFAULT_SCORING_POLICY)
    )
    name_inputs: list[str] = [item["task_digest"] for item in members]
    if args.scoring_policy is not None:
        # A task set freezes its scoring policy digest, so a different policy is a new set.
        name_inputs.append(policy_digest)
    name_hash = canonical_digest_of(name_inputs)[7:19]
    name = f"live-{args.pack_dir.name.lower().replace('_', '-')}-{name_hash}"[:63].strip("-")
    document = TaskSet.model_validate(
        build_task_set_document(
            name=name,
            members=members,
            split_seed=str(int(name_hash, 16)),
            scoring_policy_digest=policy_digest,
            split=split,
        )
    )
    digest = canonical_document_digest(document)
    section = ctx.state.section("task_set")
    with ctx.engine.connect() as connection:
        row = connection.execute(
            select(task_set.c.id, task_set.c.status).where(task_set.c.digest == digest)
        ).one_or_none()
    if row is None:
        manifest_id = ctx.upload(
            visibility="internal",
            domain=TASK_SET_DOMAIN,
            body=canonical_document_bytes(document),
            media_type="application/json",
        )
        document_path = args.state_dir / f"task-set-{name_hash}.json"
        document_path.write_text(json.dumps(document.model_dump(mode="json"), indent=2), "utf-8")
        _, output = call_cli(
            "pcb taskset create",
            pcb.main,
            [
                "taskset",
                "create",
                "--document",
                str(document_path),
                "--manifest-artifact-id",
                manifest_id,
            ],
            env=pcb_env,
        )
        task_set_id = str(output[-1]["task_set_id"])
        status = "draft"
    else:
        task_set_id, status = str(row.id), str(row.status)
    if status != "frozen":
        call_cli(
            "pcb taskset freeze",
            pcb.main,
            ["taskset", "freeze", "--task-set-id", task_set_id, "--digest", digest],
            env=pcb_env,
        )
    section.clear()
    section.update(
        task_set_id=task_set_id, digest=digest, name=name, members=selected, status="frozen"
    )
    ctx.state.save()
    print(f"tasks: frozen {split} task set {name} ({task_set_id}) with {len(selected)} task(s)")


def canonical_digest_of(value: Any) -> str:
    from polycodebench_core.canonical import canonical_digest

    return str(canonical_digest(value))


# --------------------------------------------------------------------------- step: endpoint


def build_model_config(spec: Mapping[str, Any], endpoint_id: str) -> Any:
    from polycodebench_core.model_planning import ModelConfig

    return ModelConfig.model_validate(
        {
            "schema_version": 1,
            "kind": "model_config",
            "provider_kind": spec["provider_kind"],
            "model": spec["model"],
            "immutable_revision": spec["immutable_revision"],
            "endpoint_id": endpoint_id,
            "declared_capabilities": spec["capabilities"],
            "price": spec["price"],
            "temperature": spec["temperature"],
            "seed_policy": spec["seed_policy"],
            "reasoning": spec.get("reasoning"),
            "max_output_tokens": spec["max_output_tokens"],
            "cost_policy": "provider_bound",
            "strict_money_cap": True,
        },
        strict=False,
    )


def step_endpoint(ctx: Context) -> None:
    from polycodebench_core.canonical import canonical_document_digest
    from polycodebench_core.endpoint_policy import EndpointNetworkPolicy, NetworkPolicyKind
    from polycodebench_core.model_contracts import ModelCapabilities, ProviderKind
    from polycodebench_orchestration.gateway.adapters.local import LocalEndpointAdapter
    from polycodebench_orchestration.gateway.adapters.openai_compatible import (
        OpenAICompatibleAdapter,
    )
    from polycodebench_orchestration.gateway.conformance import run_conformance
    from polycodebench_orchestration.gateway.endpoint_check import static_endpoint_check
    from polycodebench_orchestration.gateway.secrets import configured_secret_resolver
    from polycodebench_orchestration.gateway.transport import PinnedHttpTransport
    from polycodebench_persistence.endpoints import PostgresEndpointRepository
    from polycodebench_persistence.model_configs import PostgresModelConfigRepository
    from polycodebench_persistence.models import endpoint_registration, model_revision
    from polycodebench_services.model_endpoints import ModelEndpointService
    from sqlalchemy import select

    spec = ctx.model_spec()
    key = ctx.model_key()
    is_local = spec["provider_kind"] == "local"
    entry = ctx.state.section("endpoints").setdefault(key, {})
    if entry.get("approved") and entry.get("model_config_id"):
        print(f"endpoint: {key} already approved ({entry['endpoint_id']})")
        return
    principal = ctx.principal(("administrator",))
    repository = PostgresEndpointRepository(ctx.engine, database_role="pcb_endpoint_administrator")
    service = ModelEndpointService(repository)
    capabilities = ModelCapabilities.model_validate(spec["capabilities"], strict=False)
    if not entry.get("endpoint_id"):
        endpoint_id = service.register(
            principal,
            provider_kind=ProviderKind(spec["provider_kind"]),
            base_url=spec["base_url"],
            secret_ref=spec["secret_ref"],
            policy=(
                EndpointNetworkPolicy(
                    kind=NetworkPolicyKind.INTERNAL_LOCAL,
                    allowed_cidrs=tuple(spec["allowed_cidrs"]),
                )
                if is_local
                else EndpointNetworkPolicy(
                    kind=NetworkPolicyKind.PUBLIC_ALLOWLIST,
                    allowed_hosts=tuple(spec["allowed_hosts"]),
                )
            ),
            declared_capabilities=capabilities,
        )
        entry.update(endpoint_id=str(endpoint_id), approved=False)
        ctx.state.save()
        print(f"endpoint: registered pending {key} endpoint {endpoint_id}")
    endpoint_id = entry["endpoint_id"]
    config = build_model_config(spec, endpoint_id)
    config_path = ctx.args.state_dir / f"model-config-{key}.json"
    config_path.write_text(json.dumps(config.model_dump(mode="json"), indent=2), "utf-8")
    entry["model_config_file"] = str(config_path)
    entry["model_config_digest"] = canonical_document_digest(config)

    endpoint, status = repository.get_for_conformance(UUID(endpoint_id))
    secrets = configured_secret_resolver(os.environ.get("PCB_MODEL_SECRET_NAMESPACE", "models"))
    if status != "approved":
        report = static_endpoint_check(endpoint, secrets)
        entry["static_check"] = {
            "ok": report["ok"],
            "resolution_permitted": report["resolution"].get("permitted"),
            "secret_provisioned": report["secret_provisioned"],
        }
        ctx.state.save()
        if not report["ok"]:
            raise StepError(
                "static endpoint check failed (DNS policy or secret); set "
                f"{secret_env_name(spec['secret_ref'])} and verify the allowlisted host"
            )
        if not is_local:
            ctx.require_spend("the live conformance probe")
        print(f"endpoint: running live conformance against {endpoint.endpoint.url} ...", flush=True)
        conformance = asyncio.run(
            run_conformance(
                LocalEndpointAdapter() if is_local else OpenAICompatibleAdapter(),
                PinnedHttpTransport(),
                endpoint,
                config,
                secrets.resolve(endpoint.secret_ref),
            )
        )
        conformance_path = ctx.args.state_dir / f"conformance-{key}.json"
        conformance_path.write_text(json.dumps(conformance, indent=2, default=str), "utf-8")
        entry["conformance_report"] = str(conformance_path)
        ctx.state.save()
        if conformance.get("passed") is not True:
            raise StepError(f"conformance did not pass; see {conformance_path}")
        with ctx.engine.connect() as connection:
            version = connection.execute(
                select(endpoint_registration.c.row_version).where(
                    endpoint_registration.c.id == UUID(endpoint_id)
                )
            ).scalar_one()
        service.decide(
            principal,
            UUID(endpoint_id),
            decision="approved",
            reason=f"live campaign conformance passed for {spec['model']}",
            expected_version=int(version),
            conformance_report=conformance,
        )
        print(f"endpoint: approved {endpoint_id}")
    entry["approved"] = True
    ctx.state.save()

    ctx.ensure_quotas()
    config_id, revision_id = PostgresModelConfigRepository(
        ctx.engine, ctx.artifacts, owner="live-campaign"
    ).register(config)
    with ctx.engine.connect() as connection:
        bound_endpoint = connection.execute(
            select(model_revision.c.endpoint_registration_id).where(
                model_revision.c.id == revision_id
            )
        ).scalar_one()
    if str(bound_endpoint) != endpoint_id:
        raise StepError(
            "the model revision row is bound to a different endpoint; bump immutable_revision "
            f"for {key} in {ctx.args.models}"
        )
    entry.update(model_config_id=str(config_id), model_revision_id=str(revision_id))
    ctx.state.save()
    print(f"endpoint: model config {config_id}, model revision {revision_id}")


# --------------------------------------------------------------------------- step: run


def _run_account_id(engine: Any, run_id: str) -> str | None:
    from polycodebench_persistence.models import budget_account
    from sqlalchemy import select

    with engine.connect() as connection:
        value = connection.execute(
            select(budget_account.c.id).where(
                budget_account.c.scope_kind == "run", budget_account.c.scope_id == run_id
            )
        ).scalar_one_or_none()
    return None if value is None else str(value)


def step_run(ctx: Context) -> None:
    from polycodebench_core.canonical import (
        canonical_digest,
        canonical_document_digest,
        canonical_json_bytes,
    )
    from polycodebench_orchestration.gateway.adapters.openai_compatible import (
        OpenAICompatibleAdapter,
    )
    from polycodebench_orchestration.gateway.plan import plan_model_run
    from polycodebench_persistence.model_configs import PostgresModelConfigRepository
    from polycodebench_persistence.model_ledger import PostgresModelLedger
    from polycodebench_persistence.models import campaign, config_document
    from polycodebench_persistence.runs import PostgresRunRepository
    from polycodebench_scoring.loader import load_scoring_policy
    from polycodebench_services.runs import RunCreateRequest, RunCreationService
    from polycodebench_services.solve_budget_profiles import load_solve_budget_profiles
    from polycodebench_services.solve_protocols import load_protocol_directory
    from sqlalchemy import insert, select

    args = ctx.args
    cap = parse_max_usd(args.max_usd)
    section = ctx.state.section("run")
    if section.get("run_id"):
        print(f"run: already created ({section['run_id']})")
        return
    task_set = ctx.state.section("task_set")
    endpoint = ctx.state.section("endpoints").get(ctx.model_key(), {})
    if task_set.get("status") != "frozen":
        raise StepError("run needs a frozen task set; run the tasks step first")
    if not endpoint.get("model_config_id"):
        raise StepError(f"run needs an approved endpoint for {ctx.model_key()}; run endpoint first")
    attempts = len(task_set["members"])
    principal = ctx.principal(("operator",))

    protocols = load_protocol_directory(DEFAULT_PROTOCOL_DIRECTORY)
    if args.protocol not in protocols:
        raise StepError(f"protocol {args.protocol!r} is not installed")
    protocol = protocols[args.protocol]
    if protocol.mode != "single_shot":
        raise StepError("the live campaign driver only runs single-shot protocols")
    profiles = load_solve_budget_profiles(DEFAULT_BUDGET_PROFILES)
    if profiles.get(args.budget_profile) != protocol.budget:
        raise StepError(f"budget profile {args.budget_profile!r} does not match {args.protocol}")

    config = PostgresModelConfigRepository(ctx.engine, ctx.artifacts, owner="live-campaign").load(
        UUID(endpoint["model_config_id"])
    )
    plan = plan_model_run(
        config=config,
        protocol=protocol.to_definition(),
        adapter=OpenAICompatibleAdapter(),
        tasks=attempts,
        samples_per_task=1,
        max_request_bytes=4 * protocol.context.max_input_context_tokens,
        max_deliveries=MAX_DELIVERIES,
    )
    plan_path = args.state_dir / "run-plan.json"
    plan_path.write_text(json.dumps(plan.model_dump(mode="json"), indent=2), "utf-8")
    if not plan.compatible:
        raise StepError("run plan is blocked: " + "; ".join(plan.blockers))
    worst = plan.worst_case_money_micro_usd
    if worst is None or worst > cap:
        raise StepError(
            f"worst-case single-delivery exposure {micro_usd(worst)} USD exceeds the cap "
            f"{micro_usd(cap)} USD; reduce tasks or raise --max-usd (max 5.00)"
        )
    print(
        f"run: plan ok, worst case {micro_usd(worst)} USD "
        f"({micro_usd(plan.worst_case_money_with_retries_micro_usd)} with retries), "
        f"cap {micro_usd(cap)} USD"
    )

    ledger = PostgresModelLedger(ctx.engine)
    if section.get("max_cost_micro_usd") not in (None, cap):
        raise StepError(
            "this state already holds a campaign with a different cap; use a new --state-dir"
        )
    if not section.get("campaign_id"):
        section.update(campaign_id=str(uuid4()), max_cost_micro_usd=cap)
        ctx.state.save()
    campaign_id = UUID(section["campaign_id"])
    campaign_account = ledger.ensure_account(
        scope_kind="campaign", scope_id=str(campaign_id), hard_limit_micro_usd=cap
    )
    with ctx.engine.begin() as connection:
        exists = connection.execute(
            select(campaign.c.id).where(campaign.c.id == campaign_id)
        ).scalar_one_or_none()
        if exists is None:
            connection.execute(
                insert(campaign).values(
                    id=campaign_id,
                    name=f"live-{ctx.model_key()}-{campaign_id.hex[:8]}"[:160],
                    status="planned",
                    owner_subject=ctx.subject,
                    budget_account_id=campaign_account,
                    row_version=0,
                )
            )
    section["campaign_account_id"] = str(campaign_account)

    if not section.get("master_seed"):
        section["master_seed"] = str(_random.randbits(63))
        ctx.state.save()
    protocol_digest = canonical_digest(protocol.model_dump(mode="json"))
    document = build_run_config_document(
        task_set_digest=task_set["digest"],
        model_config_digest=endpoint["model_config_digest"],
        protocol_id=protocol.protocol_id,
        protocol_digest=protocol_digest,
        budget_profile=args.budget_profile,
        samples_per_task=1,
        master_seed=section["master_seed"],
        temperature=config.temperature,
        seed_policy=config.seed_policy,
        evaluation_policy_digest=canonical_document_digest(
            load_scoring_policy(args.scoring_policy or DEFAULT_SCORING_POLICY)
        ),
        hardware_class="local-docker-development",
    )
    run_config_digest = canonical_digest(document)
    with ctx.engine.connect() as connection:
        run_config_id = connection.execute(
            select(config_document.c.id).where(
                config_document.c.kind == "run_config",
                config_document.c.digest == run_config_digest,
            )
        ).scalar_one_or_none()
    if run_config_id is None:
        ctx.ensure_quotas()
        artifact_id = ctx.upload(
            visibility="internal",
            domain=RUN_CONFIG_DOMAIN,
            body=canonical_json_bytes(document),
            media_type="application/json",
        )
        run_config_id = uuid4()
        with ctx.engine.begin() as connection:
            connection.execute(
                insert(config_document).values(
                    id=run_config_id,
                    kind="run_config",
                    version_label=run_config_digest[7:19],
                    digest=run_config_digest,
                    canonical_artifact_id=UUID(artifact_id),
                    schema_version=1,
                    document=document,
                )
            )
    section["run_config_id"] = str(run_config_id)

    max_input, max_output = run_token_limits(
        attempts=attempts,
        input_context_tokens=protocol.context.max_input_context_tokens,
        output_tokens=min(config.max_output_tokens, protocol.budget.output_tokens),
    )
    request = RunCreateRequest(
        campaign_id=campaign_id,
        config_document_id=UUID(str(run_config_id)),
        task_set_id=UUID(task_set["task_set_id"]),
        model_revision_id=UUID(endpoint["model_revision_id"]),
        samples_per_task=1,
        master_seed=section["master_seed"],
        max_attempts=attempts,
        max_cost_micro_usd=cap,
        max_input_tokens=max_input,
        max_output_tokens=max_output,
        endpoint_registration_id=UUID(endpoint["endpoint_id"]),
    )
    section.setdefault("idempotency_key", f"live-campaign:{campaign_id}")
    ctx.state.save()
    result = RunCreationService(
        PostgresRunRepository(ctx.engine, database_role="pcb_operator")
    ).create(principal, request, section["idempotency_key"])
    run_id = str(result.run_id)
    run_account = _run_account_id(ctx.engine, run_id)
    if run_account is None:
        raise StepError("capped run has no run budget account")
    for attempt_id in result.attempt_ids:
        ledger.ensure_account(
            scope_kind="attempt",
            scope_id=str(attempt_id),
            hard_limit_micro_usd=cap,
            parent_account_id=UUID(run_account),
        )
    section.update(
        run_id=run_id,
        run_account_id=run_account,
        attempt_ids=[str(item) for item in result.attempt_ids],
    )
    ctx.state.save()
    print(
        f"run: created {run_id} with {len(result.attempt_ids)} attempt(s), cap {micro_usd(cap)} USD"
    )


# --------------------------------------------------------------------------- step: solve


def _solve_job_counts(engine: Any, run_id: str) -> dict[str, int]:
    from polycodebench_persistence.models import attempt, stage_job
    from sqlalchemy import func, select

    with engine.connect() as connection:
        rows = connection.execute(
            select(stage_job.c.state, func.count())
            .select_from(stage_job.join(attempt, stage_job.c.attempt_id == attempt.c.id))
            .where(attempt.c.run_id == UUID(run_id), stage_job.c.stage == "solve")
            .group_by(stage_job.c.state)
        ).all()
    return {str(state): int(count) for state, count in rows}


def _account_exhausted(engine: Any, account_id: str) -> bool:
    from polycodebench_persistence.models import budget_account
    from sqlalchemy import select

    with engine.connect() as connection:
        row = connection.execute(
            select(
                budget_account.c.hard_limit_micro_usd,
                budget_account.c.spent_confirmed,
                budget_account.c.reserved_open,
                budget_account.c.uncertain_committed,
            ).where(budget_account.c.id == UUID(account_id))
        ).one()
    used = int(row.spent_confirmed) + int(row.reserved_open) + int(row.uncertain_committed)
    return used >= int(row.hard_limit_micro_usd)


def step_solve(ctx: Context) -> None:
    run = ctx.state.section("run")
    if not run.get("run_id"):
        raise StepError("solve needs a created run; run the run step first")
    run_id = str(run["run_id"])
    if not any(
        count
        for state, count in _solve_job_counts(ctx.engine, run_id).items()
        if state in PENDING_JOB_STATES
    ):
        print("solve: no pending solve jobs")
        return
    if ctx.model_spec()["provider_kind"] != "local":
        ctx.require_spend("solving")
    tasks = ctx.state.section("tasks")
    members = ctx.state.section("task_set").get("members", [])
    groups = sorted(
        {(tasks[key]["resource_class"], tasks[key]["runtime_image_digest"]) for key in members}
    )
    worker_main = ctx.worker_main()
    workers = ctx.state.section("solve_workers")
    template = json.loads(DEFAULT_SOLVE_RESOURCE_TEMPLATE.read_text(encoding="utf-8"))
    for resource_class, image_digest in groups:
        group_key = f"{resource_class}@{image_digest}"
        if group_key in workers:
            continue
        image = runtime_image_reference(image_digest)
        spec_path = (
            ctx.args.state_dir / f"solve-resource-{resource_class}-{image_digest[7:19]}.json"
        )
        allowlist_path = ctx.args.state_dir / f"solve-image-allowlist-{image_digest[7:19]}.json"
        spec_path.write_text(
            json.dumps(
                solve_resource_spec(
                    template, resource_class=resource_class, image=image, image_digest=image_digest
                ),
                indent=2,
            ),
            "utf-8",
        )
        allowlist_path.write_text(json.dumps({image: image_digest}, indent=2), "utf-8")
        _, output = call_cli(
            "pcb-worker local-register",
            worker_main,
            [
                "local-register",
                "--resource-spec",
                str(spec_path),
                "--image-allowlist",
                str(allowlist_path),
                "--workload-identity",
                f"live-campaign-solve-{resource_class}-{image_digest[7:19]}",
            ],
            env={"PCB_LOCAL_WORKER_SETUP_ENABLED": "true"},
        )
        workers[group_key] = {
            "worker_id": str(output[-1]["worker_id"]),
            "image_allowlist": str(allowlist_path),
        }
        ctx.state.save()

    def claim_once() -> bool:
        claimed = False
        for worker in workers.values():
            _, output = call_cli(
                "pcb-worker local-run",
                worker_main,
                [
                    "local-run",
                    "--worker-id",
                    worker["worker_id"],
                    "--run-id",
                    run_id,
                    "--image-allowlist",
                    worker["image_allowlist"],
                ],
                env={"PCB_WORKER_DISPATCH_ENABLED": "true"},
            )
            claimed = claimed or bool(output and output[-1].get("claimed"))
        if claimed:
            print(f"solve: {_solve_job_counts(ctx.engine, run_id)}", flush=True)
        return claimed

    claims = solve_until_done(
        pending_count=lambda: sum(
            count
            for state, count in _solve_job_counts(ctx.engine, run_id).items()
            if state in PENDING_JOB_STATES
        ),
        claim_once=claim_once,
        budget_exhausted=lambda: _account_exhausted(ctx.engine, str(run["run_account_id"])),
        poll_seconds=ctx.args.poll_seconds,
        idle_timeout_seconds=ctx.args.idle_timeout,
    )
    print(f"solve: done after {claims} claim(s): {_solve_job_counts(ctx.engine, run_id)}")


# --------------------------------------------------------------------------- step: grade / score


def _attempts(engine: Any, run_id: str) -> list[dict[str, Any]]:
    from polycodebench_persistence.models import attempt, task, task_version
    from sqlalchemy import select

    with engine.connect() as connection:
        rows = (
            connection.execute(
                select(attempt.c.id, attempt.c.state, attempt.c.failure_class, task.c.slug)
                .join(task_version, task_version.c.id == attempt.c.task_version_id)
                .join(task, task.c.id == task_version.c.task_id)
                .where(attempt.c.run_id == UUID(run_id))
                .order_by(task.c.slug, attempt.c.sample_index)
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


def _evaluation(engine: Any, evaluation_id: str) -> dict[str, Any] | None:
    from polycodebench_persistence.models import evaluation
    from sqlalchemy import select

    with engine.connect() as connection:
        row = (
            connection.execute(
                select(evaluation.c.state, evaluation.c.gate, evaluation.c.failure_class).where(
                    evaluation.c.id == UUID(evaluation_id)
                )
            )
            .mappings()
            .one_or_none()
        )
    return None if row is None else dict(row)


def step_grade(ctx: Context) -> None:
    run = ctx.state.section("run")
    if not run.get("run_id"):
        raise StepError("grade needs a created run")
    worker_main = ctx.worker_main()
    grading = ctx.state.section("grading")
    if not grading.get("worker_id"):
        _, output = call_cli(
            "pcb-worker local-grading-register",
            worker_main,
            [
                "local-grading-register",
                "--workload-identity",
                "live-campaign-grading-1",
                *(
                    ["--scoring-policy", str(ctx.args.scoring_policy)]
                    if ctx.args.scoring_policy
                    else []
                ),
            ],
            env={"PCB_LOCAL_WORKER_SETUP_ENABLED": "true"},
        )
        grading.update(
            worker_id=str(output[-1]["worker_id"]),
            policy_config_id=str(output[-1]["policy_config_id"]),
            resource_class=str(output[-1]["resource_class"]),
        )
        ctx.state.save()
    evaluations = grading.setdefault("evaluations", {})
    for item in _attempts(ctx.engine, str(run["run_id"])):
        attempt_id = str(item["id"])
        if item["state"] != "completed":
            continue
        entry = evaluations.get(attempt_id)
        if entry is None:
            _, output = call_cli(
                "pcb-worker local-grading-enqueue",
                worker_main,
                [
                    "local-grading-enqueue",
                    "--attempt-id",
                    attempt_id,
                    "--policy-config-id",
                    grading["policy_config_id"],
                    "--resource-class",
                    grading["resource_class"],
                ],
                env={"PCB_LOCAL_GRADING_SCHEDULE_ENABLED": "true"},
            )
            entry = {
                "evaluation_id": str(output[-1]["evaluation_id"]),
                "job_id": str(output[-1]["job_id"]),
                "task": item["slug"],
            }
            evaluations[attempt_id] = entry
            ctx.state.save()
        current = _evaluation(ctx.engine, entry["evaluation_id"])
        if current is not None and current["state"] in {"queued", "running"}:
            print(f"grade: evaluating {item['slug']} ({entry['evaluation_id']}) ...", flush=True)
            call_cli(
                "pcb-worker local-grading-run",
                worker_main,
                [
                    "local-grading-run",
                    "--worker-id",
                    grading["worker_id"],
                    "--job-id",
                    entry["job_id"],
                ],
                env={"PCB_WORKER_DISPATCH_ENABLED": "true"},
            )
            current = _evaluation(ctx.engine, entry["evaluation_id"])
        if current is not None:
            entry.update(state=current["state"], gate=current["gate"])
            ctx.state.save()
    print(f"grade: {len(evaluations)} evaluation(s) recorded")


def step_score(ctx: Context) -> None:
    grading = ctx.state.section("grading")
    worker_main = ctx.worker_main()
    for entry in grading.get("evaluations", {}).values():
        if entry.get("scorecard_id"):
            continue
        current = _evaluation(ctx.engine, entry["evaluation_id"])
        if current is None or current["state"] != "ready":
            continue
        _, output = call_cli(
            "pcb-worker local-score-evaluation",
            worker_main,
            ["local-score-evaluation", "--evaluation-id", entry["evaluation_id"]],
            env={"PCB_LOCAL_SCORING_ENABLED": "true"},
        )
        result = output[-1]
        entry.update(
            scorecard_id=str(result["scorecard_id"]),
            score_gate=result.get("gate"),
            score_status=result.get("status"),
            total_score=result.get("total_score"),
        )
        ctx.state.save()
        print(f"score: {entry['task']} -> {entry['scorecard_id']} ({entry['score_gate']})")


# --------------------------------------------------------------------------- summary / publish


def step_summary(ctx: Context) -> None:
    from polycodebench_persistence.model_ledger import PostgresModelLedger

    task_set = ctx.state.section("task_set")
    run = ctx.state.section("run")
    print(f"\nTask set: {task_set.get('name', 'n/a')} ({len(task_set.get('members', []))} tasks)")
    if not run.get("run_id"):
        print("No run created yet.")
        return
    evaluations = {
        attempt_id: entry
        for attempt_id, entry in ctx.state.section("grading").get("evaluations", {}).items()
    }
    rows = []
    attempts = _attempts(ctx.engine, str(run["run_id"]))
    for item in attempts:
        entry = evaluations.get(str(item["id"]), {})
        current = _evaluation(ctx.engine, entry["evaluation_id"]) if entry else None
        rows.append(
            [
                item["slug"],
                str(item["id"])[:8],
                item["state"],
                current["state"] if current else "-",
                current["gate"] if current else "-",
                entry.get("scorecard_id", "-")[:8] if entry.get("scorecard_id") else "-",
                entry.get("total_score") if entry.get("total_score") is not None else "-",
            ]
        )
    print(f"Run: {run['run_id']}")
    print(
        format_table(["task", "attempt", "solve", "evaluation", "gate", "scorecard", "score"], rows)
    )
    passed = sum(1 for row in rows if row[4] == "pass")
    failed = sum(1 for row in rows if row[4] == "fail")
    model_zero = sum(1 for item in attempts if item.get("failure_class") == "model_failure")
    print(
        f"\npass {passed}  fail {failed}  model-failure zero {model_zero}  "
        f"ungraded {len(rows) - passed - failed - model_zero}"
    )
    ledger = PostgresModelLedger(ctx.engine)
    spend_rows = []
    for label, key in (("campaign", "campaign_account_id"), ("run", "run_account_id")):
        if not run.get(key):
            continue
        summary = ledger.account_summary(UUID(run[key]))["money_micro_usd"]
        exposure = ledger.unresolved_exposure(UUID(run[key]))
        spend_rows.append(
            [
                label,
                micro_usd(summary["limit"]),
                micro_usd(summary["spent_confirmed"]),
                micro_usd(summary["reserved_open"]),
                micro_usd(summary["uncertain_committed"]),
                len(exposure),
            ]
        )
    print("\nSpend (USD, from the model ledger)")
    print(
        format_table(
            ["account", "limit", "spent", "reserved", "uncertain", "unresolved"], spend_rows
        )
    )


def step_publish(ctx: Context) -> None:
    run = ctx.state.section("run")
    if not PUBLISH_SCRIPT.is_file():
        print(f"publish: {PUBLISH_SCRIPT.relative_to(ROOT)} does not exist yet; skipped")
        return
    if not run.get("run_id"):
        raise StepError("publish needs a created run")
    # Architecture v1 10.3: model-failure attempts count as zero, so a completed run whose
    # attempts all failed on the model's own output is still publishable; the builder refuses
    # runs that are not completed or that have neither scorecards nor model-failure zeros.
    command = publish_command(script=PUBLISH_SCRIPT, run_id=str(run["run_id"]))
    completed = subprocess.run(command, cwd=ROOT, check=False)
    if completed.returncode != 0:
        raise StepError(f"publish_live_release.py exited with status {completed.returncode}")


STEP_FUNCTIONS: dict[str, Callable[[Context], None]] = {
    "tasks": step_tasks,
    "endpoint": step_endpoint,
    "run": step_run,
    "solve": step_solve,
    "grade": step_grade,
    "score": step_score,
    "summary": step_summary,
    "publish": step_publish,
}


def steps_for(command: str, *, publish: bool) -> list[str]:
    if command != "all":
        return [command]
    return [step for step in STEPS if step != "publish" or publish]


# --------------------------------------------------------------------------- CLI


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="live_campaign", description=__doc__.split("\n\n")[0])
    parser.add_argument("step", choices=(*STEPS, "all"))
    parser.add_argument("--pack-dir", type=Path, default=DEFAULT_PACK_DIR)
    parser.add_argument("--admission-reports", type=Path, default=DEFAULT_ADMISSION_REPORTS)
    parser.add_argument("--limit-tasks", type=int)
    parser.add_argument("--tasks", type=lambda text: [item for item in text.split(",") if item])
    parser.add_argument("--model", default="openrouter-qwen3-coder")
    parser.add_argument("--models", type=Path, default=DEFAULT_MODELS)
    parser.add_argument("--max-usd", default=DEFAULT_MAX_USD)
    parser.add_argument("--protocol", default="single-shot-v1")
    parser.add_argument("--budget-profile", default="single-shot-small-v1")
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--database-url-env", default="PCB_MIGRATION_DATABASE_URL")
    parser.add_argument("--subject")
    parser.add_argument("--grant-local-roles", action="store_true")
    parser.add_argument("--allow-spend", action="store_true")
    parser.add_argument("--scoring-policy", type=Path, help="frozen scoring policy for grading")
    parser.add_argument("--publish", action="store_true", help="include publish in 'all'")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--idle-timeout", type=float, default=600.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        parse_max_usd(args.max_usd)
        args.state_dir = args.state_dir.resolve()
        args.state_dir.mkdir(parents=True, exist_ok=True)
        load_env_file(args.env_file)
        ctx = Context(args)
        try:
            for step in steps_for(args.step, publish=args.publish):
                STEP_FUNCTIONS[step](ctx)
        finally:
            ctx.close()
    except StepError as error:
        print(f"live_campaign: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
