"""Piper — ASR geri-dönüş testi (WER). Higgs bench'iyle AYNI mantık, aynı cümleler.

WER/normalizasyon `tts-local-bench/runners/asr_eval.py`'den import edilir (kopyalanmaz),
böylece buradaki sayılar `higgs-tts3/out/asr_eval.json` ile doğrudan kıyaslanabilir.
Yalnız `expected_spoken`'ı olan cümleler (n01..n14) ölçülür — sayı/tarih/saat/para
yükü orada.

    ../tts-local-bench/venvs/whisper/bin/python asr_eval.py            # tüm setler
    ... asr_eval.py piper-dfki piper-dfki-trnorm                       # alt küme

Çıktı: out/asr_eval.json + ekrana tablo. Bağımlılık: mlx_whisper (Mac).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent / "tts-local-bench"
sys.path.insert(0, str(BENCH / "runners"))
sys.path.insert(0, str(BENCH))

from asr_eval import missing_words, norm_words, transcribe, wer  # noqa: E402

OUT = HERE / "out"
SENTENCES = HERE.parent / "higgs-tts3" / "sentences.json"


def main() -> None:
    sets = sys.argv[1:] or sorted(d.name for d in OUT.iterdir() if d.is_dir())
    expected = {
        s["id"]: s["expected_spoken"]
        for s in json.loads(SENTENCES.read_text(encoding="utf-8"))["sentences"]
        if s.get("expected_spoken")
    }
    report: dict = {"setler": {}}
    for name in sets:
        d = OUT / name
        if not d.is_dir():
            print(f"ATLANDI: {name} yok", file=sys.stderr)
            continue
        rows, wers, miss_total = [], [], 0
        print(f"── {name} " + "─" * 44)
        for sid, exp in expected.items():
            wav = d / f"{sid}.wav"
            if not wav.exists():
                continue
            hyp = transcribe(wav)
            rw, hw = norm_words(exp), norm_words(hyp)
            w, s, dele, ins = wer(rw, hw)
            miss = missing_words(rw, hw)
            wers.append(w)
            miss_total += len(miss)
            rows.append({"id": sid, "wer": round(w, 4), "sub": s, "del": dele, "ins": ins,
                         "atlanan": miss, "beklenen": exp, "transkript": hyp})
            print(f"  {sid:20s} WER {w:.3f}" + (f"  ATLANAN: {miss}" if miss else ""))
        avg = sum(wers) / len(wers) if wers else None
        report["setler"][name] = {
            "ortalama_wer": round(avg, 4) if avg is not None else None,
            "toplam_atlanan_kelime": miss_total, "cumle": len(rows), "items": rows,
        }
        print(f"  → ortalama WER {avg:.3f} | toplam atlanan kelime {miss_total}\n")

    (OUT / "asr_eval.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"yazıldı: {OUT / 'asr_eval.json'}")


if __name__ == "__main__":
    main()
