"""Consistency check for the persistent implementation ledgers."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs" / "implementation"


def rows(path: Path, pattern: str) -> list[str]:
    return re.findall(pattern, path.read_text(encoding="utf-8"), re.MULTILINE)


requirements = rows(DOCS / "requirements-matrix.md", r"^\| (REQ-\d+) \|")
work_packages = rows(DOCS / "requirements-matrix.md", r"^\| (WP-\d+) \|")
e2e = rows(DOCS / "e2e-matrix.md", r"^\| (E2E-\d+) \|")
tickets = rows(DOCS / "tickets.md", r"^## (PCB-\d{2}-\d) ")
ticket_text = (DOCS / "tickets.md").read_text(encoding="utf-8")
assert len(requirements) == len(set(requirements)) == 14, requirements
assert len(work_packages) == len(set(work_packages)) == 24, work_packages
assert len(e2e) == len(set(e2e)) == 43, e2e
assert len(tickets) == len(set(tickets)) == 142, tickets
assert sorted({ticket[:6] for ticket in tickets}) == [f"PCB-{i:02d}" for i in range(35)]
assert ticket_text.count("- Owner prompt:") == len(tickets)
assert ticket_text.count("- Dependencies:") == len(tickets)

for line in (DOCS / "e2e-matrix.md").read_text(encoding="utf-8").splitlines():
    if line.startswith("| E2E-"):
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        assert cells[3] and re.search(r"\b\d{2}\b", cells[3]), line
        assert cells[4] and cells[5] in {"not_run", "passed", "failed", "blocked"}, line

progress = json.loads((DOCS / "progress.json").read_text(encoding="utf-8"))
assert len(progress["prompt_statuses"]) == 35
last_completed = int(progress["last_completed_prompt"])
active = int(progress["active_prompt"])
assert progress["prompt_statuses"][f"{last_completed:02d}"] == "done"
assert active == last_completed + 1
assert progress["prompt_statuses"][f"{active:02d}"] in {"not_started", "in_progress", "partial"}
assert all(progress["prompt_statuses"][f"{i:02d}"] == "not_started" for i in range(active + 1, 35))
assert progress["evidence_summary"]["requirements_registered"] == 14
assert progress["evidence_summary"]["work_packages_registered"] == 24
assert progress["evidence_summary"]["e2e_scenarios_registered"] == 43
assert progress["evidence_summary"]["prompts_registered"] == 35
assert progress["evidence_summary"]["pcb_tickets_registered"] == len(tickets)

manifest = json.loads((DOCS / "source-manifest.json").read_text(encoding="utf-8"))
for source in manifest["authoritative_sources"]:
    actual = hashlib.sha256(Path(source["path"]).read_bytes()).hexdigest()
    assert actual == source["sha256"], source["workspace_path"]
    if source["pack_expected_sha256"]:
        assert actual == source["pack_expected_sha256"], source["workspace_path"]

print(
    "PASS: 14 REQ, 24 WP, 43 E2E, Prompts 00-34, 142 PCB tickets, "
    "owners/evidence, progress, and source hashes"
)
