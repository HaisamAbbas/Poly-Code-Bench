#!/usr/bin/env bash
set -euo pipefail

# AMI build input contract: pin these package versions in the image build manifest.
: "${PCB_DOCKER_VERSION:?pinned Docker Engine version required}"
: "${PCB_PYTHON_VERSION:?pinned Python version required}"
: "${PCB_CONTROL_USER:?dedicated forced-command user required}"
: "${PCB_CONTROL_PUBLIC_KEY:?supervisor public key required}"
: "${PCB_GUEST_AGENT_SOURCE:?guest agent source path required}"

[[ "${PCB_CONTROL_USER}" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]]
read -r key_type key_body _key_comment <<< "${PCB_CONTROL_PUBLIC_KEY}"
[[ "${key_type}" =~ ^(ssh-ed25519|ecdsa-sha2-nistp256)$ ]]
[[ "${key_body}" =~ ^[A-Za-z0-9+/=]+$ ]]

systemctl start docker
actual_docker="$(docker version --format '{{.Server.Version}}')"
actual_python="$(python3 --version | awk '{print $2}')"
[[ "${actual_docker}" == "${PCB_DOCKER_VERSION}" ]]
[[ "${actual_python}" == "${PCB_PYTHON_VERSION}" ]]

id "${PCB_CONTROL_USER}" >/dev/null 2>&1 || useradd --system --create-home --shell /bin/sh "${PCB_CONTROL_USER}"
install -o root -g root -m 0750 "${PCB_GUEST_AGENT_SOURCE}" /usr/local/sbin/pcb-guest-control
install -d -o root -g "${PCB_CONTROL_USER}" -m 0750 "/home/${PCB_CONTROL_USER}/.ssh"
printf 'restrict,command="sudo --non-interactive /usr/local/sbin/pcb-guest-control" %s\n' \
  "${PCB_CONTROL_PUBLIC_KEY}" > "/home/${PCB_CONTROL_USER}/.ssh/authorized_keys"
chown root:"${PCB_CONTROL_USER}" "/home/${PCB_CONTROL_USER}/.ssh/authorized_keys"
chmod 0640 "/home/${PCB_CONTROL_USER}/.ssh/authorized_keys"

install -d -o root -g root -m 0755 /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/70-polycodebench-control.conf <<EOF
Match User ${PCB_CONTROL_USER}
    ForceCommand sudo --non-interactive /usr/local/sbin/pcb-guest-control
    AllowTcpForwarding no
    AllowAgentForwarding no
    X11Forwarding no
    PermitTunnel no
    PermitTTY no
EOF
printf '%s\n' "${PCB_CONTROL_USER} ALL=(root) NOPASSWD: /usr/local/sbin/pcb-guest-control" \
  > /etc/sudoers.d/70-polycodebench-control
chmod 0440 /etc/sudoers.d/70-polycodebench-control
visudo -cf /etc/sudoers.d/70-polycodebench-control
sshd -t
systemctl enable docker sshd

# This helper must run at boot before SSH accepts the stage control connection.
cat > /etc/systemd/system/pcb-guest-ready.service <<'EOF'
[Unit]
Description=PolyCodeBench disposable guest readiness
After=docker.service sshd.service
Requires=docker.service sshd.service

[Service]
Type=oneshot
ExecStart=/usr/bin/docker info --format {{.ServerVersion}}
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
systemctl enable pcb-guest-ready.service
