#!/usr/bin/env python3
"""MOSS-Transcribe-Diarize mikrofon test istemcisi (YAN PROJE - Turkce kalite testi).

Mikrofondan kayit alir, 16k mono WAV yapar, uzaktaki servise POST eder ve
diarize edilmis segmentleri okunur bicimde yazdirir.

Kullanim:
  python mic_test.py                 # Enter'a basana kadar kaydeder (push-to-talk)
  python mic_test.py --seconds 8     # sabit 8 saniye kaydeder
  python mic_test.py --file ses.wav  # mikrofon yerine mevcut WAV dosyasini gonderir
  python mic_test.py --server http://192.168.0.25:8909 --language tr

Bagimlilik: sounddevice + numpy + requests  (bkz. requirements.txt)
Not: --file kullanilirsa sounddevice/numpy gerekmez.
"""
from __future__ import annotations

import argparse
import io
import sys
import wave

SR = 16000  # servis 16 kHz bekliyor
GAP_WARN_S = 5.0  # ardisik segmentler arasi bu esikten buyuk bosluk -> uyari


def record_until_enter() -> bytes:
    import numpy as np
    import sounddevice as sd

    print("Kayit basladi. Bitirmek icin ENTER'a bas...", flush=True)
    frames: list = []

    def cb(indata, _frames, _time, _status):
        frames.append(indata.copy())

    with sd.InputStream(samplerate=SR, channels=1, dtype="int16", callback=cb):
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass
    if not frames:
        print("Ses alinamadi.", file=sys.stderr)
        sys.exit(1)
    audio = np.concatenate(frames, axis=0)
    return audio.tobytes()


def record_seconds(seconds: float) -> bytes:
    import numpy as np
    import sounddevice as sd

    print(f"{seconds} saniye kaydediliyor...", flush=True)
    audio = sd.rec(int(seconds * SR), samplerate=SR, channels=1, dtype="int16")
    sd.wait()
    return np.asarray(audio, dtype="int16").tobytes()


def pcm_to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)  # int16
        w.setframerate(SR)
        w.writeframes(pcm)
    return buf.getvalue()


def load_wav_file(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def check_silent_gaps(segments: list) -> list[str]:
    """Ardisik segmentler arasi buyuk bosluklari isaretle (sessiz atlama olabilir)."""
    warnings = []
    for i in range(len(segments) - 1):
        cur_end = segments[i].get("end")
        nxt_start = segments[i + 1].get("start")
        if cur_end is None or nxt_start is None:
            continue
        gap = nxt_start - cur_end
        if gap > GAP_WARN_S:
            warnings.append(
                f"UYARI: {gap:.1f}s sessiz atlama olabilir (seg {i} -> {i + 1})"
            )
    return warnings


def main() -> None:
    ap = argparse.ArgumentParser(description="MOSS diarize mikrofon testi")
    ap.add_argument("--server", default="http://192.168.0.25:8909")
    ap.add_argument("--language", default="tr")
    ap.add_argument("--seconds", type=float, default=None,
                    help="sabit sure kaydet; verilmezse ENTER'a kadar")
    ap.add_argument("--file", default=None, help="mikrofon yerine WAV dosyasi gonder")
    args = ap.parse_args()

    import requests

    if args.file:
        wav = load_wav_file(args.file)
    else:
        pcm = record_seconds(args.seconds) if args.seconds else record_until_enter()
        wav = pcm_to_wav(pcm)

    url = f"{args.server}/transcribe?language={args.language}"
    print(f"Gonderiliyor -> {url} ({len(wav)} bytes)", flush=True)
    resp = requests.post(
        url, data=wav,
        headers={"Content-Type": "audio/wav"}, timeout=600,
    )
    resp.raise_for_status()
    data = resp.json()

    segments = data.get("segments", [])
    if not segments:
        print("(segment yok)")
        print("--- ham cikti ---")
        print(data.get("raw", ""))
        return

    for s in segments:
        print(f"[{s['speaker']} {s['start']:.1f}-{s['end']:.1f}] {s['text']}")

    for w in check_silent_gaps(segments):
        print(w)


if __name__ == "__main__":
    main()
