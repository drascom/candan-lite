"""Piper Türkçe (99eren99/piper-turkish-tts) — hafif taban referansı.

KLONLAMA YOK, DUYGU KONTROLÜ YOK: tek sabit ses, 22.05 kHz. VITS/ONNX, CPU'da koşar
(MPS kullanmaz) — bu yüzden RTF'i diğerleriyle aynı donanım ekseninde okuma.

Model kartındaki inference varsayılanları kullanılıyor (config.json ile aynı):
length_scale=1.2, noise_scale=0.4, noise_w_scale=0.3.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from huggingface_hub import hf_hub_download
from piper import PiperVoice, SynthesisConfig

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import bench  # noqa: E402

REPO = "99eren99/piper-turkish-tts"

voice = PiperVoice.load(
    hf_hub_download(REPO, "model.onnx"),
    config_path=hf_hub_download(REPO, "config.json"),
    use_cuda=False,
)
CFG = SynthesisConfig(length_scale=1.2, noise_scale=0.4, noise_w_scale=0.3)


def synth(text: str):
    chunks = list(voice.synthesize(text, syn_config=CFG))
    if not chunks:
        raise RuntimeError("Piper hiç ses üretmedi")
    audio = np.concatenate([np.frombuffer(c.audio_int16_bytes, dtype=np.int16) for c in chunks])
    return audio, chunks[0].sample_rate


bench("piper", synth, variant="default")
