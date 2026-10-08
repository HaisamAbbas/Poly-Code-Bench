"""Application-level independent reviewer checks for bounded replacement work."""

from __future__ import annotations

from collections.abc import Sequence

from polycodebench_core.benchmark_audit_documents import EntityRef


def validate_replacement_plan_reviewers(
    *,
    plan_authors: Sequence[EntityRef],
    plan_checkers: Sequence[EntityRef],
    author: EntityRef,
    checker: EntityRef,
    reviewer: EntityRef,
) -> None:
    """Require each task to be checked by preregistered people other than its author."""
    author_id = author.entity_id
    checker_id = checker.entity_id
    reviewer_id = reviewer.entity_id
    allowed_authors = {item.entity_id for item in plan_authors}
    allowed_checkers = {item.entity_id for item in plan_checkers}
    if author_id not in allowed_authors or checker_id not in allowed_checkers:
        raise ValueError("replacement review participants were not preregistered")
    if len({author_id, checker_id, reviewer_id}) != 3:
        raise ValueError("replacement author, checker and reviewer must be independent")
