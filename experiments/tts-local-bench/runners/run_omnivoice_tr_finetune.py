"""dcx514ai/omnivoice_tr_finetune — İSKELET. Repo GATED, erişim bekleniyor.

Erişim gelince:
    hf auth login                      # gated repoyu görebilen token
    ./runners/setup_omnivoice.sh       # aynı venv yeter (OmniVoice mimarisi)
    venvs/omnivoice/bin/python runners/run_omnivoice_tr_finetune.py

Görev notundaki sabitler: revision="v1500", trust_remote_code=True.
Erişim yoksa script anlaşılır bir mesajla çıkar — bench'in geri kalanını etkilemez.
"""
from __future__ import annotations

import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import REF, bench, ref_text  # noqa: E402

REPO = "dcx514ai/omnivoice_tr_finetune"
REVISION = "v1500"

try:
    from omnivoice import OmniVoice
except ImportError:
    sys.exit("omnivoice kurulu değil → ./runners/setup_omnivoice.sh")

DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"

try:
    model = OmniVoice.from_pretrained(
        REPO,
        revision=REVISION,
        trust_remote_code=True,
        device_map=DEVICE,
        dtype=torch.float32,
    )
except Exception as exc:  # noqa: BLE001 — gated repo erişimi yoksa temiz çık
    sys.exit(f"{REPO} yüklenemedi (muhtemelen gated erişim yok): {type(exc).__name__}: {exc}")

RT = ref_text()


def synth(text: str):
    audio = model.generate(text=text, ref_audio=str(REF), ref_text=RT)
    return audio[0], 24000


bench("omnivoice-tr-finetune", synth)
