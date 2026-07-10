#!/usr/bin/env python3
"""enroll — speaker-ID kayıt CLI'ı.

Kullanım:
  python enroll.py <isim> <ses.wav>          # wav'dan embed + kaydet
  python enroll.py <isim> --record <saniye>  # mikrofondan kaydet (sounddevice varsa)
  python enroll.py --list                    # kayıtlı kişileri listele

Aynı isim varsa yeni ÖRNEK eklenir (silmez). Wav en az SPEAKER_ENROLL_MIN_SECONDS
(varsayılan 4sn) olmalı. Model = SPEAKER_MODEL_PATH (models/campplus.onnx).
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

WORKER_DIR = Path(__file__).resolve().parent
load_dotenv(WORKER_DIR / ".env")

import numpy as np  # noqa: E402

from speaker_id import (  # noqa: E402
    WORKER_DIR as _WD,
    SpeakerID,
    SpeakerStore,
    emb_to_bytes,
)

MODEL_ID = os.getenv("SPEAKER_MODEL_ID", "campplus_zh_en_advanced_v1")
ENROLL_MIN_S = float(os.getenv("SPEAKER_ENROLL_MIN_SECONDS", "4.0") or 4.0)


def _resolve(path: str) -> str:
    p = Path(path)
    return str(p if p.is_absolute() else (_WD / p))


def _build_speaker() -> SpeakerID:
    model_path = _resolve(os.getenv("SPEAKER_MODEL_PATH", "models/campplus.onnx"))
    if not os.path.isfile(model_path):
        sys.exit(f"HATA: model bulunamadı: {model_path}")
    return SpeakerID(model_path, MODEL_ID)


def _read_wav(path: str) -> tuple[np.ndarray, int]:
    import soundfile as sf

    data, sr = sf.read(path, dtype="float32", always_2d=False)
    if data.ndim > 1:  # çok kanal → mono ortala
        data = data.mean(axis=1)
    return np.ascontiguousarray(data.astype(np.float32)), int(sr)


def _record(seconds: float) -> tuple[np.ndarray, int]:
    try:
        import sounddevice as sd
    except Exception:  # noqa: BLE001
        sys.exit("HATA: --record için 'sounddevice' gerekli (pip install sounddevice).")
    sr = 16000
    print(f"[enroll] {seconds:.0f}sn kayıt başlıyor — konuşun…", flush=True)
    audio = sd.rec(int(seconds * sr), samplerate=sr, channels=1, dtype="float32")
    sd.wait()
    return np.ascontiguousarray(audio.reshape(-1).astype(np.float32)), sr


def _enroll(name: str, samples: np.ndarray, sr: int, source: str) -> int:
    dur = len(samples) / sr if sr else 0.0
    if dur < ENROLL_MIN_S:
        sys.exit(f"HATA: ses çok kısa ({dur:.1f}sn < {ENROLL_MIN_S:.1f}sn gerekli).")
    sp = _build_speaker()
    emb = sp.embed_samples(samples, sr)
    store = SpeakerStore()
    row = store.create_speaker_sync(name)
    store.add_sample_sync(row["id"], emb_to_bytes(emb), sp.dim, MODEL_ID, source)
    total = next((s["sample_count"] for s in store.list_speakers_sync() if s["id"] == row["id"]), "?")
    print(f"[enroll] '{name}' (id={row['id']}) kaydedildi — dim={sp.dim}, "
          f"süre={dur:.1f}sn, toplam örnek={total}, db={store.path}")
    return 0


def _list() -> int:
    store = SpeakerStore()
    rows = store.list_speakers_sync()
    if not rows:
        print(f"[enroll] kayıtlı kişi yok (db={store.path})")
        return 0
    print(f"[enroll] {len(rows)} kişi (db={store.path}):")
    for r in rows:
        print(f"  #{r['id']}  {r['name']}  örnek={r['sample_count']}  "
              f"dim={r['dim']}  model={r['model_id']}")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv[0] == "--list":
        return _list()
    name = argv[0]
    rest = argv[1:]
    if len(rest) >= 2 and rest[0] == "--record":
        samples, sr = _record(float(rest[1]))
        return _enroll(name, samples, sr, source="mic")
    if len(rest) == 1:
        wav = rest[0]
        if not os.path.isfile(wav):
            sys.exit(f"HATA: wav bulunamadı: {wav}")
        samples, sr = _read_wav(wav)
        return _enroll(name, samples, sr, source=os.path.basename(wav))
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
