"""Ortak bench koşum yardımcısı — her model runner'ı bunu import eder.

Bağımlılık: sadece stdlib + numpy. (Her modelin kendi venv'i var; soundfile/torchaudio
her yerde yok, o yüzden WAV yazımı stdlib `wave` ile.)

Kullanım (runner içinde):

    from common import bench

    def synth(text: str):
        return model.generate(text), 24000   # (float32 np.ndarray, sample_rate)

    bench("chatterbox", synth)

Çıktı:
    out/<model>/<id>.wav
    out/<model>/timings.json   → merge_timings.py bunları out/timings.json'a birleştirir
"""
from __future__ import annotations

import json
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SENTENCES = ROOT / "sentences.json"
OUT = ROOT / "out"
REF = ROOT / "refs" / "ayhan_ref.wav"
REF_16K = ROOT / "refs" / "ayhan_ref_16k.wav"
REF_TEXT_FILE = ROOT / "refs" / "ayhan_ref.txt"


def load_sentences(path: Path | None = None) -> list[dict]:
    data = json.loads((path or SENTENCES).read_text(encoding="utf-8"))
    return data["sentences"]


def ref_text() -> str:
    """Referans klibin transkripti. Yoksa boş string döner (runner karar verir)."""
    if REF_TEXT_FILE.exists():
        return REF_TEXT_FILE.read_text(encoding="utf-8").strip()
    return ""


def write_wav(path: Path, audio: np.ndarray, sample_rate: int) -> float:
    """float32/-1..1 veya int16 diziyi mono 16-bit WAV olarak yaz. Süreyi (sn) döner."""
    audio = np.asarray(audio)
    if audio.ndim > 1:
        audio = audio.reshape(-1) if 1 in audio.shape else audio.mean(axis=0)
    if audio.dtype != np.int16:
        audio = np.clip(audio.astype(np.float32), -1.0, 1.0)
        audio = (audio * 32767.0).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(audio.tobytes())
    return len(audio) / float(sample_rate)


def bench(model: str, synth, variant: str = "", sentences_file: Path | None = None,
          only: list[str] | None = None) -> None:
    """Tüm cümleleri sentezle, süre ölç, out/<model>/ altına yaz.

    synth(text) -> (audio_array, sample_rate)
    sentences_file: None → sentences.json; duygu ekseni için sentences_emotion.json verilir.
    only: yalnız bu id'ler üretilir (alt-küme koşuları için; None → hepsi).
    Bir cümle patlarsa atlanır ve timings'e hata olarak kaydedilir; bench durmaz.
    """
    name = f"{model}-{variant}" if variant else model
    outdir = OUT / name
    outdir.mkdir(parents=True, exist_ok=True)
    rows = []

    items = load_sentences(sentences_file)
    if only:
        idx = {x: i for i, x in enumerate(only)}
        items = sorted((s for s in items if s["id"] in only), key=lambda s: idx[s["id"]])
        missing = set(only) - {s["id"] for s in items}
        if missing:
            raise SystemExit(f"bilinmeyen cümle id'si: {sorted(missing)}")
    total = len(items)
    for i, s in enumerate(items, 1):
        sid, text = s["id"], s["text"]
        wav_path = outdir / f"{sid}.wav"
        print(f"[{name}] {i:2d}/{total} {sid} ...", flush=True)
        t0 = time.perf_counter()
        try:
            audio, sr = synth(text)
            elapsed = time.perf_counter() - t0
            dur = write_wav(wav_path, audio, sr)
            # Sıfır uzunluklu çıktı BAŞARISIZLIKTIR: model o cümle için ses üretmemiş.
            # (normalize_text=True bazı kısa sorularda bunu yapıyor.) Sessizce "ok" saymıyoruz.
            if dur <= 0:
                raise RuntimeError("model sıfır uzunlukta ses üretti (boş çıktı)")
            rows.append({
                "id": sid,
                "wall_s": round(elapsed, 3),
                "audio_s": round(dur, 3),
                "rtf": round(elapsed / dur, 3),
                "sample_rate": int(sr),
                "ok": True,
            })
            print(f"    {elapsed:.2f}s wall / {dur:.2f}s audio (RTF {elapsed / dur:.2f})", flush=True)
        except Exception as exc:  # noqa: BLE001 — tek cümle patlarsa bench durmasın
            elapsed = time.perf_counter() - t0
            rows.append({"id": sid, "wall_s": round(elapsed, 3), "ok": False, "error": f"{type(exc).__name__}: {exc}"})
            print(f"    HATA: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)

    ok = [r for r in rows if r["ok"]]
    rtfs = [r["rtf"] for r in ok if r.get("rtf") is not None]
    summary = {
        "model": name,
        "device": "mps (Apple M4 Pro)",
        "count_ok": len(ok),
        "count_fail": len(rows) - len(ok),
        "total_wall_s": round(sum(r["wall_s"] for r in rows), 2),
        "total_audio_s": round(sum(r["audio_s"] for r in ok), 2),
        "mean_rtf": round(sum(rtfs) / len(rtfs), 3) if rtfs else None,
        "items": rows,
    }
    (outdir / "timings.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[{name}] bitti: {len(ok)}/{len(rows)} ok, toplam {summary['total_wall_s']}s wall", flush=True)
