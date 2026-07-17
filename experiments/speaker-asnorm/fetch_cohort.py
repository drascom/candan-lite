#!/usr/bin/env python3
"""Cohort edin — aile-DIŞI yabancı konuşmacı gömmeleri (AS-norm için).

Kaynak: google/fleurs, tr_tr (Türkçe, CC-BY-4.0), HF datasets-server rows API üstünden.
Her satır = ayrı bir okunmuş cümle (FLEURS'te çok sayıda farklı konuşmacı) → cohort örneği.
İndirilen ham WAV cohort/audio/ (gitignore); tek çıktı = cohort/embeddings.npy (Nx256, L2-norm).

Kullanım:
    python fetch_cohort.py --n 120
    python fetch_cohort.py --n 120 --keep-wav   # ham wav'ı silme (varsayılan siler)
"""

from __future__ import annotations

import argparse
import json
import urllib.request

import numpy as np

from enc import HERE, build_wespeaker, embed_wav_windows

DATASET = "google/fleurs"
CONFIG = "tr_tr"
SPLIT = "validation"
COHORT_DIR = HERE / "cohort"
AUDIO_DIR = COHORT_DIR / "audio"
EMB_PATH = COHORT_DIR / "embeddings.npy"
UA = {"User-Agent": "candan-asnorm-exp/1.0"}


def _get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def rows_page(offset: int, length: int) -> list[dict]:
    url = (
        f"https://datasets-server.huggingface.co/rows?dataset={DATASET}"
        f"&config={CONFIG}&split={SPLIT}&offset={offset}&length={length}"
    )
    return json.loads(_get(url).decode())["rows"]


def collect_audio_urls(n: int) -> list[str]:
    urls: list[str] = []
    offset = 0
    while len(urls) < n:
        page = rows_page(offset, 100)
        if not page:
            break
        for r in page:
            au = r["row"].get("audio") or []
            if au and au[0].get("src"):
                urls.append(au[0]["src"])
        offset += 100
    return urls[:n]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--n", type=int, default=120, help="cohort utterance sayısı")
    p.add_argument("--keep-wav", action="store_true", help="ham WAV'ları silme")
    a = p.parse_args(argv)

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[cohort] {DATASET}/{CONFIG}/{SPLIT} — {a.n} utterance hedef")
    urls = collect_audio_urls(a.n)
    print(f"[cohort] {len(urls)} imzalı audio URL toplandı")

    enc = build_wespeaker()
    embs: list[np.ndarray] = []
    total_bytes = 0
    for i, url in enumerate(urls):
        wav = AUDIO_DIR / f"c{i:04d}.wav"
        try:
            data = _get(url, timeout=60)
            wav.write_bytes(data)
            total_bytes += len(data)
            # Her utterance TEK gömme (tüm dosya) — cohort'ta pencere şart değil.
            e = embed_wav_windows(enc, wav, per_window=False)
            if e:
                embs.append(e[0])
        except Exception as ex:  # noqa: BLE001
            print(f"[atla] {i}: {ex}")
        finally:
            if not a.keep_wav and wav.exists():
                wav.unlink()
        if (i + 1) % 25 == 0:
            print(f"  ... {i + 1}/{len(urls)}  ({total_bytes / 1e6:.1f} MB)")

    if not embs:
        print("[hata] hiç gömme çıkarılamadı")
        return 1
    arr = np.stack(embs).astype(np.float32)
    np.save(EMB_PATH, arr)
    print(f"[cohort] {arr.shape[0]} gömme × {arr.shape[1]}d → {EMB_PATH}")
    print(f"[cohort] indirilen toplam ses ~{total_bytes / 1e6:.1f} MB (wav {'tutuldu' if a.keep_wav else 'silindi'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
