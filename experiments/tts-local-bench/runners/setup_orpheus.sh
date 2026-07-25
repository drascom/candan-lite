#!/usr/bin/env bash
# Orpheus-TTS-Turkish (Karayakar/Orpheus-TTS-Turkish-PT-5000) + SNAC 24 kHz decoder.
# GGUF yerine safetensors+transformers seçildi: llama.cpp'de audio-token akışını elle
# çözmek gerekiyor, transformers yolu model kartındaki referans akışın birebir aynısı.
set -euo pipefail
cd "$(dirname "$0")/.."
uv venv --python 3.12 --allow-existing venvs/orpheus
VIRTUAL_ENV=venvs/orpheus uv pip install torch torchaudio transformers snac numpy huggingface_hub
echo "OK: venvs/orpheus"
