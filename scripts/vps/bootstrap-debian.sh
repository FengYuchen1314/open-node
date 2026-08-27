#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "bootstrap-debian.sh must run as root" >&2
  exit 1
fi

apt-get update
apt-get install -y ca-certificates curl git gnupg python3-venv

node_major="0"
if command -v node >/dev/null 2>&1; then
  node_major="$(node --version | sed -E 's/^v([0-9]+).*/\1/')"
fi

if [ "$node_major" -lt 24 ]; then
  curl -fsSL https://deb.nodesource.com/setup_24.x -o /tmp/nodesource_setup_24.sh
  bash /tmp/nodesource_setup_24.sh
  apt-get install -y nodejs
fi

python3 --version
node --version
npm --version
