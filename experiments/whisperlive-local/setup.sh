#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
python_bin="${PYTHON_BIN:-python3.12}"

"$python_bin" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --no-deps whisper-live==0.9.0
.venv/bin/python -m pip install -r requirements.txt

echo "Hazır. Önce server.py, sonra başka terminalde mic_test.py çalıştırın."
