#!/usr/bin/env bash
# ZEPAY V3 installer — creates a venv, installs deps, runs the selftest.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PYTHON:-python3}
echo "== ZEPAY V3 install =="
$PY -m venv .venv
. .venv/bin/activate
pip install --upgrade pip >/dev/null
pip install fastapi uvicorn httpx websockets numpy
pip install redis "psycopg[binary]" 2>/dev/null || echo "optional backends skipped (redis/postgres)"
pip install pytest ruff black mypy 2>/dev/null || echo "dev tools skipped"

mkdir -p "${ZEPAY_DATA_DIR:-$HOME/.zepay}"
export PYTHONPATH="$PWD"
echo "== selftest (safety invariants) =="
python -m zepay.apps.selftest

echo
echo "Install OK. Run:  source .venv/bin/activate && python -m zepay.apps.server"
echo "Then open:        http://127.0.0.1:8000"
