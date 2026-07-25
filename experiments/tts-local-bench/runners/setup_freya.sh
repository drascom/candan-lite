#!/usr/bin/env bash
# FreyaTTS — Türkçeye özel 183M, KLONLAMA YOK (tek sabit ses).
# NOT: requirements.txt pin'siz; `voxcpm` bağımlılığı çözümlemeyi eski librosa'ya
# düşürüyor → o da numba 0.53'ü çekiyor ve Python 3.12'de derlenmiyor.
# Modern librosa/numba'yı açıkça pinleyerek backtracking'i engelliyoruz.
set -euo pipefail
cd "$(dirname "$0")/.."
uv venv --python 3.12 --allow-existing venvs/freya
[ -d vendor/FreyaTTS ] || git clone --depth 1 https://github.com/freyavoiceai/FreyaTTS vendor/FreyaTTS
VIRTUAL_ENV=venvs/freya uv pip install "librosa>=0.11" "numba>=0.60" torch numpy einops soundfile huggingface_hub safetensors voxcpm
echo "OK: venvs/freya"
