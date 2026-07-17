#!/usr/bin/env python3
"""Ortak yardımcılar — WeSpeaker gömme + WAV yükleme + 3sn pencereleme.

ab.py'deki OnnxEncoder ve load_wav_16k_mono'yu YENİDEN kullanır (kopya değil):
sibling dizin experiments/speaker-encoder-ab import path'e eklenir.
WeSpeaker onnx SALT-OKUNUR; canlı worker/ , speakers.db, .env'e DOKUNULMAZ.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
AB_DIR = HERE.parent / "speaker-encoder-ab"
sys.path.insert(0, str(AB_DIR))

from ab import (  # noqa: E402  (path ekledikten sonra import)
    WESPEAKER_MODEL_ID,
    WESPEAKER_PATH,
    OnnxEncoder,
)

TARGET_SR = 16000
WIN_SEC = 3.0
RMS_MIN = 0.015  # canlı worker ile aynı sessizlik eşiği


def load_wav_16k_mono(path: Path) -> np.ndarray:
    """WAV -> [-1,1] float32 mono @16k. soundfile ile (PCM + IEEE-float destekli).

    ab.py'nin stdlib `wave` loader'ı IEEE-float (fmt 3) WAV'ları okuyamıyor (FLEURS böyle);
    soundfile her ikisini de okur. Gerekirse basit lineer resample.
    """
    import soundfile as sf

    a, sr = sf.read(str(path), dtype="float32", always_2d=False)
    a = np.asarray(a, dtype=np.float32)
    if a.ndim > 1:
        a = a.mean(axis=1)
    if sr != TARGET_SR and a.size:
        dst_n = round(a.size * TARGET_SR / sr)
        a = np.interp(
            np.linspace(0.0, a.size - 1, dst_n, dtype=np.float64),
            np.arange(a.size),
            a,
        ).astype(np.float32)
    return np.ascontiguousarray(a, dtype=np.float32)


def build_wespeaker() -> OnnxEncoder:
    if not WESPEAKER_PATH.is_file():
        raise FileNotFoundError(f"WeSpeaker onnx yok: {WESPEAKER_PATH}")
    return OnnxEncoder("wespeaker resnet34-LM", WESPEAKER_PATH, WESPEAKER_MODEL_ID)


def l2(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def windows(samples: np.ndarray, win_sec: float = WIN_SEC, rms_min: float = RMS_MIN) -> list[np.ndarray]:
    """~win_sec'lik pencerelere böl; rms<rms_min (sessiz) pencereleri at."""
    w = int(win_sec * TARGET_SR)
    if samples.size < w:
        chunks = [samples] if samples.size >= int(0.8 * w) else []
    else:
        chunks = [samples[i : i + w] for i in range(0, samples.size - w + 1, w)]
    out = []
    for c in chunks:
        if c.size and float(np.sqrt(np.mean(c.astype(np.float64) ** 2))) >= rms_min:
            out.append(np.ascontiguousarray(c, dtype=np.float32))
    return out


def embed_wav_windows(enc: OnnxEncoder, wav: Path, per_window: bool = True) -> list[np.ndarray]:
    """WAV -> pencere gömmeleri (L2-normalize). per_window=False ise tüm dosya tek gömme."""
    samples = load_wav_16k_mono(wav)
    segs = windows(samples) if per_window else ([samples] if samples.size else [])
    return [l2(enc.embed(s)) for s in segs if s.size]
