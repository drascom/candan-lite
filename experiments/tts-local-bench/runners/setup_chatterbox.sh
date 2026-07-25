#!/usr/bin/env bash
# Chatterbox Multilingual (ResembleAI) — zero-shot klon + exaggeration parametresi.
#
# setuptools<81 NEDEN: chatterbox → perth (ses filigranı) → `pkg_resources` import ediyor.
# uv, 3.12 venv'ine setuptools'u hiç koymuyor; koyunca da 83.0.0 geliyor ve o sürümde
# pkg_resources KALDIRILMIŞ. perth'in __init__'i ImportError'ı yutup watermarker'ı None
# yapıyor → model yüklenirken "TypeError: 'NoneType' object is not callable".
set -euo pipefail
cd "$(dirname "$0")/.."
uv venv --python 3.12 --allow-existing venvs/chatterbox
VIRTUAL_ENV=venvs/chatterbox uv pip install chatterbox-tts numpy "setuptools<81"
echo "OK: venvs/chatterbox"
