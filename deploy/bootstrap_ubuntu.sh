#!/usr/bin/env bash
# Run on the VPS as root through setup_server.py.
set -Eeuo pipefail
trap 'printf "\nServer setup failed at line %s.\n" "$LINENO" >&2' ERR

if [ "$(id -u)" -ne 0 ]; then
    printf 'Run this script as root.\n' >&2
    exit 1
fi

. /etc/os-release
if [ "${ID:-}" != ubuntu ] || [ "${VERSION_ID:-}" != 24.04 ]; then
    printf 'This installer requires Ubuntu 24.04.\n' >&2
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive
printf '\n[1/4] Installing prerequisite packages...\n'
apt-get -o DPkg::Lock::Timeout=120 update </dev/null
apt-get -o DPkg::Lock::Timeout=120 install -y ca-certificates curl python3 unzip ufw </dev/null

printf '\n[2/4] Installing Docker Engine and Compose...\n'
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    printf 'Docker and Compose are already installed.\n'
else
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL --retry 3 https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
    apt-get -o DPkg::Lock::Timeout=120 update </dev/null
    apt-get -o DPkg::Lock::Timeout=120 install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin </dev/null
fi
systemctl enable --now docker

printf '\n[3/4] Allowing SSH and website traffic...\n'
# Preserve the current SSH access before enabling the firewall.
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable

printf '\n[4/4] Checking Docker and firewall...\n'
systemctl is-active --quiet docker
docker version
docker compose version
ufw status
printf '\nINFOCODE_SERVER_READY\n'
