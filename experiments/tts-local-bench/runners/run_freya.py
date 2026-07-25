"""FreyaTTS (freyavoice/Freya-TTS) — Türkçeye özel 183M, Apache-2.0.

KLONLAMA YOK: tek sabit konuşmacı. Referans klip KULLANILMAZ — bu beklenen davranış.
Bench'teki rolü: "Türkçe telaffuz/akıcılık ne kadar iyi olabilir" tavanını göstermek.
Çıktı 48 kHz mono.
"""
from __future__ import annotations

import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, bench  # noqa: E402

# FreyaTTS'in pyproject/setup.py'si yok → paket klonlanmış repo kökünden import edilir.
sys.path.insert(0, str(ROOT / "vendor" / "FreyaTTS"))
from freyatts import FreyaTTS  # noqa: E402

DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"
tts = FreyaTTS.from_pretrained("freyavoice/freya-tts", device=DEVICE)


def synth(text: str):
    wav = tts.synthesize(text)  # np.float32, 48 kHz
    return wav, 48000


bench("freya", synth, variant="default")
