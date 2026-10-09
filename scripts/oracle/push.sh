#!/usr/bin/env bash
# Run on the operator workstation. Copies the working tree to the Oracle VM: tracked plus
# untracked-but-not-ignored files (so .env, .local, .cache, .protected, .wheelhouse and every
# other git-ignored path stay home), minus a few extra patterns excluded below. Shell scripts,
# systemd units, the Caddyfile and compose YAML under scripts/ and infra/oracle are converted to
# LF on the VM (the Windows working tree has CRLF).
#
# Usage: scripts/oracle/push.sh <vm-public-ip> [ssh-private-key]
set -euo pipefail

HOST="${1:?usage: push.sh <vm-public-ip> [ssh-private-key]}"
KEY="${2:-.local/oracle/pcb_deploy_ed25519}"
REMOTE_DIR="polycodebench"
SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new "deploy@${HOST}")

cd "$(dirname "$0")/../.."
# tar exits 2 for files deleted in the working tree but still tracked; that is tolerated.
git ls-files -z --cached --others --exclude-standard \
  | { tar --create --gzip --null --ignore-failed-read \
      --exclude='.env' --exclude='.env.*' --exclude='*.pem' --exclude='*.key' \
      --exclude='*.tfstate*' --exclude='*.tfvars' --exclude='*/.terraform' \
      --exclude='*/node_modules' --exclude='*/.next' --exclude='*.tsbuildinfo' \
      --exclude='*/__pycache__' --exclude='.local' --exclude='.claude' \
      --exclude='infra/oracle/deploy/.deploy.env' --exclude='tmp_*' \
      --exclude='*.sqlite3' --files-from=- 2>/dev/null || [ $? -eq 2 ]; } \
  | "${SSH[@]}" "mkdir -p ${REMOTE_DIR} && tar --extract --gzip --directory ${REMOTE_DIR} --no-same-owner \
      && cd ${REMOTE_DIR} \
      && find scripts infra/oracle -type f \( -name '*.sh' -o -name '*.service' -o -name Caddyfile -o -name '*.yaml' \) \
        -exec sed -i 's/\r\$//' {} +"
echo "Pushed working tree to deploy@${HOST}:~/${REMOTE_DIR}"
