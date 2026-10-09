"""Measure how much distinct authored task packs overlap, to calibrate the screening thresholds.

For every ordered pair of packs under ROOT, index one pack and screen the other against it, then
report the distribution of the four overlap measures. The pairs are distinct problems, so this is
the background overlap that a genuinely new candidate should stay below.

Usage: uv run --locked --all-packages python scripts/taskgen_pack_overlap.py taskpacks
This is read-only and does not execute any task code.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from polycodebench_taskgen.overlap import ReferenceIndex

TEXT_SUFFIXES = frozenset({".py", ".rs", ".js", ".ts", ".go", ".java", ".c", ".cpp", ".h", ".md"})
MEASURES = (
    ("surface max-document", "surface", "max_document_bp"),
    ("structural max-document", "structural", "max_document_bp"),
    ("surface union", "surface", "union_bp"),
    ("structural union", "structural", "union_bp"),
)


def _pack_text(pack: Path) -> str:
    chunks: list[str] = []
    for path in sorted(pack.rglob("*")):
        if path.is_file() and path.suffix in TEXT_SUFFIXES and "__pycache__" not in path.parts:
            chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(chunks)


def _packs(root: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for manifest in sorted(root.rglob("manifest.yaml")):
        text = _pack_text(manifest.parent)
        if text.strip():
            found[manifest.parent.relative_to(root).as_posix()] = text
    return found


def main() -> int:
    parser = argparse.ArgumentParser(prog="taskgen_pack_overlap")
    parser.add_argument("root", type=Path)
    parser.add_argument("--ngram", type=int, default=8)
    args = parser.parse_args()
    packs = _packs(args.root.resolve())
    print(f"packs: {len(packs)}")
    rows: list[dict[str, int | str]] = []
    for name, text in packs.items():
        for other, other_text in packs.items():
            if other == name:
                continue
            index = ReferenceIndex(ngram=args.ngram)
            index.add(other, other_text)
            summary = index.screen(text)
            row: dict[str, int | str] = {"pack": name, "reference": other}
            for label, view, field in MEASURES:
                row[label] = getattr(summary.view(view), field)  # type: ignore[arg-type]
            rows.append(row)
    if not rows:
        print("fewer than two packs; nothing to compare")
        return 0
    for label, _, _ in MEASURES:
        values = sorted(int(row[label]) for row in rows)
        p90 = values[int(len(values) * 0.9)]
        print(
            f"{label}: min={values[0]} median={values[len(values) // 2]} "
            f"p90={p90} max={values[-1]} pairs={len(values)}"
        )
    worst = sorted(rows, key=lambda row: -int(row["structural max-document"]))[:5]
    for row in worst:
        print(
            f"  structural max-document {row['structural max-document']}: "
            f"{row['pack']} <- {row['reference']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
