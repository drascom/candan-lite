#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
VENV="$HERE/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"
NEMO_COMMIT="ba2cd63ef8de8a3183a3c02b310c66d616b9a991"

"$PYTHON_BIN" -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip setuptools wheel Cython packaging
"$VENV/bin/python" -m pip install \
  'torch>=2.7,<2.10' 'torchaudio>=2.7,<2.10' \
  --index-url https://download.pytorch.org/whl/cu126
"$VENV/bin/python" -m pip install \
  "nemo_toolkit[asr] @ git+https://github.com/NVIDIA-NeMo/NeMo.git@$NEMO_COMMIT" \
  wyoming sounddevice

"$VENV/bin/python" -c \
  'import torch; from nemo.collections.asr.models import SortformerEncLabelModel; print("torch", torch.__version__, "cuda", torch.cuda.is_available(), "sortformer", SortformerEncLabelModel.__name__)'

