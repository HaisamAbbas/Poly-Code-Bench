#!/usr/bin/env bash
# Run ON the Oracle A1 VM from the repository root, after deploy.sh.
# Rebuilds the Python and Rust grading images natively for arm64, re-seals the arm64 pilot
# packs against the new digests and re-runs admission (emulated admission timed out on the
# workstation). Long-running: expect tens of minutes. Logs go to .cache/arm64-logs/.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
export PATH="$HOME/.local/bin:$PATH"
LOGS=.cache/arm64-logs
mkdir -p "$LOGS"
[ "$(uname -m)" = "aarch64" ] || { echo "expected an aarch64 host" >&2; exit 1; }

run() { # run <log-name> <command...>
  local name="$1"; shift
  echo "==> $name"
  if ! "$@" >"$LOGS/$name.log" 2>&1; then
    echo "FAILED: $name (see $LOGS/$name.log)" >&2
    tail -20 "$LOGS/$name.log" >&2
    exit 1
  fi
}

PY=(uv run --locked --all-packages python)
[ -d .wheelhouse/evaluator-arm64 ] || {
  echo "missing .wheelhouse/evaluator-arm64; push it from the workstation or run the pip download in scripts/build_python_images.py" >&2
  exit 1
}

run python-build "${PY[@]}" scripts/build_python_images.py --platform linux/arm64
run rust-components "${PY[@]}" scripts/fetch_rust_components.py --platform linux/arm64
run rust-build "${PY[@]}" scripts/build_rust_images.py --platform linux/arm64

for lang in python rust; do
  pack=".protected/taskpacks/${lang}-pilot-arm64"
  reports=".protected/reports/${lang}-pilot-arm64"
  run "${lang}-seal" "${PY[@]}" "scripts/${lang}_task_tool.py" --protected "$pack" seal-all
  # Admission reports per-task results; keep going so the inventory records partial passes.
  "${PY[@]}" "scripts/${lang}_admit_all.py" --protected "$pack" --reports "$reports" \
    >"$LOGS/${lang}-admit.log" 2>&1 || echo "admission reported failures for ${lang}; see $LOGS/${lang}-admit.log"
  run "${lang}-inventory" "${PY[@]}" "scripts/${lang}_pilot_inventory.py" --protected "$pack" \
    --reports "$reports" --output "taskpacks/${lang}-pilot-arm64/inventory.yaml"
  grep -E "^(authored_clusters|executable_admission_passed):" "taskpacks/${lang}-pilot-arm64/inventory.yaml"
done
