#!/usr/bin/env bash
# Run ON the Oracle A1 VM as the deploy user, from the repository root.
# Idempotent: brings the public PolyCodeBench POC up (or updates it) behind Caddy/HTTPS.
#
# Usage: scripts/oracle/deploy.sh --domain bench.example.com [--acme-email ops@example.com]
#
# Data services (PostgreSQL, SeaweedFS, Keycloak) and the API/web processes listen only on
# 127.0.0.1; Caddy is the only public listener (80/443). No secret value is printed.
set -euo pipefail

DOMAIN=""
ACME_EMAIL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --domain) DOMAIN="$2"; shift 2 ;;
    --acme-email) ACME_EMAIL="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -n "$DOMAIN" ] || { echo "--domain is required (--acme-email is optional)" >&2; exit 2; }

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
UV_VERSION=0.12.17
NODE_VERSION=24.21.0
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"

step() { printf '\n==> %s\n' "$*"; }

step "Host tools"
[ "$(uname -m)" = "aarch64" ] || { echo "expected an aarch64 host" >&2; exit 1; }
docker compose version >/dev/null
if ! command -v uv >/dev/null || [ "$(uv --version | awk '{print $2}')" != "$UV_VERSION" ]; then
  curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" | sh
fi
if ! command -v node >/dev/null || [ "$(node --version)" != "v${NODE_VERSION}" ]; then
  tmp="$(mktemp -d)"
  (cd "$tmp" \
    && curl -fsSLO "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-arm64.tar.xz" \
    && curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt" \
      | grep " node-v${NODE_VERSION}-linux-arm64.tar.xz\$" | sha256sum -c - \
    && sudo tar -xJf "node-v${NODE_VERSION}-linux-arm64.tar.xz" -C /usr/local --strip-components=1)
  rm -rf "$tmp"
fi

step "Close non-essential listeners"
# Ubuntu cloud images run rpcbind on :111; nothing here needs it.
sudo systemctl disable --now rpcbind.service rpcbind.socket >/dev/null 2>&1 || true

step "Python environment"
uv sync --locked --all-packages --group dev

step "Local credentials and public domain"
uv run --locked --group dev python scripts/local_stack.py prepare
uv run --locked --group dev python scripts/oracle/configure_public.py --domain "$DOMAIN" --acme-email "$ACME_EMAIL"

COMPOSE=(docker compose -f compose.yaml -f infra/oracle/deploy/compose.oracle.yaml
  --env-file .env --env-file infra/oracle/deploy/.deploy.env --profile local-auth)

# Server-side OIDC calls go to Caddy on this host rather than hairpinning via the public IP.
grep -qE "^127\.0\.0\.1[[:space:]]+${DOMAIN//./\\.}\$" /etc/hosts \
  || echo "127.0.0.1 ${DOMAIN}" | sudo tee -a /etc/hosts >/dev/null

step "Data services and reverse proxy"
"${COMPOSE[@]}" up -d postgres object-store keycloak caddy
uv run --locked --group dev python scripts/local_stack.py wait-for-keycloak
uv run --locked --group dev python scripts/local_stack.py bootstrap-db

step "Web build"
npm exec --yes --package=pnpm@12.5.1 -- pnpm install --frozen-lockfile
set -a; . ./.env; set +a
PCB_PUBLIC_API_URL=http://127.0.0.1:8010/v1 \
  npm exec --yes --package=pnpm@12.5.1 -- pnpm --filter @polycodebench/web build

step "Services"
sudo install -m 644 infra/oracle/deploy/pcb-api.service /etc/systemd/system/pcb-api.service
sudo install -m 644 infra/oracle/deploy/pcb-web.service /etc/systemd/system/pcb-web.service
sudo sed -i "s|@ROOT@|$ROOT|g; s|@USER@|$(id -un)|g" \
  /etc/systemd/system/pcb-api.service /etc/systemd/system/pcb-web.service
sudo systemctl daemon-reload
sudo systemctl enable pcb-api pcb-web
sudo systemctl restart pcb-api pcb-web

step "Health"
for _ in $(seq 1 30); do
  curl -fsS http://127.0.0.1:8010/readyz >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS http://127.0.0.1:8010/readyz >/dev/null && echo "api ready"
for _ in $(seq 1 30); do
  curl -fsS -o /dev/null http://127.0.0.1:3001/ 2>/dev/null && break
  sleep 2
done
curl -fsS -o /dev/null "https://${DOMAIN}/" && echo "public site reachable at https://${DOMAIN}/"
echo "Listening sockets (only :22, :80, :443 should be non-loopback):"
ss -ltnH | awk '{print $4}' | sort -u
