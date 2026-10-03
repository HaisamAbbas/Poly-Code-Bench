"""Repository-task pack tooling: digest, seal, admit and grade matrix (Prompt 25).

    python scripts/repo_task_tool.py digest taskpacks/repo-tasks/ini-interpolate
    python scripts/repo_task_tool.py seal   taskpacks/repo-tasks/ini-interpolate
    python scripts/repo_task_tool.py admit  taskpacks/repo-tasks/ini-interpolate --report ...
    python scripts/repo_task_tool.py matrix taskpacks/repo-tasks/ini-interpolate --report ...

Judge evidence produced here is **fixture judge evidence**: deterministic fixture votes parsed
through the real judge services (build_packet / parse_vote / aggregate) because the judge panel
is unprovisioned in this workspace (``config/judging/panel-v1.yaml``). Reports label it as such;
no live judge call is made or claimed.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[1]
for _extra in ("packages/core/src", "packages/scoring/src", "packages/services/src"):
    _path = str(ROOT / _extra)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from polycodebench_core.canonical import canonical_document_digest, sha256_bytes  # noqa: E402
from polycodebench_core.judge_contracts import (  # noqa: E402
    JudgementResult,
    JudgePacket,
    JudgeRubric,
)
from polycodebench_core.models import TaskOutputContract  # noqa: E402
from polycodebench_evaluation.evaluator import baseline_from_package  # noqa: E402
from polycodebench_evaluation.repo_task_admission import (  # noqa: E402
    admit_repo_task,
    variant_submission,
)
from polycodebench_evaluation.repo_task_grading import (  # noqa: E402
    RepoTaskGrade,
    frozen_task_for,
    grade_repo_task,
    judge_is_wanted,
)
from polycodebench_scoring.loader import (  # noqa: E402
    load_evidence_ownership,
    load_scoring_policy,
)
from polycodebench_scoring.manifest import ScoringInvocation  # noqa: E402
from polycodebench_services.judging import (  # noqa: E402
    aggregate,
    build_packet,
    load_panel,
    load_rubric,
    parse_vote,
)
from polycodebench_services.repo_tasks import (  # noqa: E402
    ImportedRepoTask,
    import_repo_task_package,
)

CONFIG = ROOT / "config"
RUBRIC_PATH = CONFIG / "judging" / "rubric-v1.yaml"
PANEL_PATH = CONFIG / "judging" / "panel-v1.yaml"
POLICY_PATH = CONFIG / "scoring" / "pilot-v1.yaml"
OWNERSHIP_PATH = CONFIG / "scoring" / "evidence_ownership.yaml"
FIXTURE_RATIONALE = "The cited span shows the behaviour the item asks for."


def load_pack(path: Path) -> tuple[ImportedRepoTask, JudgeRubric]:
    rubric = load_rubric(RUBRIC_PATH)
    return import_repo_task_package(path, rubric=rubric), rubric


def fixture_vote_text(packet: JudgePacket, scores: dict[str, str]) -> str:
    """One fixture judge response: every item scored and citing the first span."""
    anchor = packet.spans[0].anchor_id
    items = {
        item.item_id: {
            "score": scores.get(item.item_id, "1.000000"),
            "citations": [{"anchor_id": anchor, "note": "cited span"}],
            "rationale": FIXTURE_RATIONALE,
            "uncertainty": [],
            "facts": [],
        }
        for item in packet.items
    }
    return json.dumps({"packet_digest": packet.digest(), "items": items})


def fixture_judgement(
    grade: RepoTaskGrade,
    *,
    rubric: JudgeRubric,
    scores: dict[str, str],
) -> JudgementResult:
    """Three deterministic fixture votes over the real packet, aggregated by the real service."""
    if grade.judge_packet_input is None:
        raise ValueError("the grade carries no judge packet input")
    panel = load_panel(PANEL_PATH)
    packet = build_packet(rubric=rubric, panel=panel, packet_input=grade.judge_packet_input)
    votes = []
    deliveries = []
    from datetime import UTC, datetime

    created_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    for index in range(3):
        text = fixture_vote_text(packet, scores)
        vote = parse_vote(
            text=text,
            packet=packet,
            panel=panel,
            vote_index=index,
            seed=None,
            raw_response_digest=sha256_bytes(text.encode("utf-8")),
        )
        votes.append(vote)
        deliveries.append(
            {
                "packet_id": packet.packet_id,
                "vote_index": index,
                "delivery_index": 0,
                "status": "valid",
                "judge_revision": panel.judge_revision,
                "created_at": created_at,
            }
        )
    from polycodebench_core.judge_contracts import JudgeDelivery

    return aggregate(
        packet=packet,
        panel=panel,
        votes=votes,
        deliveries=[JudgeDelivery.model_validate(entry) for entry in deliveries],
    )


def _scorer_digest() -> str:
    import polycodebench_scoring.scorer as scorer_module

    return sha256_bytes(Path(scorer_module.__file__).read_bytes())


def _invocation() -> ScoringInvocation:
    from datetime import UTC, datetime

    return ScoringInvocation(
        run_id=str(uuid.uuid4()),
        candidate_id=str(uuid.uuid4()),
        recorded_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        scorer_digest=_scorer_digest(),
    )


def grade_with_fixture_judgement(
    pack: ImportedRepoTask,
    rubric: JudgeRubric,
    *,
    submission_kind: Literal["source_bundle", "unified_diff"],
    payload: Any,
    scores: dict[str, str],
    label: str,
) -> dict[str, Any]:
    """One matrix row: grade, fixture judge through the real services, real scorecard."""
    package = pack.imported.manifest
    baseline = baseline_from_package({**pack.visible_files, **pack.hidden_files})
    first = grade_repo_task(
        authoring=pack.authoring,
        binding=pack.binding,
        rubric=rubric,
        package=package,
        package_digest=pack.imported.package_digest,
        baseline_files=baseline,
        hidden_files=pack.hidden_files,
        submission_kind=submission_kind,
        payload=payload,
    )
    judgement = (
        fixture_judgement(first, rubric=rubric, scores=scores)
        if first.judge_packet_input is not None
        else None
    )
    grade = grade_repo_task(
        authoring=pack.authoring,
        binding=pack.binding,
        rubric=rubric,
        package=package,
        package_digest=pack.imported.package_digest,
        baseline_files=baseline,
        hidden_files=pack.hidden_files,
        submission_kind=submission_kind,
        payload=payload,
        judgement=judgement,
    )
    policy = load_scoring_policy(POLICY_PATH)
    ownership = load_evidence_ownership(OWNERSHIP_PATH)
    task = frozen_task_for(
        package,
        task_digest=pack.imported.package_digest,
        inventory_digest=pack.binding.hidden_case_inventory_digest,
    )
    outcome = grade.to_scorecard(
        task=task, policy=policy, ownership=ownership, invocation=_invocation()
    )
    scorecard = outcome.scorecard
    judgement_row = None
    if judgement is not None:
        judgement_row = {
            "status": judgement.status,
            "packet_id": judgement.packet_id,
            "rubric_digest": judgement.rubric_digest,
            "items": {
                item.item_id: {"status": item.status, "mean_score": item.mean_score}
                for item in judgement.items
            },
        }
    return {
        "label": label,
        "judge_evidence_class": "fixture_judge_votes",
        "judge_is_wanted_before_dispatch": judge_is_wanted(first),
        "grade": grade.report(),
        "judgement": judgement_row,
        "scorecard": {
            "gate": scorecard.gate.value,
            "status": scorecard.status.value,
            "total_score": scorecard.total_score,
            "contributions": {
                f"{item.dimension.value}/{item.item_id}": item.contribution
                for item in scorecard.items
            },
        },
    }


def command_digest(path: Path) -> int:
    import yaml

    document = yaml.safe_load((path / "manifest.yaml").read_text(encoding="utf-8"))
    contract = TaskOutputContract.model_validate(document["output_contract"])
    print(canonical_document_digest(contract))
    return 0


def command_seal(path: Path) -> int:
    pack, rubric = load_pack(path)
    print(json.dumps(pack.binding.model_dump(mode="json"), indent=2, sort_keys=True))
    return 0


def command_admit(path: Path, report: Path | None) -> int:
    pack, rubric = load_pack(path)
    result = admit_repo_task(pack=pack, rubric=rubric)
    document = result.report()
    if report is not None:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "task_id": result.task_id,
                "admitted": result.admitted,
                "checks": document["checks"],
            },
            indent=2,
        )
    )
    return 0 if result.admitted else 1


def command_matrix(path: Path, report: Path | None) -> int:
    pack, rubric = load_pack(path)
    package_files = {**pack.visible_files, **pack.hidden_files}
    rows = []
    all_scores = "1.000000"
    weak_scores = {
        "duplication": "0.000000",
        "repository_style_consistency": "0.000000",
    }
    for fixture in pack.imported.manifest.fixtures:
        submission_kind, payload = variant_submission(package_files, fixture.name)
        scores = weak_scores if fixture.variant == "quality_defective" else {}
        rows.append(
            grade_with_fixture_judgement(
                pack,
                rubric,
                submission_kind=submission_kind,
                payload=payload,
                scores={**{item: all_scores for item in rubric_item_ids(rubric)}, **scores},
                label=f"{fixture.name}:{submission_kind}",
            )
        )
    # One patch-artifact row: the reference solution re-submitted as a unified diff over the
    # baseline, to show patch and workspace artifacts grade identically.
    reference = next(f for f in pack.imported.manifest.fixtures if f.variant == "reference")
    _, overlay = variant_submission(package_files, reference.name)
    baseline = baseline_from_package(package_files)
    assert isinstance(overlay, dict)
    patch = diff_against(baseline, overlay)
    rows.append(
        grade_with_fixture_judgement(
            pack,
            rubric,
            submission_kind="unified_diff",
            payload=patch,
            scores={item: all_scores for item in rubric_item_ids(rubric)},
            label=f"{reference.name}:unified_diff",
        )
    )
    document = {
        "task_id": pack.authoring.task_id,
        "package_digest": pack.imported.package_digest,
        "contract_digest": pack.binding.contract_digest,
        "execution_tier": "local_fixture",
        "judge_evidence_class": "fixture_judge_votes",
        "judge_note": (
            "Judge evidence is deterministic fixture votes through the real judge services; "
            "the judge panel is unprovisioned (config/judging/panel-v1.yaml) and no live judge "
            "call was made."
        ),
        "rows": rows,
    }
    if report is not None:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
    for row in rows:
        print(
            json.dumps(
                {
                    "label": row["label"],
                    "gate": row["grade"]["gate"]["status"],
                    "total": row["scorecard"]["total_score"],
                    "failed_mandatory": row["grade"]["failed_mandatory_criteria"],
                }
            )
        )
    return 0


def rubric_item_ids(rubric: JudgeRubric) -> list[str]:
    return [item.item_id for item in rubric.items if item.dimension.value == "code_quality"]


def diff_against(baseline: dict[str, bytes], overlay: dict[str, bytes]) -> str:
    """A unified diff from the baseline to the overlay workspace (patch-artifact submissions)."""
    from polycodebench_evaluation.repo_task_grading import assemble_workspace

    workspace = assemble_workspace(
        baseline,
        submission_kind="source_bundle",
        payload=overlay,
        allowed_paths=sorted(overlay),
        protected_paths=(),
    )
    chunks = []
    for path in sorted(workspace.changed_files):
        old = baseline.get(path, b"").decode("utf-8").splitlines(keepends=True)
        new = workspace.files[path].decode("utf-8").splitlines(keepends=True)
        chunks.extend(
            difflib.unified_diff(old, new, fromfile=f"a/{path}", tofile=f"b/{path}")
        )
    return "".join(chunks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("digest", "seal"):
        entry = sub.add_parser(name)
        entry.add_argument("pack", type=Path)
    for name in ("admit", "matrix"):
        entry = sub.add_parser(name)
        entry.add_argument("pack", type=Path)
        entry.add_argument("--report", type=Path)
    arguments = parser.parse_args(argv)
    if arguments.command == "digest":
        return command_digest(arguments.pack)
    if arguments.command == "seal":
        return command_seal(arguments.pack)
    if arguments.command == "admit":
        return command_admit(arguments.pack, arguments.report)
    return command_matrix(arguments.pack, arguments.report)


if __name__ == "__main__":
    raise SystemExit(main())
