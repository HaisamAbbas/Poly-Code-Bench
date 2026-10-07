"""Create a clearly synthetic, locally signed release for public UI development and tests.

This helper is never called by the HTTP service. Its values exercise rendering states only and
must not be cited as benchmark measurements.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from polycodebench_publication.aggregation import MetricDefinition
from polycodebench_publication.projections import (
    ArtifactRef,
    ContributionRow,
    Coverage,
    DimensionBreakdown,
    Methodology,
    PublicMetric,
    PublicScorecard,
    PublicSourceVersion,
    PublicSubmittedPatch,
    PublicTask,
    PublicTaskContent,
    PublicToolFinding,
)
from polycodebench_publication.projections_query import (
    ReleaseContent,
    ReleaseEntry,
    ReleaseLanguageProfile,
)
from polycodebench_publication.releases import (
    REQUIRED_CHECKS,
    ReleasePrincipal,
    ReleaseStore,
    SigningKey,
    ValidationEvidence,
    digest,
    validate_projection,
)


def _metric(
    metric_id: str,
    label: str,
    value: str | None,
    *,
    status: str = "measured",
    unit: str = "score",
    low: str | None = None,
    high: str | None = None,
    reason: str | None = None,
) -> PublicMetric:
    return PublicMetric(
        metric_id=metric_id,
        label=label,
        unit=unit,
        direction="higher",
        status=status,
        value=value,
        interval_low=low,
        interval_high=high,
        coverage="1.000000" if status == "measured" else None,
        conditional_on_pass=False,
        reason=reason,
    )


def _dimension(
    name: str,
    value: str | None,
    *,
    status: str = "measured",
    reason: str | None = None,
    applicable_tasks: int = 1,
    opportunity_count: int = 1,
) -> DimensionBreakdown:
    metric_id = "dimension_" + name
    return DimensionBreakdown(
        dimension=name,
        metric=_metric(
            metric_id,
            name.replace("_", " ").title(),
            value,
            status=status,
            reason=reason,
        ),
        applicable_tasks=applicable_tasks,
        opportunity_count=opportunity_count,
    )


def _scorecard_metrics() -> tuple[PublicMetric, ...]:
    return (
        _metric(
            "code_score",
            "Code score",
            "89.750000",
            low="81.000000",
            high="96.000000",
            reason="Synthetic UI fixture interval; not a benchmark estimate.",
        ),
        _metric("pass_rate", "Pass rate", "0.670000", unit="ratio"),
        _metric(
            "gated_repair_score",
            "Repair score",
            None,
            status="gated_zero",
            reason="Synthetic example: correctness gate failed.",
        ),
        _metric(
            "not_applicable_example",
            "Concurrency diagnostic",
            None,
            status="not_applicable",
            reason="The synthetic task declared no concurrent workload.",
        ),
        _metric(
            "missing_example",
            "Dependency scan",
            None,
            status="missing",
            reason="No scan result was included in this UI fixture.",
        ),
        _metric(
            "pending_review_example",
            "Maintainability review",
            None,
            status="needs_review",
            reason="Synthetic example: reviewer decision is pending.",
        ),
    )


def _definitions() -> tuple[MetricDefinition, ...]:
    dimensions = (
        "correctness",
        "security",
        "efficiency",
        "code_quality",
        "idiomatic",
        "robustness",
    )
    return (
        MetricDefinition(
            metric_id="code_score",
            label="Code score",
            unit="score",
            domain=("0", "100"),
            uncertainty_method="synthetic-ui-fixture-only",
        ),
        MetricDefinition(metric_id="pass_rate", label="Pass rate", unit="ratio", domain=("0", "1")),
        MetricDefinition(
            metric_id="gated_repair_score",
            label="Repair score",
            unit="score",
            domain=("0", "100"),
        ),
        *(
            MetricDefinition(
                metric_id=f"dimension_{dimension}",
                label=dimension.replace("_", " ").title(),
                unit="score",
                domain=("0", "100"),
                uncertainty_method="synthetic-ui-fixture-only",
            )
            for dimension in dimensions
        ),
        MetricDefinition(
            metric_id="answer_accuracy",
            label="Answer accuracy",
            unit="score",
            domain=("0", "100"),
            uncertainty_method="synthetic-ui-fixture-only",
        ),
    )


def _entries() -> tuple[ReleaseEntry, ...]:
    metrics = _scorecard_metrics()
    dimensions = (
        _dimension("correctness", "89.750000"),
        _dimension(
            "security",
            None,
            status="gated_zero",
            reason="Synthetic example: required correctness gate failed.",
        ),
        _dimension(
            "efficiency",
            None,
            status="not_applicable",
            reason="No performance workload was declared.",
            applicable_tasks=0,
            opportunity_count=0,
        ),
        _dimension("code_quality", "76.000000"),
        _dimension(
            "idiomatic",
            None,
            status="needs_review",
            reason="Synthetic example: reviewer decision is pending.",
        ),
        _dimension(
            "robustness",
            None,
            status="missing",
            reason="No robustness observation was included.",
        ),
    )
    alpha_languages = (
        ReleaseLanguageProfile(
            language_id="python",
            evidence_url="/v1/scorecards/synthetic-scorecard-a",
            dimensions=(
                _dimension("correctness", "92.000000"),
                _dimension("code_quality", "76.000000"),
            ),
            diagnostics=(
                _dimension("ruff", "88.000000"),
                _dimension(
                    "dependency_audit",
                    None,
                    status="missing",
                    reason="No audit result was included.",
                ),
            ),
            tool_coverage=(("ruff", "1 measured opportunity"), ("dependency audit", "missing")),
        ),
        ReleaseLanguageProfile(
            language_id="rust",
            evidence_url="/v1/scorecards/synthetic-scorecard-a-rust",
            dimensions=(_dimension("correctness", "82.000000"),),
            diagnostics=(_dimension("clippy", "73.000000"),),
            tool_coverage=(("clippy", "1 measured opportunity"),),
        ),
        ReleaseLanguageProfile(
            language_id="javascript",
            evidence_url="/v1/scorecards/synthetic-scorecard-a-javascript",
            dimensions=(_dimension("correctness", "74.000000"),),
            diagnostics=(_dimension("eslint", "80.000000"),),
            tool_coverage=(("eslint", "1 measured opportunity"),),
        ),
    )
    alpha_id, beta_id, candidate_id, answer_id = (
        "synthetic-code-a",
        "synthetic-code-b",
        "synthetic-code-c",
        "synthetic-answer-only",
    )
    alpha_scorecard = "synthetic-scorecard-a"
    beta_scorecard = "synthetic-scorecard-b"
    candidate_scorecard = "synthetic-scorecard-c"
    answer_scorecard = "synthetic-scorecard-answer"
    coverage = Coverage(tasks=3, samples=9, independent_clusters=3)
    return (
        ReleaseEntry(
            model_config_id=alpha_id,
            label="Fixture Code System A",
            capabilities=("code_generation",),
            languages=("python", "rust", "javascript"),
            metrics=metrics,
            coverage=coverage,
            generation_cost_micros="123456",
            latency_ms_p50=820,
            latency_ms_p95=1430,
            dimensions=dimensions,
            language_profiles=alpha_languages,
            run_mode="single_shot",
            budget_profile_id="synthetic-small-budget",
            evidence_url=f"/v1/scorecards/{alpha_scorecard}",
        ),
        ReleaseEntry(
            model_config_id=beta_id,
            label="Fixture Code System B",
            capabilities=("code_generation",),
            languages=("python",),
            metrics=(
                _metric(
                    "code_score",
                    "Code score",
                    "75.000000",
                    low="68.000000",
                    high="83.000000",
                    reason="Synthetic UI fixture interval; not a benchmark estimate.",
                ),
                _metric("pass_rate", "Pass rate", "0.500000", unit="ratio"),
            ),
            coverage=Coverage(tasks=1, samples=2, independent_clusters=1),
            generation_cost_micros="234567",
            latency_ms_p50=1110,
            latency_ms_p95=1950,
            dimensions=(_dimension("correctness", "75.000000"),),
            language_profiles=(
                ReleaseLanguageProfile(
                    language_id="python",
                    evidence_url="/v1/scorecards/synthetic-scorecard-b",
                    dimensions=(_dimension("correctness", "75.000000"),),
                    diagnostics=(_dimension("ruff", "65.000000"),),
                    tool_coverage=(("ruff", "1 measured opportunity"),),
                ),
            ),
            run_mode="standard_agent",
            budget_profile_id="synthetic-medium-budget",
            evidence_url=f"/v1/scorecards/{beta_scorecard}",
        ),
        ReleaseEntry(
            model_config_id=candidate_id,
            label="Fixture Code System C",
            capabilities=("code_generation",),
            languages=("python", "rust", "javascript"),
            metrics=(
                _metric(
                    "code_score",
                    "Code score",
                    "84.000000",
                    low="79.000000",
                    high="90.000000",
                    reason="Synthetic UI fixture interval; not a benchmark estimate.",
                ),
                _metric("pass_rate", "Pass rate", "0.780000", unit="ratio"),
            ),
            coverage=coverage,
            generation_cost_micros="198765",
            latency_ms_p50=940,
            latency_ms_p95=1610,
            dimensions=(_dimension("correctness", "84.000000"),),
            language_profiles=tuple(
                ReleaseLanguageProfile(
                    language_id=language,
                    evidence_url=f"/v1/scorecards/synthetic-scorecard-c{scorecard_suffix}",
                    dimensions=(_dimension("correctness", value),),
                    diagnostics=(_dimension(tool, tool_value),),
                    tool_coverage=((tool, "1 measured opportunity"),),
                )
                for language, value, tool, tool_value, scorecard_suffix in (
                    ("python", "86.000000", "ruff", "81.000000", ""),
                    ("rust", "85.000000", "clippy", "78.000000", "-rust"),
                    ("javascript", "79.000000", "eslint", "82.000000", "-javascript"),
                )
            ),
            run_mode="single_shot",
            budget_profile_id="synthetic-small-budget",
            evidence_url=f"/v1/scorecards/{candidate_scorecard}",
        ),
        ReleaseEntry(
            model_config_id=answer_id,
            label="Fixture Answer Only System",
            capabilities=("repository_question_answering",),
            languages=("python",),
            metrics=(_metric("answer_accuracy", "Answer accuracy", "84.000000"),),
            coverage=coverage,
            generation_cost_micros=None,
            latency_ms_p50=None,
            latency_ms_p95=None,
            dimensions=(),
            language_profiles=(),
            run_mode="single_shot",
            budget_profile_id="synthetic-small-budget",
            evidence_url=f"/v1/scorecards/{answer_scorecard}",
        ),
    )


def _tasks() -> tuple[PublicTask, ...]:
    code_and_qa = (
        PublicTask(
            task_id="synthetic-task-example",
            version=1,
            disclosed=True,
            language_id="python",
            family="algorithmic",
            difficulty="moderate",
            statement_summary="Implement a stable grouping helper over a sequence of records.",
            limitations=("Synthetic task description; no benchmark results are represented.",),
            evidence_url="/v1/tasks/synthetic-task-example",
        ),
        PublicTask(
            task_id="synthetic-task-rust",
            version=1,
            disclosed=True,
            language_id="rust",
            family="algorithmic",
            difficulty="moderate",
            statement_summary="Implement a bounded parser over public input records.",
            limitations=("Synthetic task description; no benchmark results are represented.",),
            evidence_url="/v1/tasks/synthetic-task-rust",
        ),
        PublicTask(
            task_id="synthetic-task-javascript",
            version=1,
            disclosed=True,
            language_id="javascript",
            family="algorithmic",
            difficulty="moderate",
            statement_summary="Implement a deterministic reducer with explicit edge cases.",
            limitations=("Synthetic task description; no benchmark results are represented.",),
            evidence_url="/v1/tasks/synthetic-task-javascript",
        ),
        PublicTask(
            task_id="synthetic-qa-task",
            version=1,
            disclosed=True,
            language_id="python",
            family="repository_question_answering",
            difficulty="introductory",
            statement_summary="Answer a question using only the disclosed repository facts.",
            limitations=("Synthetic task description; no benchmark results are represented.",),
            evidence_url="/v1/tasks/synthetic-qa-task",
        ),
    )
    pagination_fixtures = tuple(
        PublicTask(
            task_id=f"synthetic-browse-task-{index:03d}",
            version=1,
            disclosed=True,
            language_id="python",
            family="pagination-fixture",
            difficulty="introductory",
            statement_summary=(
                f"Synthetic task-list pagination record {index:03d}; no model scorecards or "
                "evaluation results are attached."
            ),
            limitations=("Synthetic UI pagination fixture; not an evaluated benchmark task.",),
            evidence_url=f"/v1/tasks/synthetic-browse-task-{index:03d}",
        )
        for index in range(1, 61)
    )
    return code_and_qa + pagination_fixtures


def _scorecards(release_id: str, policy_digest: str) -> tuple[PublicScorecard, ...]:
    rows: list[PublicScorecard] = []
    code_task_scores = {
        "synthetic-task-example": ("92.000000", "80.000000"),
        "synthetic-task-rust": ("82.000000", "85.000000"),
        "synthetic-task-javascript": ("74.000000", "79.000000"),
    }
    specs: list[tuple[str, str, str, str, tuple[PublicMetric, ...]]] = []
    for task_id, (alpha_value, candidate_value) in code_task_scores.items():
        alpha_suffix = {
            "synthetic-task-example": "",
            "synthetic-task-rust": "-rust",
            "synthetic-task-javascript": "-javascript",
        }[task_id]
        specs.append(
            (
                "synthetic-code-a",
                f"synthetic-scorecard-a{alpha_suffix}",
                task_id,
                alpha_value,
                (
                    _metric(
                        "code_score",
                        "Code score",
                        alpha_value,
                        low=f"{float(alpha_value) - 5:.6f}",
                        high=f"{float(alpha_value) + 5:.6f}",
                    ),
                    _metric("pass_rate", "Pass rate", "0.670000", unit="ratio"),
                ),
            )
        )
        specs.append(
            (
                "synthetic-code-c",
                f"synthetic-scorecard-c{alpha_suffix}",
                task_id,
                candidate_value,
                (
                    _metric(
                        "code_score",
                        "Code score",
                        candidate_value,
                        low=f"{float(candidate_value) - 4:.6f}",
                        high=f"{float(candidate_value) + 4:.6f}",
                    ),
                    _metric("pass_rate", "Pass rate", "0.780000", unit="ratio"),
                ),
            )
        )
    specs.extend(
        (
            (
                "synthetic-code-b",
                "synthetic-scorecard-b",
                "synthetic-task-example",
                "75.000000",
                (
                    _metric(
                        "code_score", "Code score", "75.000000", low="68.000000", high="83.000000"
                    ),
                    _metric("pass_rate", "Pass rate", "0.500000", unit="ratio"),
                ),
            ),
            (
                "synthetic-answer-only",
                "synthetic-scorecard-answer",
                "synthetic-qa-task",
                "84.000000",
                (_metric("answer_accuracy", "Answer accuracy", "84.000000"),),
            ),
        )
    )
    for model_id, card_id, task_id, value, metrics in specs:
        evidence_refs = ()
        if card_id == "synthetic-scorecard-a":
            evidence_refs = (
                "synthetic-source-python-v1",
                "synthetic-patch-a-python",
                "synthetic-finding-a-python",
                "private/oracle-review.json",
            )
        suffix_contrib = (
            card_id.rsplit("-", 1)[-1]
            if card_id.rsplit("-", 1)[-1] in {"rust", "javascript"}
            else "python"
        )
        if task_id == "synthetic-qa-task":
            contributions = ()
        else:
            contributions = (
                ContributionRow(
                    item_id=f"synthetic-{suffix_contrib}-item",
                    dimension="correctness",
                    nominal_weight_bp=10_000,
                    effective_weight_bp=10_000,
                    presentation_weight_bp=10_000,
                    arithmetic="synthetic raw score × 10000 / 10000; fixture display only",
                    evidence_refs=evidence_refs,
                    value=value,
                ),
            )
        published_metrics = metrics
        if model_id == "synthetic-code-a" and task_id == "synthetic-task-example":
            published_metrics = (*metrics, *_scorecard_metrics()[2:])
        rows.append(
            PublicScorecard(
                scorecard_id=card_id,
                release_id=release_id,
                model_config_id=model_id,
                task_id=task_id,
                formula_version="synthetic-ui-fixture-v1",
                policy_digest=policy_digest,
                gating_status="scored",
                metrics=published_metrics,
                contributions=contributions,
                evidence_url=f"/v1/scorecards/{card_id}",
            )
        )
    return tuple(rows)


def _task_contents() -> tuple[PublicTaskContent, ...]:
    code_tasks = (
        ("synthetic-task-example", "python", "src/grouping.py"),
        ("synthetic-task-rust", "rust", "src/parser.rs"),
        ("synthetic-task-javascript", "javascript", "src/reducer.js"),
    )
    details = []
    for task_id, language, path in code_tasks:
        source_id = f"synthetic-source-{language}-v1"
        suffix = "" if task_id == "synthetic-task-example" else f"-{language}"
        details.append(
            PublicTaskContent(
                task_id=task_id,
                task_version=1,
                statement=(
                    f"Implement the public {language} task with deterministic output and "
                    "explicit edge handling."
                ),
                source_versions=(
                    PublicSourceVersion(
                        source_id=source_id,
                        version_label="source-v1",
                        path=path,
                        language_id=language,
                        source_text=(
                            "# Synthetic public source\ndef solve(records):\n"
                            "    return list(records)\n"
                        ),
                    ),
                ),
                submitted_patches=(
                    PublicSubmittedPatch(
                        patch_id=f"synthetic-patch-a-{language}",
                        model_config_id="synthetic-code-a",
                        scorecard_id=f"synthetic-scorecard-a{suffix}",
                        summary="Authored development fixture patch",
                        diff_text=(
                            "--- a/source\n+++ b/source\n+<script>alert('synthetic')</script>\n"
                        ),
                    ),
                    PublicSubmittedPatch(
                        patch_id=f"synthetic-patch-c-{language}",
                        model_config_id="synthetic-code-c",
                        scorecard_id=f"synthetic-scorecard-c{suffix}",
                        summary="Authored development fixture patch",
                        diff_text="--- a/source\n+++ b/source\n+return records.slice()\n",
                    ),
                ),
                tool_findings=(
                    PublicToolFinding(
                        finding_id=f"synthetic-finding-a-{language}",
                        model_config_id="synthetic-code-a",
                        scorecard_id=f"synthetic-scorecard-a{suffix}",
                        source_id=source_id,
                        tool_id={"python": "ruff", "rust": "clippy", "javascript": "eslint"}[
                            language
                        ],
                        rule_id="synthetic-example-rule",
                        severity="info",
                        line=2,
                        message="Synthetic informational finding for public workflow tests.",
                    ),
                ),
            )
        )
    details.append(
        PublicTaskContent(
            task_id="synthetic-qa-task",
            task_version=1,
            statement="Answer from the disclosed facts and cite the supporting source span.",
            source_versions=(
                PublicSourceVersion(
                    source_id="synthetic-source-qa-v1",
                    version_label="facts-v1",
                    path="docs/facts.md",
                    language_id="text",
                    source_text="Synthetic fact: the fixture uses a stable public identifier.\n",
                ),
            ),
            submitted_patches=(),
            tool_findings=(),
        )
    )
    return tuple(details)


def create_synthetic_release(
    store: ReleaseStore,
    signer: SigningKey | None = None,
    *,
    artifacts: tuple[ArtifactRef, ...] = (),
) -> str:
    """Publish a generated fixture through the real local release lifecycle."""
    principal = ReleasePrincipal(subject_id="local-ui-fixture", roles=frozenset({"administrator"}))
    policy_digest = digest({"policy": "synthetic-ui-fixture-v1"})
    draft_id = str(uuid4())
    content = ReleaseContent(
        policy_digest=policy_digest,
        formula_version="synthetic-ui-fixture-v1",
        entries=_entries(),
        disclosed_tasks=_tasks(),
        task_contents=_task_contents(),
        scorecards=_scorecards(draft_id, policy_digest),
        artifacts=artifacts,
        methodology=Methodology(
            version="synthetic-ui-fixture-v1",
            methods=("Values are authored only to exercise public page states.",),
            formulas=(
                "Synthetic fixture formula: raw task value × effective weight / "
                "total effective weight.",
            ),
            tools=(
                "Fixture-only Ruff, Clippy and ESLint labels; no tool was executed "
                "for this release.",
            ),
            deviations=("All values and task contents are authored display fixtures.",),
            native_benchmarks=(
                ("native", "Synthetic display label only; this release contains no native result."),
                (
                    "adapted",
                    "Synthetic display label only; this release contains no adapted result.",
                ),
            ),
            correction_history=(
                "Any successor or withdrawal is recorded in this immutable release notice; "
                "this authored fixture claims no metric correction.",
            ),
            limitations=("Synthetic development data; not live benchmark results.",),
        ),
        metric_definitions=_definitions(),
    )
    projection: dict[str, Any] = {
        "schema_version": 1,
        "fixture_kind": "synthetic_internal",
        "scope": "exploratory",
        "cohort_digest": digest({"fixture": draft_id, "kind": "synthetic-ui"}),
        "limitations": [
            "Synthetic development data for UI testing only; no benchmark measurements "
            "or model evaluations.",
            "Confidence interval values are authored display fixtures and are not "
            "statistical estimates.",
        ],
        "metrics": [
            {
                "metric_id": "code_score",
                "value": "89.750000",
                "coverage": "1.000000",
                "interval_low": "81.000000",
                "interval_high": "96.000000",
                "conditional_on_pass": False,
            }
        ],
    }
    validate_projection(projection)
    content_doc = content.model_dump(mode="json")
    draft = store.draft(principal, content_doc, projection, f"draft-{draft_id}")
    release_id = str(draft["id"])
    version = int(draft["version"])
    content = content.model_copy(
        update={
            "scorecards": tuple(
                card.model_copy(update={"release_id": release_id}) for card in content.scorecards
            )
        }
    )
    draft = store.update(
        principal,
        release_id,
        content.model_dump(mode="json"),
        projection,
        version,
        f"attach-release-id-{draft_id}",
    )
    version = int(draft["version"])
    evidence = tuple(
        ValidationEvidence(
            check=check,
            subject_digest=str(draft["content_digest"]),
            expected_digest=str(draft["content_digest"]),
            observed_digest=str(draft["content_digest"]),
            reference="synthetic-ui-fixture-generator",
        )
        for check in sorted(REQUIRED_CHECKS)
    )
    validated = store.validate(principal, release_id, evidence, version, f"validate-{draft_id}")
    reviewed = store.review(
        principal,
        release_id,
        "Synthetic UI fixture; not benchmark data.",
        int(validated["version"]),
        f"review-{draft_id}",
    )
    approved = store.approve(
        principal,
        release_id,
        str(reviewed["content_digest"]),
        int(reviewed["version"]),
        f"approve-{draft_id}",
    )
    pointer = store.current()
    store.publish(
        principal,
        release_id,
        signer or SigningKey("synthetic-ui-fixture-key", Ed25519PrivateKey.generate()),
        int(pointer["generation"]),
        int(approved["version"]),
        f"publish-{draft_id}",
    )
    return release_id


def _persistent_fixture_signer(private_key_path: Path, keyring_path: Path) -> SigningKey:
    """Load or create an ignored local-only fixture key and its public verification keyring."""
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        load_pem_private_key,
    )
    from polycodebench_publication.keyring import Keyring, public_key_b64

    key_id = "synthetic-local-fixture"
    private_key_path.parent.mkdir(parents=True, exist_ok=True)
    if not private_key_path.exists():
        generated = Ed25519PrivateKey.generate()
        private_bytes = generated.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        try:
            descriptor = os.open(private_key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "wb") as output:
                output.write(private_bytes)
            try:
                os.chmod(private_key_path, 0o600)
            except OSError:
                # Windows ACLs inherit from the ignored .cache directory; the key is still never
                # written into the tracked source tree.
                pass
    loaded = load_pem_private_key(private_key_path.read_bytes(), password=None)
    if not isinstance(loaded, Ed25519PrivateKey):
        raise RuntimeError("local synthetic release key is not Ed25519")
    signer = SigningKey(key_id, loaded)

    keyring = (
        Keyring.from_document(json.loads(keyring_path.read_text(encoding="utf-8")))
        if keyring_path.exists()
        else Keyring()
    )
    entry = next((item for item in keyring.entries if item.key_id == key_id), None)
    if entry is not None:
        if entry.state != "active" or entry.public_key_b64 != public_key_b64(signer):
            raise RuntimeError("local synthetic signer does not match its active keyring entry")
    else:
        keyring = keyring.rotate(signer, at=datetime.now(UTC).isoformat(timespec="seconds"))
        keyring_path.parent.mkdir(parents=True, exist_ok=True)
        keyring_path.write_text(json.dumps(keyring.document(), indent=2) + "\n", encoding="utf-8")
    return signer


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--signing-key", type=Path)
    parser.add_argument("--keyring", type=Path)
    arguments = parser.parse_args()
    if arguments.count < 1 or arguments.count > 5:
        parser.error("--count must be between 1 and 5")
    if (arguments.signing_key is None) != (arguments.keyring is None):
        parser.error("--signing-key and --keyring must be provided together")
    arguments.store.parent.mkdir(parents=True, exist_ok=True)
    store = ReleaseStore(arguments.store)
    signer = (
        _persistent_fixture_signer(arguments.signing_key, arguments.keyring)
        if arguments.signing_key is not None and arguments.keyring is not None
        else None
    )
    release_ids = tuple(create_synthetic_release(store, signer) for _ in range(arguments.count))
    if len(release_ids) > 1:
        predecessor = store.get(release_ids[0])
        pointer = store.current()
        store.withdraw(
            ReleasePrincipal(subject_id="local-ui-fixture", roles=frozenset({"administrator"})),
            release_ids[0],
            "Synthetic fixture release superseded for historical navigation tests.",
            int(predecessor["version"]),
            int(pointer["generation"]),
            f"withdraw-{release_ids[0]}",
            replacement_release_id=release_ids[-1],
        )
    print(f"Created synthetic development releases {', '.join(release_ids)} in {arguments.store}.")
    print("It is UI test data, not a benchmark result.")


if __name__ == "__main__":
    main()
