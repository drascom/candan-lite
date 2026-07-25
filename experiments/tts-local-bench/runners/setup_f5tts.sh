#!/usr/bin/env bash
# F5-TTS-Turkish (marduk-ra) — SWivid/F5-TTS mimarisi, TR fine-tune. nfe_step=64.
set -euo pipefail
cd "$(dirname "$0")/.."
uv venv --python 3.12 --allow-existing venvs/f5tts
VIRTUAL_ENV=venvs/f5tts uv pip install "librosa>=0.11" "numba>=0.60"
VIRTUAL_ENV=venvs/f5tts uv pip install f5-tts numpy
echo "OK: venvs/f5tts"
