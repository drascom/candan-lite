"""Katalogda vurgu var mı — belgeye ek olarak MODELE de sor.

Resmi `PROMPTING.md` (43 etiket: 21 emotion + 10 prosody + 3 style + 9 sfx)
kelime düzeyinde vurgu etiketi İÇERMİYOR; `emphasis`/`stress`/`accent` geçmiyor,
SSML benzeri bir sözdizimi de yok. Belge ayrıca uyarıyor: *"Only the tags below
are recognized — anything else degrades output or gets read literally."*

Bu betik o uyarıyı DOĞRULAR: uydurma vurgu etiketleri ve SSML denemeleri canlı
uca gönderilir, Whisper'la geri okunur. Etiket harfi harfine okunuyorsa
(transkriptte "emphasis", "stress", "prosody" gibi kelimeler) katalogda gizli
bir vurgu kolu YOK demektir — dolaylı yollara geçilir.

    ../tts-local-bench/venvs/whisper/bin/python katalog_yoklama.py

Çıktı: out/katalog_yoklama.json + ekrana tablo.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "higgs-tts3"))
sys.path.insert(0, str(HERE.parent / "tts-local-bench"))
sys.path.insert(0, str(HERE.parent / "tts-local-bench" / "runners"))

from asr_eval import norm_words, transcribe, wer  # noqa: E402
from token_probe import _wav, synth  # noqa: E402

OUT = HERE / "out"
WAV = OUT / "yoklama"

# Hedef kelime "tek başına"; vurgu gelirse orada gelmeli.
CUMLE = "Sınavdan tam not almışsın, hem de tek başına çalışarak."

# Uydurma/olası vurgu sözdizimleri. Hiçbiri katalogda YOK — soru şu: sessizce
# yok sayılıyor mu, harfi harfine okunuyor mu, yoksa çıktıyı mı bozuyor?
DENEMELER = [
    ("taban", CUMLE),
    ("prosody_emphasis", f"<|prosody:emphasis|>{CUMLE}"),
    ("emphasis_strong", f"<|emphasis:strong|>{CUMLE}"),
    ("prosody_stress", "Sınavdan tam not almışsın, hem de <|prosody:stress|>tek başına çalışarak."),
    ("emphasis_ici", "Sınavdan tam not almışsın, hem de <|emphasis|>tek başına<|/emphasis|> çalışarak."),
    ("ssml", 'Sınavdan tam not almışsın, hem de <emphasis level="strong">tek başına</emphasis> çalışarak.'),
    ("accent_ici", "Sınavdan tam not almışsın, hem de <|accent:tek başına|>çalışarak."),
]
N = 3


def main() -> None:
    WAV.mkdir(parents=True, exist_ok=True)
    beklenen = norm_words(CUMLE)
    rapor: dict = {"cumle": CUMLE, "n": N, "denemeler": {}}
    for idx, (ad, metin) in enumerate(DENEMELER, 1):
        satirlar = []
        for i in range(N):
            pcm, sr, _ = synth(metin)
            p = WAV / f"{ad}-{i}.wav"
            p.write_bytes(_wav(pcm, sr))
            hyp = transcribe(p)
            hw = norm_words(hyp)
            satirlar.append({
                "i": i, "sure_s": round(len(pcm) / 2 / sr, 3),
                "wer": round(wer(beklenen, hw)[0], 4),
                "transkript": hyp,
                # Etiket harfi harfine mi okundu?
                "harfi_harfine": any(k in hyp.lower() for k in
                                     ("emphas", "stress", "accent", "prosody",
                                      "level", "strong", "vurgu")),
            })
        rapor["denemeler"][ad] = {
            "metin": metin, "items": satirlar,
            "ort_wer": round(sum(s["wer"] for s in satirlar) / N, 4),
            "harfi_harfine": sum(1 for s in satirlar if s["harfi_harfine"]),
        }
        r = rapor["denemeler"][ad]
        print(f"[{idx}/{len(DENEMELER)}] {ad:18s} WER {r['ort_wer']:.3f}  "
              f"harfi-harfine {r['harfi_harfine']}/{N}", flush=True)
        for s in satirlar:
            print(f"      · {s['transkript']}")
        (OUT / "katalog_yoklama.json").write_text(
            json.dumps(rapor, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nyazıldı: {OUT / 'katalog_yoklama.json'}", file=sys.stderr)


if __name__ == "__main__":
    main()
