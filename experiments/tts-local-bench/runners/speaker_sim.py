"""Konuşmacı tutarlılığı ölçümü — campplus gömmesi + kosinüs benzerliği.

"Ses sabit kaldı mı?" sorusunu KULAKLA değil ölçerek yanıtlar.

    worker/.venv/bin/python experiments/tts-local-bench/runners/speaker_sim.py

Model: worker/models/campplus.onnx — projenin kendi speaker-ID modeli, sherpa-onnx ile
(worker/speaker_id.py ile AYNI yol: SpeakerEmbeddingExtractor fbank'ı içeride üretir).
Bu yüzden worker/.venv ile koşulur; bench venv'lerine sherpa-onnx KURULMADI, worker'a da
hiçbir şey yazılmaz — salt okuma.

Raporlanan:
  • set-içi ikili kosinüs (ort/min/std) → set kendi içinde ne kadar tek bir sese benziyor
  • kaynağa (refs/omnipick.wav) benzerlik → kısa referans sesi ne kadar koruyor

Çıktı: out/speaker_sim.json
"""
from __future__ import annotations

import json
import sys
import wave
from itertools import combinations
from pathlib import Path

import numpy as np
import sherpa_onnx

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
MODEL = ROOT.parent.parent / "worker" / "models" / "campplus.onnx"

SETS = [
    # Deney A
    "autoseed-fixed", "autoseed-random",
    # üst taban (aynı referansla klon → ses sabit olmalı)
    "omnivoice-clone",
    # Deney B
    "omnipick-clone-3s", "omnipick-clone-6s", "omnipick-clone",
]


def read_wav(p: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(p), "rb") as w:
        sr, n = w.getframerate(), w.getnframes()
        a = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
    return a, sr


def make_extractor():
    cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(MODEL), num_threads=2, provider="cpu")
    return sherpa_onnx.SpeakerEmbeddingExtractor(cfg)


def embed(ex, p: Path) -> np.ndarray:
    a, sr = read_wav(p)
    s = ex.create_stream()
    s.accept_waveform(sample_rate=sr, waveform=a)
    s.input_finished()
    v = np.array(ex.compute(s), dtype=np.float32)
    return v / (np.linalg.norm(v) + 1e-9)  # L2 → nokta çarpımı = kosinüs


def stats(vals: list[float]) -> dict:
    if not vals:
        return {"ort": None, "min": None, "std": None, "n": 0}
    a = np.array(vals)
    return {"ort": round(float(a.mean()), 4), "min": round(float(a.min()), 4),
            "std": round(float(a.std(ddof=1)) if len(a) > 1 else 0.0, 4), "n": len(a)}


def main() -> None:
    if not MODEL.is_file():
        sys.exit(f"campplus yok: {MODEL}")
    ex = make_extractor()
    src = embed(ex, ROOT / "refs" / "omnipick.wav")

    report: dict = {"model": str(MODEL), "dim": int(ex.dim), "setler": {}}
    print(f"campplus dim={ex.dim}\n")
    print(f"{'set':22s} {'set-ici ort':>12s} {'min':>7s} {'std':>7s} {'kaynaga':>9s}")

    for name in SETS:
        d = OUT / name
        if not d.is_dir():
            print(f"{name:22s}  ATLANDI (yok)")
            continue
        wavs = sorted(d.glob("*.wav"))
        embs = {w.stem: embed(ex, w) for w in wavs}
        within = [float(embs[a] @ embs[b]) for a, b in combinations(sorted(embs), 2)]
        to_src = [float(v @ src) for v in embs.values()]
        w_st, s_st = stats(within), stats(to_src)
        report["setler"][name] = {"set_ici": w_st, "kaynaga_benzerlik": s_st, "wav": len(wavs)}
        print(f"{name:22s} {w_st['ort']:12.4f} {w_st['min']:7.4f} {w_st['std']:7.4f} {s_st['ort']:9.4f}")

    (OUT / "speaker_sim.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nyazıldı: out/speaker_sim.json")


if __name__ == "__main__":
    main()
