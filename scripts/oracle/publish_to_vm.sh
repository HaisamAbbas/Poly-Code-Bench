#!/usr/bin/env bash
# Run on the OWNER's PC. Publishes the locally built and signed release store into the Oracle VM's
# public catalog over an SSH tunnel to the VM's loopback-only PostgreSQL.
#
# The publisher DSN is read from the VM's .env over SSH into a shell variable and handed to the
# publish process through its environment only; it is never printed or written to disk. Every
# signature and digest is verified locally first (scripts/render/publish_remote.py), and a re-run is
# an idempotent replay.
#
# Usage: scripts/oracle/publish_to_vm.sh [--host IP] [--key FILE] [--dry-run] [--allow-synthetic]
#                                        [--store FILE] [--keyring FILE]
# By default only live_exploratory releases are accepted; --allow-synthetic overrides that.
set -euo pipefail

HOST="155.248.254.59"
KEY=".local/oracle/pcb_deploy_ed25519"
REMOTE_DIR="polycodebench"
LOCAL_PORT="${PCB_VM_TUNNEL_PORT:-47432}"  # outside the Windows excluded port ranges
PASS_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --key) KEY="$2"; shift 2 ;;
    --dry-run|--allow-synthetic) PASS_ARGS+=("$1"); shift ;;
    --store|--keyring) PASS_ARGS+=("$1" "$2"); shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

cd "$(dirname "$0")/../.."
DRY=0
for arg in "${PASS_ARGS[@]+"${PASS_ARGS[@]}"}"; do [ "$arg" = "--dry-run" ] && DRY=1; done
SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new -o ExitOnForwardFailure=yes "deploy@${HOST}")

if [ "$DRY" = 1 ]; then
  # Local verification only: no SSH, no database access.
  exec uv run --offline --locked --all-packages python scripts/render/publish_remote.py \
    "${PASS_ARGS[@]}"
fi

# Non-secret: the publication target the VM's API serves (the catalog rows are keyed by it).
TARGET="$("${SSH[@]}" "grep -m1 '^PCB_PUBLICATION_TARGET=' ${REMOTE_DIR}/.env | cut -d= -f2-")"
[ -n "$TARGET" ] || { echo "could not read PCB_PUBLICATION_TARGET from the VM" >&2; exit 1; }
VM_DSN="$("${SSH[@]}" "grep -m1 '^PCB_PUBLISHER_DATABASE_URL=' ${REMOTE_DIR}/.env | cut -d= -f2-")"
[ -n "$VM_DSN" ] || { echo "could not read the publisher DSN from the VM" >&2; exit 1; }

# Point the DSN at the local end of the tunnel (host and port only; credentials stay in the env).
PCB_RENDER_PUBLISHER_DATABASE_URL="$(PCB_VM_DSN="$VM_DSN" PCB_TUNNEL_PORT="$LOCAL_PORT" uv run --offline --locked python - <<'PY'
import os
from urllib.parse import urlsplit, urlunsplit

parts = urlsplit(os.environ["PCB_VM_DSN"])
netloc = f"{parts.netloc.rpartition('@')[0]}@127.0.0.1:{os.environ['PCB_TUNNEL_PORT']}"
print(urlunsplit(parts._replace(netloc=netloc)))
PY
)"
unset VM_DSN
export PCB_RENDER_PUBLISHER_DATABASE_URL

VM_PG_PORT="$("${SSH[@]}" "grep -m1 '^PCB_LOCAL_POSTGRES_PORT=' ${REMOTE_DIR}/.env | cut -d= -f2-" || true)"
VM_PG_PORT="${VM_PG_PORT:-55432}"

"${SSH[@]}" -N -L "127.0.0.1:${LOCAL_PORT}:127.0.0.1:${VM_PG_PORT}" &
TUNNEL_PID=$!
trap 'kill "$TUNNEL_PID" 2>/dev/null || true' EXIT
for _ in $(seq 1 30); do
  uv run --offline --locked python -c "import socket,sys; socket.create_connection(('127.0.0.1', int(sys.argv[1])), 1).close()" \
    "$LOCAL_PORT" 2>/dev/null && break
  kill -0 "$TUNNEL_PID" 2>/dev/null || { echo "ssh tunnel failed (port ${LOCAL_PORT} busy or reserved? set PCB_VM_TUNNEL_PORT)" >&2; exit 1; }
  sleep 1
done

uv run --offline --locked --all-packages python scripts/render/publish_remote.py \
  --target "$TARGET" "${PASS_ARGS[@]+"${PASS_ARGS[@]}"}"
