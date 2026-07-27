"""Higgs deneyi — ortak koşum/ölçüm yardımcısı (SUNUCUDA çalışır).

tts-local-bench/runners/common.py'nin sunucu sürümü. Farklar:
  • cihaz "cuda (RTX 3090)" — Mac MPS değil, ölçümler karışmasın diye ayrı dizin.
  • VRAM ve RAM TEPE değerleri örnekleyici bir arka plan iş parçacığıyla ölçülür.
    (`nvidia-smi` tüm GPU'yu görür: llama-server + whisper de içinde. Bu yüzden
    hem "GPU toplam" hem "bu süreç" ayrı raporlanır.)

Bağımlılık: stdlib + numpy. torch İSTEĞE BAĞLI (varsa süreç-içi VRAM de ölçülür).
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
SENTENCES = ROOT / "sentences.json"
OUT = ROOT / "out"


# ─────────────────────────── cümleler / wav ────────────────────────────────
def load_sentences(path: Path | None = None) -> list[dict]:
    return json.loads((path or SENTENCES).read_text(encoding="utf-8"))["sentences"]


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """Mono float32 (-1..1) + örnekleme hızı. 16-bit PCM bekler (stdlib wave)."""
    with wave.open(str(path), "rb") as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw != 2:
        raise ValueError(f"{path}: 16-bit PCM bekleniyordu, sampwidth={sw}")
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    return a, sr


def write_wav(path: Path, audio: np.ndarray, sample_rate: int) -> float:
    """float32/-1..1 veya int16 diziyi mono 16-bit WAV yaz. Süreyi (sn) döner."""
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


# ─────────────────────────── VRAM / RAM ölçümü ─────────────────────────────
def gpu_used_mib() -> int:
    """Tüm GPU'da kullanılan MiB (llama-server + whisper dahil)."""
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, check=True,
        )
        return int(r.stdout.strip().splitlines()[0])
    except Exception:  # noqa: BLE001 — ölçüm yoksa bench durmasın
        return -1


def ram_used_mib() -> int:
    """MemTotal - MemAvailable (MiB). Sistem geneli; swap'a düşüşü de yakalar."""
    vals = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        k, _, v = line.partition(":")
        vals[k] = int(v.strip().split()[0])  # kB
    return (vals["MemTotal"] - vals["MemAvailable"]) // 1024


class PeakMonitor:
    """Arka planda GPU/RAM örnekler; `with` bloğunun tepe değerlerini tutar."""

    def __init__(self, interval: float = 0.25):
        self.interval = interval
        self.gpu_peak = 0
        self.ram_peak = 0
        self.samples = 0
        self._stop = threading.Event()
        self._t: threading.Thread | None = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.gpu_peak = max(self.gpu_peak, gpu_used_mib())
            self.ram_peak = max(self.ram_peak, ram_used_mib())
            self.samples += 1
            self._stop.wait(self.interval)

    def __enter__(self) -> "PeakMonitor":
        self.gpu_peak = gpu_used_mib()
        self.ram_peak = ram_used_mib()
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._t:
            self._t.join(timeout=2)

    def as_dict(self) -> dict:
        return {"gpu_peak_mib": self.gpu_peak, "ram_peak_mib": self.ram_peak,
                "samples": self.samples}


def torch_vram_mib() -> dict:
    """Süreç-İÇİ VRAM (torch ayırıcısı). torch yoksa boş sözlük."""
    try:
        import torch

        if not torch.cuda.is_available() or torch.cuda.max_memory_allocated() == 0:
            # torch kurulu ama bu süreç GPU kullanmıyor (örn. OmniVoice HTTP runner'ı):
            # "0 MiB" yazmak yanıltıcı olur, alanı hiç koyma.
            return {}
        return {
            "torch_allocated_peak_mib": round(torch.cuda.max_memory_allocated() / 2**20),
            "torch_reserved_peak_mib": round(torch.cuda.max_memory_reserved() / 2**20),
        }
    except Exception:  # noqa: BLE001
        return {}


# ─────────────────────────── bench döngüsü ─────────────────────────────────
def bench(name: str, synth, *, device: str, only: list[str] | None = None,
          extra: dict | None = None) -> dict:
    """Tüm cümleleri sentezle, süre ölç, out/<name>/ altına yaz.

    synth(text) -> (audio_array, sample_rate)
    Bir cümle patlarsa atlanır ve timings'e hata olarak kaydedilir; bench durmaz.
    """
    outdir = OUT / name
    outdir.mkdir(parents=True, exist_ok=True)
    rows = []

    items = load_sentences()
    if only:
        items = [s for s in items if s["id"] in only]
    total = len(items)

    with PeakMonitor() as mon:
        for i, s in enumerate(items, 1):
            sid, text = s["id"], s["text"]
            print(f"[{name}] {i:2d}/{total} {sid} ...", flush=True)
            t0 = time.perf_counter()
            try:
                audio, sr = synth(text)
                elapsed = time.perf_counter() - t0
                dur = write_wav(outdir / f"{sid}.wav", audio, sr)
                if dur <= 0:
                    raise RuntimeError("model sıfır uzunlukta ses üretti (boş çıktı)")
                rows.append({
                    "id": sid, "wall_s": round(elapsed, 3), "audio_s": round(dur, 3),
                    "rtf": round(elapsed / dur, 3), "sample_rate": int(sr), "ok": True,
                })
                print(f"    {elapsed:.2f}s wall / {dur:.2f}s audio "
                      f"(RTF {elapsed / dur:.2f})", flush=True)
            except Exception as exc:  # noqa: BLE001 — tek cümle patlarsa bench durmasın
                elapsed = time.perf_counter() - t0
                rows.append({"id": sid, "wall_s": round(elapsed, 3), "ok": False,
                             "error": f"{type(exc).__name__}: {exc}"})
                print(f"    HATA: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)

    ok = [r for r in rows if r["ok"]]
    rtfs = [r["rtf"] for r in ok]
    summary = {
        "model": name,
        "device": device,
        "count_ok": len(ok),
        "count_fail": len(rows) - len(ok),
        "total_wall_s": round(sum(r["wall_s"] for r in rows), 2),
        "total_audio_s": round(sum(r["audio_s"] for r in ok), 2),
        "mean_rtf": round(sum(rtfs) / len(rtfs), 3) if rtfs else None,
        "uretim": mon.as_dict() | torch_vram_mib(),
        "items": rows,
    }
    if extra:
        summary |= extra
    (outdir / "timings.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[{name}] bitti: {len(ok)}/{len(rows)} ok, "
          f"toplam {summary['total_wall_s']}s wall, ortalama RTF {summary['mean_rtf']}",
          flush=True)
    return summary
