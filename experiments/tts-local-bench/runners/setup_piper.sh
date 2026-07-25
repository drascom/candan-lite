#!/usr/bin/env bash
# Piper Türkçe (99eren99/piper-turkish-tts) — hafif ONNX/VITS taban referansı.
# Tek sabit ses (num_speakers=1), 22.05 kHz, espeak-ng ile Türkçe fonemleştirme.
# CPU'da koşar (ONNX Runtime); MPS kullanmaz.
set -euo pipefail
cd "$(dirname "$0")/.."
uv venv --python 3.12 --allow-existing venvs/piper
VIRTUAL_ENV=venvs/piper uv pip install piper-tts huggingface_hub numpy
echo "OK: venvs/piper"
