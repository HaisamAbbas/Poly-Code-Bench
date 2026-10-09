#!/usr/bin/env bash
# Run on the operator workstation. Copies the working tree (including the git-ignored
# .protected task packs, excluding local credentials and build output) to the Oracle VM.
#
# Usage: scripts/oracle/push.sh <vm-public-ip> [ssh-private-key]
set -euo pipefail

HOST="${1:?usage: push.sh <vm-public-ip> [ssh-private-key]}"
KEY="${2:-.local/oracle/pcb_deploy_ed25519}"
REMOTE_DIR="polycodebench"
SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new "deploy@${HOST}")

cd "$(dirname "$0")/../.."
tar --create --gzip \
  --exclude=./.git --exclude=./node_modules --exclude='./apps/*/node_modules' \
  --exclude='./packages/*/node_modules' --exclude=./.venv --exclude=./.cache \
  --exclude=./.local --exclude='./.env' --exclude='./.env.*' --exclude='./pytest-cache-files-*' \
  --exclude=./dist --exclude='./tmp_*' --exclude='./apps/web/.next' --exclude='*/__pycache__' \
  --exclude='./infra/oracle/terraform/.terraform' --exclude='*.tfstate*' --exclude='*.tfvars' \
  --exclude='./infra/oracle/deploy/.deploy.env' \
  . | "${SSH[@]}" "mkdir -p ${REMOTE_DIR} && tar --extract --gzip --directory ${REMOTE_DIR} --no-same-owner"
echo "Pushed working tree to deploy@${HOST}:~/${REMOTE_DIR}"
