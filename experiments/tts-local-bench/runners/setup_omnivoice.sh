#!/usr/bin/env bash
# OmniVoice (k2-fsa) — BASELINE. Serverdeki clone modunu taklit eder.
set -euo pipefail
cd "$(dirname "$0")/.."
uv venv --python 3.12 --allow-existing venvs/omnivoice
VIRTUAL_ENV=venvs/omnivoice uv pip install torch==2.8.0 torchaudio==2.8.0 numpy soundfile
VIRTUAL_ENV=venvs/omnivoice uv pip install omnivoice
# num2words: normalize_text=True için gerekli (Türkçe destekliyor) — omnipick-norm seti
VIRTUAL_ENV=venvs/omnivoice uv pip install num2words
echo "OK: venvs/omnivoice"
