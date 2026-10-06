"""``pcb-judge``: the judge gateway and the review/adjudication surface.

Exit codes follow the CLI contract: 0 success, 2 validation/config error, 3 permission error,
4 blocked (no judge access, review required, calibration unmet), 5 infrastructure failure.

The offline commands (``rubric``, ``panel``, ``packet``, ``validate-vote``) touch no database and
make no model call, so an operator can inspect exactly what a judge would see. The stored commands
need the database, the object store and an approved judge endpoint; without them ``run`` exits 4
with ``JUDGE_PANEL_UNAVAILABLE`` instead of substituting another model for the judge.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from polycodebench_core.application_errors import AuthorizationError, InvalidState, ServiceError
from polycodebench_core.canonical import (
    canonical_document_bytes,
    parse_json_strict,
    sha256_bytes,
)
from polycodebench_core.judge_calibration import CalibrationLabel, CalibrationPacket
from polycodebench_core.judge_contracts import (
    AdjudicationDecision,
    JudgeDelivery,
    JudgementResult,
    JudgePacket,
    JudgePacketInput,
    JudgePanel,
    JudgeVote,
    PanelUnavailable,
    VoteRejected,
    judge_record_bytes,
)
from polycodebench_core.model_contracts import CallScope, ProviderKind
from polycodebench_persistence.artifacts import ArtifactRepository
from polycodebench_persistence.database import Database
from polycodebench_persistence.endpoints import PostgresEndpointRepository
from polycodebench_persistence.judging import PostgresJudgingRepository
from polycodebench_persistence.model_configs import PostgresModelConfigRepository
from polycodebench_persistence.model_ledger import PostgresModelLedger
from polycodebench_persistence.object_store import S3ArtifactStore
from polycodebench_services.judging import (
    aggregate,
    audit_selected,
    build_adjudication,
    build_packet,
    load_calibration_policy,
    load_panel,
    load_rubric,
    parse_vote,
    review_entry,
)
from polycodebench_services.judging_calibration import import_labels, measure_calibration
from polycodebench_services.rbac import Permission, Principal, Role, authorize

from polycodebench_orchestration.gateway.adapters.local import LocalEndpointAdapter
from polycodebench_orchestration.gateway.adapters.openai_compatible import (
    OpenAICompatibleAdapter,
)
from polycodebench_orchestration.gateway.secrets import configured_secret_resolver
from polycodebench_orchestration.gateway.service import GatewayResult, ModelGateway
from polycodebench_orchestration.gateway.store import ArtifactResponseStore
from polycodebench_orchestration.gateway.throttle import ThrottleRegistry
from polycodebench_orchestration.gateway.transport import PinnedHttpTransport
from polycodebench_orchestration.judge.protocol import load_judge_protocol
from polycodebench_orchestration.judge.runner import JudgeRunner, VoteArtifacts

EXIT_OK, EXIT_VALIDATION, EXIT_PERMISSION, EXIT_BLOCKED, EXIT_INFRA = 0, 2, 3, 4, 5
RUBRIC = Path("config/judging/rubric-v1.yaml")
PANEL = Path("config/judging/panel-v1.yaml")
CALIBRATION = Path("config/judging/calibration-v1.yaml")
OWNER = "pcb-judge"


@dataclass
class World:
    database: Database
    store: ArtifactResponseStore
    judging: PostgresJudgingRepository
    configs: PostgresModelConfigRepository
    ledger: PostgresModelLedger
    endpoints: PostgresEndpointRepository

    def gateway(self) -> ModelGateway:
        return ModelGateway(
            endpoints=self.endpoints,
            ledger=self.ledger,
            store=self.store,
            transport=PinnedHttpTransport(),
            secrets=configured_secret_resolver(
                os.environ.get("PCB_MODEL_SECRET_NAMESPACE", "models")
            ),
            adapters={
                ProviderKind.OPENAI_COMPATIBLE: OpenAICompatibleAdapter(),
                ProviderKind.LOCAL: LocalEndpointAdapter(),
            },
            throttles=ThrottleRegistry(),
        )


def _world() -> World:
    url = os.environ.get("PCB_DATABASE_URL")
    endpoint = os.environ.get("PCB_OBJECT_STORE_ENDPOINT")
    missing = [
        name
        for name, value in (
            ("PCB_DATABASE_URL", url),
            ("PCB_OBJECT_STORE_ENDPOINT", endpoint),
        )
        if not value
    ]
    if missing:
        raise InvalidState("missing configuration: " + ", ".join(missing))
    assert url is not None and endpoint is not None
    database = Database(url)
    store = S3ArtifactStore(
        endpoint_url=endpoint,
        buckets={
            "hidden": os.environ.get("PCB_BUCKET_HIDDEN", "pcb-judge-hidden"),
            "internal": os.environ.get("PCB_BUCKET_INTERNAL", "pcb-judge-internal"),
            "public": os.environ.get("PCB_BUCKET_PUBLIC", "pcb-judge-public"),
        },
    )
    artifacts = ArtifactRepository(database.engine, store, max_upload_bytes=8 * 1024 * 1024)
    return World(
        database=database,
        store=ArtifactResponseStore(artifacts, owner=OWNER, encryption_domain="judge"),
        judging=PostgresJudgingRepository(database.engine),
        configs=PostgresModelConfigRepository(database.engine, artifacts, owner=OWNER),
        ledger=PostgresModelLedger(database.engine),
        endpoints=PostgresEndpointRepository(database.engine),
    )


def _principal() -> Principal:
    subject = os.environ.get("PCB_SERVICE_IDENTITY", "")
    roles = {Role(item) for item in os.environ.get("PCB_ROLES", "").split(",") if item}
    return Principal(subject, frozenset(roles))


def _load(path: str | Path) -> Any:
    return parse_json_strict(Path(path).read_bytes())


def _emit(value: Any) -> None:
    print(json.dumps(value, sort_keys=True, indent=2, default=str))


def _packet_summary(packet: JudgePacket) -> dict[str, Any]:
    return {
        "packet_id": packet.packet_id,
        "packet_digest": packet.digest(),
        "rubric": f"{packet.rubric_id}@{packet.rubric_version}",
        "rubric_digest": packet.rubric_digest,
        "panel": f"{packet.panel_id}@{packet.panel_version}",
        "panel_digest": packet.panel_digest,
        "language": packet.language,
        "packet_role": packet.packet_role,
        "items": [item.item_id for item in packet.items],
        "spans": len(packet.spans),
        "untrusted_comments": [
            {
                "anchor_id": comment.anchor_id,
                "path": comment.path,
                "instruction_attempt": comment.instruction_attempt,
            }
            for comment in packet.untrusted_comments
        ],
        "withheld_fields": list(packet.withheld_fields),
        "tools": list(packet.tools),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pcb-judge")
    commands = parser.add_subparsers(dest="command", required=True)

    rubric = commands.add_parser("rubric", help="show the frozen judge rubric")
    rubric.add_argument("--rubric", type=Path, default=RUBRIC)

    panel = commands.add_parser("panel", help="show the fixed judge panel and its readiness")
    panel.add_argument("--panel", type=Path, default=PANEL)
    panel.add_argument("--rubric", type=Path, default=RUBRIC)
    panel.add_argument("--cohort", help="cohort JSON naming the candidate model configurations")

    packet = commands.add_parser("packet", help="build the anonymized packet; no model call")
    packet.add_argument("--input", required=True, help="judge_packet_input JSON")
    packet.add_argument("--rubric", type=Path, default=RUBRIC)
    packet.add_argument("--panel", type=Path, default=PANEL)
    packet.add_argument("--out", help="write the canonical packet document here")

    validate = commands.add_parser("validate-vote", help="validate one judge response offline")
    validate.add_argument("--packet", required=True)
    validate.add_argument("--response", required=True)
    validate.add_argument("--panel", type=Path, default=PANEL)

    run = commands.add_parser("run", help="run the panel for one packet (requires judge access)")
    run.add_argument("--evaluation-id", type=UUID, required=True)
    run.add_argument("--input", required=True, help="judge_packet_input JSON")
    run.add_argument("--rubric", type=Path, default=RUBRIC)
    run.add_argument("--panel", type=Path, default=PANEL)
    run.add_argument("--cohort-id", type=UUID)

    stored = commands.add_parser("result", help="show a stored judge result and its deliveries")
    stored.add_argument("packet_id", type=UUID)

    queue = commands.add_parser("review-queue", help="list packets whose result is not ready")
    queue.add_argument("--limit", type=int, default=20)

    show = commands.add_parser("show", help="reviewer view of one packet (identity withheld)")
    show.add_argument("packet_id", type=UUID)

    decide = commands.add_parser("adjudicate", help="record a reviewer override of one item score")
    decide.add_argument("packet_id", type=UUID)
    decide.add_argument("--item", required=True)
    decide.add_argument("--score", required=True, choices=("0.000000", "0.500000", "1.000000"))
    decide.add_argument("--anchor", action="append", required=True, dest="anchors")
    decide.add_argument("--reason", required=True)
    decide.add_argument("--reviewed-votes", required=True, help="comma-separated vote indexes")
    decide.add_argument("--rubric", type=Path, default=RUBRIC)
    decide.add_argument("--panel", type=Path, default=PANEL)
    decide.add_argument("--supersedes", type=UUID)

    calibration = commands.add_parser(
        "calibration", help="measure the panel against qualified human labels"
    )
    calibration.add_argument("--policy", type=Path, default=CALIBRATION)
    calibration.add_argument("--packets", required=True, help="calibration packet JSON array")
    calibration.add_argument("--labels", help="human label JSON array; absent means blocked")
    calibration.add_argument("--judge-results", help="judge result documents keyed by packet id")
    calibration.add_argument(
        "--scored-packets",
        help="scored packet digests; without them disjointness stays undemonstrated",
    )
    calibration.add_argument("--rubric", type=Path, default=RUBRIC)
    calibration.add_argument("--panel", type=Path, default=PANEL)
    calibration.add_argument("--report", help="write the calibration report here")
    calibration.add_argument(
        "--notes",
        default="labels absent: judge access and human review are unverified inputs",
        help="recorded alongside the metrics; never a substitute for them",
    )
    return parser


def _offline(args: argparse.Namespace) -> int:
    if args.command == "rubric":
        rubric = load_rubric(args.rubric)
        _emit(
            {
                "rubric_id": rubric.rubric_id,
                "version": rubric.version,
                "digest": rubric.digest(),
                "languages": list(rubric.languages),
                "prompt_template_version": rubric.prompt_template_version,
                "items": [
                    {
                        "item_id": item.item_id,
                        "dimension": item.dimension.value,
                        "anchors": [anchor.value for anchor in item.anchors],
                        "residual_reason": item.residual_reason,
                    }
                    for item in rubric.items
                ],
            }
        )
        return EXIT_OK
    if args.command == "panel":
        rubric = load_rubric(args.rubric)
        panel = load_panel(args.panel)
        cohort = _load(args.cohort) if args.cohort else None
        candidates = tuple(cohort.get("candidate_model_config_ids", ())) if cohort else ()
        report: dict[str, Any] = {
            "panel_id": panel.panel_id,
            "version": panel.version,
            "digest": panel.digest(),
            "rubric_digest": rubric.digest(),
            "matches_rubric": rubric.rubric_id == panel.rubric_id
            and rubric.version == panel.rubric_version,
            "provisioned": panel.provisioned,
            "judge_revision": panel.judge_revision,
            "votes_required": panel.votes_required,
            "distinct_seeds": panel.distinct_seeds,
            "max_deliveries_per_vote": panel.max_deliveries_per_vote(),
            "repair_instruction_id": panel.repair.instruction_id,
            "tools": list(panel.tools),
            "blinded_fields": list(panel.blinded_fields),
            "effective_for_scoring": panel.effective_for_scoring,
            "calibration_status": panel.calibration_status,
        }
        try:
            report["judge_model_config_id"] = panel.require_access(candidates)
        except PanelUnavailable as blocked:
            report["ready"] = False
            report["blocker"] = blocked.code
            report["blocker_detail"] = str(blocked)
            _emit(report)
            return EXIT_BLOCKED
        report["ready"] = True
        _emit(report)
        return EXIT_OK
    if args.command == "packet":
        rubric = load_rubric(args.rubric)
        panel = load_panel(args.panel)
        packet_input = JudgePacketInput.model_validate(_load(args.input), strict=False)
        packet = build_packet(rubric=rubric, panel=panel, packet_input=packet_input)
        if args.out:
            Path(args.out).write_bytes(packet.canonical_bytes())
        _emit(_packet_summary(packet))
        return EXIT_OK
    if args.command == "validate-vote":
        panel = load_panel(args.panel)
        packet = JudgePacket.model_validate(_unwrap(_load(args.packet)), strict=False)
        text = Path(args.response).read_text(encoding="utf-8")
        vote = parse_vote(
            text=text,
            packet=packet,
            panel=panel,
            vote_index=0,
            seed=None,
            raw_response_digest=sha256_bytes(text.encode("utf-8")),
        )
        _emit({"accepted": True, "vote": vote.model_dump(mode="json")})
        return EXIT_OK
    return EXIT_VALIDATION


def _run_packet(world: World, args: argparse.Namespace) -> int:
    # Dispatching judge calls spends the evaluation budget, so it is an operator/evaluator action.
    authorize(_principal(), Permission.RUN_PLAN)
    rubric = load_rubric(args.rubric)
    panel = load_panel(args.panel)
    cohort_candidates: tuple[str, ...] = ()
    if args.cohort_id is not None:
        cohort = world.judging.cohort_for_panel(args.cohort_id)
        cohort_candidates = tuple(cohort["candidate_model_config_ids"])
    judge_config_id = panel.require_access(cohort_candidates)
    config = world.configs.load(UUID(judge_config_id))
    packet_input = JudgePacketInput.model_validate(_load(args.input), strict=False)
    packet = build_packet(rubric=rubric, panel=panel, packet_input=packet_input)
    packet_artifact = world.store.put(
        packet.canonical_bytes(), kind="judge_packet", media_type="application/json"
    )
    packet_row_id = world.judging.register_packet(
        evaluation_id=args.evaluation_id,
        packet=packet,
        packet_artifact_id=packet_artifact,
        cohort_id=args.cohort_id,
    )
    runner = JudgeRunner(
        gateway=world.gateway(),
        panel=panel,
        protocol=load_judge_protocol(),
        resolve_artifacts=_artifact_resolver(world),
        persist_delivery=_delivery_writer(world, packet_row_id),
        persist_vote=_vote_writer(world, packet_row_id),
    )
    run = asyncio.run(
        runner.run(
            packet=packet,
            config=config,
            config_document_id=UUID(judge_config_id),
            scope=CallScope(kind="evaluation", scope_id=args.evaluation_id),
        )
    )
    result = run.result(panel, audit_selected=audit_selected(packet, panel))
    digest = _store_result(world, packet_row_id, args.evaluation_id, result)
    _emit(
        {
            "packet_row_id": str(packet_row_id),
            "packet": _packet_summary(packet),
            "status": result.status,
            "valid_vote_indexes": list(result.valid_vote_indexes),
            "deliveries": [delivery.model_dump(mode="json") for delivery in result.deliveries],
            "items": [item.model_dump(mode="json") for item in result.items],
            "report_digest": digest,
        }
    )
    return EXIT_OK if result.status == "ready" else EXIT_BLOCKED


def _artifact_resolver(
    world: World,
) -> Callable[[JudgePacket, int, int, GatewayResult], VoteArtifacts]:
    def resolve(
        packet: JudgePacket, vote_index: int, delivery_index: int, result: GatewayResult
    ) -> VoteArtifacts:
        del packet, vote_index, delivery_index
        found = world.judging.delivery_artifacts(result.intent_id, result.delivery_index)
        return VoteArtifacts(
            raw_artifact_id=str(found["raw_artifact_id"]) if found["raw_artifact_id"] else None,
            call_delivery_id=str(found["call_delivery_id"]) if found["call_delivery_id"] else None,
            call_intent_id=str(result.intent_id),
        )

    return resolve


def _delivery_writer(
    world: World, packet_row_id: UUID
) -> Callable[[JudgePacket, JudgeDelivery, VoteArtifacts], None]:
    def persist(packet: JudgePacket, delivery: JudgeDelivery, artifacts: VoteArtifacts) -> None:
        del packet
        world.judging.record_delivery(
            packet_row_id=packet_row_id,
            delivery=delivery,
            call_delivery_id=UUID(artifacts.call_delivery_id)
            if artifacts.call_delivery_id
            else None,
            call_intent_id=UUID(artifacts.call_intent_id) if artifacts.call_intent_id else None,
        )

    return persist


def _vote_writer(
    world: World, packet_row_id: UUID
) -> Callable[[JudgePacket, JudgeVote, bytes, VoteArtifacts], None]:
    def persist(
        packet: JudgePacket, vote: JudgeVote, normalized: bytes, artifacts: VoteArtifacts
    ) -> None:
        del packet
        if artifacts.call_delivery_id is None:
            raise InvalidState("the gateway delivery for this vote was not recorded")
        artifact = world.store.put(normalized, kind="judge_vote", media_type="application/json")
        world.judging.record_vote(
            packet_row_id=packet_row_id,
            vote=vote.model_copy(update={"normalized_artifact_id": str(artifact)}),
        )

    return persist


def _store_result(
    world: World, packet_row_id: UUID, evaluation_id: UUID, result: JudgementResult
) -> str:
    """Freeze one result document. A later adjudication appends a new result; nothing is edited."""
    digest = sha256_bytes(canonical_document_bytes(result))
    frozen = result.model_copy(update={"report_digest": digest})
    artifact = world.store.put(
        canonical_document_bytes(frozen), kind="judge_result", media_type="application/json"
    )
    world.judging.record_result(
        packet_row_id=packet_row_id,
        evaluation_id=evaluation_id,
        result=frozen,
        report_artifact_id=artifact,
        result_index=world.judging.next_result_index(packet_row_id),
    )
    return digest


def _stored_result(world: World, packet_id: UUID) -> int:
    # Stored votes and deliveries are restricted evidence, not a public read.
    authorize(_principal(), Permission.RESTRICTED_EVIDENCE_READ)
    rows = world.judging.results(packet_id)
    if not rows:
        _emit({"error": "no stored result for this packet", "packet_id": str(packet_id)})
        return EXIT_VALIDATION
    latest = rows[-1]
    _emit(
        {
            "packet_id": str(packet_id),
            "result_index": latest["result_index"],
            "status": latest["status"],
            "report_digest": latest["report_digest"],
            "deliveries": [
                {
                    "vote_index": delivery["vote_index"],
                    "delivery_index": delivery["delivery_index"],
                    "status": delivery["status"],
                    "invalid_reason": delivery["invalid_reason"],
                    "seed": str(delivery["seed"]) if delivery["seed"] is not None else None,
                    "seed_supported": delivery["seed_supported"],
                    "repair_instruction_id": delivery["repair_instruction_id"],
                }
                for delivery in world.judging.deliveries(packet_id)
            ],
            "votes": [
                {"vote_index": vote["vote_index"], "status": vote["status"]}
                for vote in world.judging.votes(packet_id)
            ],
        }
    )
    return EXIT_OK


def _review_queue(world: World, args: argparse.Namespace) -> int:
    authorize(_principal(), Permission.EVALUATION_ADJUDICATE)
    _emit({"packets": world.judging.packets_needing_review(limit=args.limit)})
    return EXIT_OK


def _show(world: World, packet_id: UUID) -> int:
    authorize(_principal(), Permission.EVALUATION_ADJUDICATE)
    row = world.judging.packet_row(packet_id)
    packet = _stored_packet(world, row)
    results = world.judging.results(packet_id)
    if not results:
        _emit({"error": "no stored result for this packet"})
        return EXIT_VALIDATION
    result = _stored_result_document(world, results[-1])
    entry = review_entry(
        packet=packet,
        result=result,
        votes=_stored_votes(world, packet_id),
        adjudications=_stored_adjudications(world, UUID(str(row["evaluation_id"]))),
    )
    _emit(
        {
            "packet": _packet_summary(packet),
            "result_status": entry.result_status,
            "triggers_by_item": entry.triggers_by_item,
            "needs_decision": list(entry.needs_decision),
            "votes": [vote.model_dump(mode="json") for vote in entry.votes],
            "deliveries": [
                JudgeDelivery.model_validate(delivery).model_dump(mode="json")
                for delivery in entry.deliveries
            ],
            "adjudications": [decision.model_dump(mode="json") for decision in entry.adjudications],
            "identity_withheld": True,
        }
    )
    return EXIT_OK


def _stored_packet(world: World, row: dict[str, Any]) -> JudgePacket:
    body = world.store.get(UUID(str(row["packet_artifact_id"])))
    packet = JudgePacket.model_validate(_unwrap_bytes(body), strict=False)
    if packet.digest() != row["packet_digest"]:
        raise InvalidState("stored packet does not match the registered digest")
    return packet


def _stored_result_document(world: World, row: dict[str, Any]) -> JudgementResult:
    return JudgementResult.model_validate(_payload(world, row, "report_artifact_id"), strict=False)


def _payload(world: World, row: dict[str, Any], artifact_column: str) -> dict[str, Any]:
    """Read a stored judge artefact and prove its bytes still match the registered digest.

    Artefacts are canonical envelopes, so the payload is unwrapped before it is validated against
    the contract. An artefact that no longer matches its digest is refused rather than repaired.
    """
    body = world.store.get(UUID(str(row[artifact_column])))
    if (
        sha256_bytes(body)
        != row["report_digest" if artifact_column == "report_artifact_id" else "digest"]
    ):
        raise InvalidState("stored judge artefact bytes do not match the registered digest")
    return _unwrap_bytes(body)


def _stored_votes(world: World, packet_id: UUID) -> tuple[JudgeVote, ...]:
    votes: list[JudgeVote] = []
    for row in world.judging.votes(packet_id):
        artifact = row["normalized_artifact_id"]
        if artifact is None:
            raise InvalidState(
                f"vote {row['vote_index']} has no stored document and cannot be reviewed"
            )
        body = world.store.get(UUID(str(artifact)))
        votes.append(JudgeVote.model_validate(_unwrap_bytes(body), strict=False))
    return tuple(votes)


def _stored_adjudications(world: World, evaluation_id: UUID) -> tuple[AdjudicationDecision, ...]:
    decisions: list[AdjudicationDecision] = []
    for row in world.judging.adjudications_for(evaluation_id):
        body = world.store.get(UUID(str(row["resolution_artifact_id"])))
        decisions.append(AdjudicationDecision.model_validate(_unwrap_bytes(body), strict=False))
    return tuple(decisions)


def _unwrap(document: Any) -> dict[str, Any]:
    """Return the payload of a canonical judge envelope.

    Every stored judge artefact is a canonical envelope, so a file or artifact is unwrapped before
    it is validated against the contract.
    """
    if not isinstance(document, dict) or not isinstance(document.get("payload"), dict):
        raise InvalidState("judge document is not a canonical envelope")
    payload = document["payload"]
    assert isinstance(payload, dict)
    return payload


def _unwrap_bytes(body: bytes) -> dict[str, Any]:
    """Unwrap a stored artefact's canonical envelope."""
    return _unwrap(parse_json_strict(body))


def _adjudicate(world: World, args: argparse.Namespace) -> int:
    principal = _principal()
    authorize(principal, Permission.EVALUATION_ADJUDICATE)
    if not principal.subject_id:
        raise AuthorizationError("a reviewer decision needs an identified subject")
    rubric = load_rubric(args.rubric)
    row = world.judging.packet_row(args.packet_id)
    packet = _stored_packet(world, row)
    votes = _stored_votes(world, args.packet_id)
    reviewed = tuple(int(value) for value in args.reviewed_votes.split(",") if value.strip())
    panel = load_panel(args.panel)
    decision = build_adjudication(
        packet=packet,
        rubric=rubric,
        panel=panel,
        item_id=args.item,
        score=args.score,
        cited_anchor_ids=tuple(args.anchors),
        reason=args.reason,
        reviewer_subject=principal.subject_id,
        votes=votes,
        reviewed_vote_indexes=reviewed,
        supersedes_id=str(args.supersedes) if args.supersedes else None,
    )
    artifact = world.store.put(
        judge_record_bytes(decision), kind="judge_adjudication", media_type="application/json"
    )
    adjudication_id = world.judging.record_adjudication(
        evaluation_id=row["evaluation_id"],
        decision=decision,
        resolution_artifact_id=artifact,
        supersedes_id=args.supersedes,
    )
    # The override takes effect as a new immutable result. Earlier results and every vote stay.
    decisions = _stored_adjudications(world, UUID(str(row["evaluation_id"])))
    result = _recompute(
        world,
        packet_row_id=args.packet_id,
        packet=packet,
        panel=panel,
        votes=votes,
        adjudications=decisions,
        audit_selected=audit_selected(packet, panel),
    )
    digest = _store_result(
        world,
        args.packet_id,
        UUID(str(row["evaluation_id"])),
        result,
    )
    _emit(
        {
            "adjudication_id": str(adjudication_id),
            "packet_id": str(args.packet_id),
            "item_id": decision.item_id,
            "score": decision.score,
            "cited_anchor_ids": list(decision.cited_anchor_ids),
            "reviewed_vote_indexes": list(decision.reviewed_vote_indexes),
            "supersedes_id": decision.supersedes_id,
            "votes_retained": [vote.vote_index for vote in votes],
            "result_status": result.status,
            "result_report_digest": digest,
        }
    )
    return EXIT_OK


def _recompute(
    world: World,
    *,
    packet_row_id: UUID,
    packet: JudgePacket,
    panel: JudgePanel,
    votes: tuple[JudgeVote, ...],
    adjudications: tuple[AdjudicationDecision, ...],
    audit_selected: bool,
) -> JudgementResult:
    """Rebuild an item result from the retained deliveries, votes and adjudications."""
    deliveries = tuple(_delivery_from_row(row) for row in world.judging.deliveries(packet_row_id))
    return aggregate(
        packet=packet,
        panel=panel,
        votes=votes,
        deliveries=deliveries,
        adjudications=adjudications,
        audit_selected=audit_selected,
    )


def _delivery_from_row(row: dict[str, Any]) -> JudgeDelivery:
    seed = row.get("seed")
    artifact = row["raw_artifact_id"]
    delivery = row["call_delivery_id"]
    intent = row["call_intent_id"]
    return JudgeDelivery(
        packet_id=str(row["packet_id"]),
        vote_index=int(row["vote_index"]),
        delivery_index=int(row["delivery_index"]),
        status=row["status"],
        invalid_reason=row["invalid_reason"],
        detail=row["detail"],
        judge_revision=row["judge_revision"],
        seed=int(seed) if seed is not None else None,
        seed_supported=bool(row["seed_supported"]),
        repair_instruction_id=row["repair_instruction_id"],
        raw_response_digest=row["raw_response_digest"],
        raw_response_artifact_id=str(artifact) if artifact else None,
        call_delivery_id=str(delivery) if delivery else None,
        call_intent_id=str(intent) if intent else None,
        created_at=row["created_at"].isoformat().replace("+00:00", "Z"),
    )


def _calibration(args: argparse.Namespace) -> int:
    policy = load_calibration_policy(args.policy)
    rubric = load_rubric(args.rubric)
    panel = load_panel(args.panel)
    packets = tuple(
        CalibrationPacket.model_validate(entry, strict=False) for entry in _load(args.packets)
    )
    missing: list[str] = []
    labels: tuple[CalibrationLabel, ...] = ()
    if not args.labels:
        missing.append(
            f"no qualified {policy.qualification_pattern} human label file supplied; "
            "calibration cannot be reported as measured"
        )
    else:
        labels = import_labels(_load(args.labels), policy=policy, packets=packets)
    results: dict[str, JudgementResult] = {}
    if args.judge_results:
        for packet_id, document in _load(args.judge_results).items():
            results[packet_id] = JudgementResult.model_validate(document, strict=False)
    report = measure_calibration(
        policy=policy,
        rubric_digest=rubric.digest(),
        panel_digest=panel.digest(),
        packets=packets,
        labels=labels,
        results=results,
        scored_packet_digests=tuple(_load(args.scored_packets)) if args.scored_packets else (),
        missing_inputs=tuple(missing),
        notes=args.notes,
    )
    if args.report:
        Path(args.report).write_bytes(report.canonical_bytes())
    _emit(_unwrap(parse_json_strict(report.canonical_bytes())))
    return EXIT_OK if report.status == "measured" else EXIT_BLOCKED


def main() -> int:
    args = _parser().parse_args()
    world: World | None = None
    try:
        if args.command in {"rubric", "panel", "packet", "validate-vote"}:
            return _offline(args)
        if args.command == "calibration":
            return _calibration(args)
        world = _world()
        if args.command == "run":
            return _run_packet(world, args)
        if args.command == "result":
            return _stored_result(world, args.packet_id)
        if args.command == "review-queue":
            return _review_queue(world, args)
        if args.command == "show":
            return _show(world, args.packet_id)
        if args.command == "adjudicate":
            return _adjudicate(world, args)
        return EXIT_VALIDATION
    except AuthorizationError:
        print("permission denied", file=sys.stderr)
        return EXIT_PERMISSION
    except PanelUnavailable as blocked:
        print(f"{blocked.code}: {blocked}", file=sys.stderr)
        return EXIT_BLOCKED
    except VoteRejected as rejected:
        _emit({"accepted": False, "reason": rejected.reason, "detail": rejected.detail})
        return EXIT_VALIDATION
    except ServiceError as error:
        print(error.code, file=sys.stderr)
        return EXIT_INFRA if error.status_code >= 500 else EXIT_VALIDATION
    except ValueError as error:
        print(f"invalid input: {type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_VALIDATION
    finally:
        if world is not None:
            world.database.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
