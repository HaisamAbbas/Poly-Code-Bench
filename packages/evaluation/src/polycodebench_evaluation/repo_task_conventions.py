"""Deterministic repository-convention analysis for repository tasks (Prompt 25, PCB-25-3).

The rules here are the machine-ownable share of a repository's written conventions: each one is a
plain AST property that can be checked identically on the frozen baseline snapshot and on the
candidate workspace. What they produce is *evidence*, not a verdict: every finding carries a
canonical issue key that is stable across baseline and candidate, so
``polycodebench_evaluation.evaluator.baseline_relations`` can classify it as introduced, worsened,
unchanged in a changed file, or unchanged legacy debt that is no candidate's fault.

Judgement-owned convention questions (whether a name communicates, whether duplication is
acceptable here) stay with the frozen judge rubric; these findings travel to the judge as
analyzer evidence spans and to the report as traceable rows.

Issue keys exclude line numbers on purpose: the same pre-existing defect keeps its identity
across an edit that moves it, so a moved-but-unfixed finding is still baseline debt rather than a
brand new candidate fault.
"""

from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from polycodebench_core.models import Observation

#: rule id -> (issue family, severity, description)
KNOWN_CONVENTION_RULES: dict[str, tuple[str, str, str]] = {
    "bare-except": (
        "convention",
        "medium",
        "a bare except swallows failures the repository's error contract declares",
    ),
    "mutable-default-arg": (
        "convention",
        "medium",
        "a mutable default argument leaks state between calls",
    ),
    "duplicated-block": (
        "duplication",
        "low",
        "one normalized statement block is duplicated at more than one location",
    ),
}

DUPLICATION_MIN_STATEMENTS = 5
_DIGEST_LEN = 16


@dataclass(frozen=True)
class ConventionFinding:
    rule_id: str
    family: str
    path: str
    symbol: str
    line: int
    severity: str
    detail: str
    issue_key: str


def _issue_key(family: str, anchor: str) -> str:
    digest = hashlib.sha256(anchor.encode("utf-8")).hexdigest()[:_DIGEST_LEN]
    return f"rt.{family}.{digest}"


def _block_fingerprint(body: list[ast.stmt]) -> str:
    dump = "|".join(ast.dump(statement, include_attributes=False) for statement in body)
    return hashlib.sha256(dump.encode("utf-8")).hexdigest()[:_DIGEST_LEN]


class _Collector(ast.NodeVisitor):
    def __init__(self, path: str, source: str) -> None:
        self._path = path
        self._lines = source.splitlines()
        self._symbols: list[str] = ["module"]
        self.findings: list[ConventionFinding] = []
        self.blocks: list[tuple[str, str, int, str]] = []

    def _symbol(self) -> str:
        return self._symbols[-1]

    def _emit(self, rule_id: str, line: int, symbol: str, anchor: str, detail: str) -> None:
        family, severity, _ = KNOWN_CONVENTION_RULES[rule_id]
        self.findings.append(
            ConventionFinding(
                rule_id=rule_id,
                family=family,
                path=self._path,
                symbol=symbol,
                line=line,
                severity=severity,
                detail=detail,
                issue_key=_issue_key(family, anchor),
            )
        )

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is None:
            self._emit(
                "bare-except",
                node.lineno,
                self._symbol(),
                f"{self._path}\x00bare-except\x00{self._symbol()}",
                f"bare except in {self._symbol()!r}",
            )
        self.generic_visit(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        defaults = list(node.args.defaults) + [
            default for default in node.args.kw_defaults if default is not None
        ]
        for default in defaults:
            if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                self._emit(
                    "mutable-default-arg",
                    node.lineno,
                    node.name,
                    f"{self._path}\x00mutable-default-arg\x00{node.name}",
                    f"{node.name!r} uses a mutable default argument",
                )
                break
        if len(node.body) >= DUPLICATION_MIN_STATEMENTS:
            self.blocks.append((self._path, node.name, node.lineno, _block_fingerprint(node.body)))
        self._symbols.append(node.name)
        self.generic_visit(node)
        self._symbols.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)


class ConventionAnalyzer:
    """Runs the declared convention rules over one workspace. Deterministic and offline."""

    def __init__(self, rule_ids: tuple[str, ...]) -> None:
        unknown = sorted(set(rule_ids) - set(KNOWN_CONVENTION_RULES))
        if unknown:
            raise ValueError(f"unknown convention rules: {unknown}")
        self._rule_ids = tuple(sorted(set(rule_ids)))

    @property
    def rule_ids(self) -> tuple[str, ...]:
        return self._rule_ids

    def analyze(self, files: Mapping[str, bytes]) -> tuple[ConventionFinding, ...]:
        findings: list[ConventionFinding] = []
        blocks: list[tuple[str, str, int, str]] = []
        for path in sorted(files):
            if not path.endswith(".py"):
                continue
            try:
                source = files[path].decode("utf-8")
                tree = ast.parse(source)
            except (UnicodeDecodeError, SyntaxError):
                # A workspace that will not parse is a candidate failure the acceptance gate
                # reports; the convention scan records nothing rather than inventing findings.
                continue
            collector = _Collector(path, source)
            collector.visit(tree)
            findings.extend(collector.findings)
            blocks.extend(collector.blocks)
        findings = [entry for entry in findings if entry.rule_id in self._rule_ids]
        if "duplicated-block" in self._rule_ids:
            findings.extend(self._duplicate_findings(blocks))
        return tuple(sorted(findings, key=lambda entry: (entry.issue_key, entry.path, entry.line)))

    @staticmethod
    def _duplicate_findings(
        blocks: list[tuple[str, str, int, str]],
    ) -> list[ConventionFinding]:
        by_fingerprint: dict[str, list[tuple[str, str, int]]] = {}
        for path, symbol, line, fingerprint in blocks:
            by_fingerprint.setdefault(fingerprint, []).append((path, symbol, line))
        findings: list[ConventionFinding] = []
        for fingerprint, locations in sorted(by_fingerprint.items()):
            if len(locations) < 2:
                continue
            family, severity, _ = KNOWN_CONVENTION_RULES["duplicated-block"]
            where = ", ".join(f"{path}:{symbol}" for path, symbol, _ in sorted(locations))
            for path, symbol, line in sorted(locations):
                findings.append(
                    ConventionFinding(
                        rule_id="duplicated-block",
                        family=family,
                        path=path,
                        symbol=symbol,
                        line=line,
                        severity=severity,
                        detail=f"block {fingerprint} duplicated at {where}",
                        issue_key=_issue_key("duplication", f"block\x00{fingerprint}"),
                    )
                )
        return findings


def findings_as_observations(
    findings: tuple[ConventionFinding, ...],
    *,
    tool_digest: str,
    candidate_digest: str,
) -> list[Observation]:
    """One observation per canonical issue key: several locations of one defect count once."""
    by_key: dict[str, ConventionFinding] = {}
    for finding in findings:
        by_key.setdefault(finding.issue_key, finding)
    observations: list[Observation] = []
    for key in sorted(by_key):
        finding = by_key[key]
        observations.append(
            Observation.model_validate_json(
                json.dumps(
                    {
                        "schema_version": 1,
                        "kind": "observation",
                        "check_id": f"repotask-convention.{finding.rule_id}",
                        "tool_digest": tool_digest,
                        "candidate_digest": candidate_digest,
                        "status": "measured",
                        "value": True,
                        "severity": finding.severity,
                        "confidence": "high",
                        "location": {
                            "schema_version": 1,
                            "kind": "source_location",
                            "path": finding.path,
                            "start_line": finding.line,
                            "end_line": finding.line,
                            "start_column": None,
                            "end_column": None,
                            "base_or_candidate_digest": candidate_digest,
                        },
                        "baseline_relation": None,
                        "issue_key": finding.issue_key,
                        "primary_owner": "code_quality",
                        "raw_artifact_ids": [],
                        "explanation": f"{finding.rule_id}: {finding.detail}",
                    }
                )
            )
        )
    return observations
