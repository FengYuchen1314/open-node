#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

python3 -m venv backend/.venv
source backend/.venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e "backend[dev]"
pytest backend/tests
deactivate

if [ -f frontend/package-lock.json ]; then
  npm --prefix frontend ci
else
  npm --prefix frontend install
fi

npm --prefix frontend test
npm --prefix frontend run build

if [ -f probe-worker/package.json ]; then
  if [ -f probe-worker/package-lock.json ]; then
    npm --prefix probe-worker ci
  else
    npm --prefix probe-worker install
  fi

  npm --prefix probe-worker run typecheck
fi
