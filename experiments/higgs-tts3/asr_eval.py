"""ASR geri-dönüş testi — Higgs vs OmniVoice, Türkçe zor vakalarda kelime düşüyor mu?

Kulakla değil ÖLÇEREK: her wav Whisper'la yazıya çevrilir, `expected_spoken` ile
kıyaslanır; WER + ATLANAN KELİME raporlanır ("başlıyor" tipi düşmeleri ikincisi
yakalar — OmniVoice'ta yaşanan hataydı, bkz. handoff/2026-07-25-trnorm-production.md).

WER/normalizasyon mantığı tts-local-bench/runners/asr_eval.py'den AYNEN kullanılır
(kopyalanmaz, import edilir) — iki bench'in sayıları karşılaştırılabilir olsun diye.

    experiments/tts-local-bench/venvs/whisper/bin/python asr_eval.py
    ... asr_eval.py higgs-clone omnivoice-server       # alt küme

Yalnız `expected_spoken`'ı olan cümleler (n01..n14) ölçülür.
Çıktı: out/asr_eval.json + ekrana tablo.  Bağımlılık: mlx_whisper (Mac).
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
DEFAULT_SETS = ["omnivoice-server", "higgs-default", "higgs-clone"]


def main() -> None:
    sets = sys.argv[1:] or DEFAULT_SETS
    expected = {
        s["id"]: s["expected_spoken"]
        for s in json.loads((HERE / "sentences.json").read_text(encoding="utf-8"))["sentences"]
        if s.get("expected_spoken")
    }

    # Taban gürültüsü: metni kesin bilinen referans klip. Set WER'leri bunun
    # ÜSTÜNDEKİ kısım kadar anlamlı.
    ref_wav, ref_txt = BENCH / "refs" / "omnipick.wav", BENCH / "refs" / "omnipick.txt"
    report: dict = {"setler": {}}
    if ref_wav.exists():
        hyp = transcribe(ref_wav)
        base = wer(norm_words(ref_txt.read_text(encoding="utf-8")), norm_words(hyp))[0]
        print(f"TABAN (refs/omnipick.wav): WER {base:.3f}\n")
        report["taban"] = {"wav": "tts-local-bench/refs/omnipick.wav",
                           "wer": round(base, 4), "transkript": hyp}

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
