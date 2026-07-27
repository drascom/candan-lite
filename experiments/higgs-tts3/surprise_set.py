"""Şaşırma tonu — aday token'ların KULAK testi (A/B).

NEDEN: canlıda kullanılan `<|emotion:surprise|>` şaşkın DUYULMUYOR. 27 Tem
ölçümünde 21 emotion içinde Δsüre'si en düşük olan oydu (-0.02 s). Anlaşılır ama
iş yapmıyor olabilir. Burada AYNI cümle, farklı aday token'larla üretilir; hangisi
gerçekten şaşkın duyuluyor, kullanıcı kulakla seçer.

İki cümle bilerek farklı sınıfta:
  • A — ünlemli: kelime zaten şaşkınlığı taşıyor, token onu güçlendirecek mi?
  • B — ünlemsiz: şaşkınlığı YALNIZ ton taşımak zorunda. Asıl sınav bu.

Ses `token_probe.py::synth` ile CANLI streaming ucundan alınır (deney koşumu
DEĞİL — eski `elation` yanlış teşhisi tam bundan çıkmıştı). Token cümle başında
ve BİTİŞİK (boşluksuz), sfx kuralıyla aynı.

    ../../worker/.venv/bin/python surprise_set.py

Çıktı: out/surprise/<cumle>-<ad>.wav ve out/surprise.json → `./serve.sh surprise.html`
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from token_probe import _wav, synth  # noqa: E402

OUT = HERE / "out" / "surprise"

# (kod, not, cümle)
SENTENCES: list[tuple[str, str, str]] = [
    ("A", "ünlemli — kelime zaten şaşkınlığı taşıyor",
     "Vay canına! Kargon bir gün erken gelmiş."),
    ("B", "ünlemsiz — şaşkınlığı YALNIZ ton taşımak zorunda",
     "Sınavdan tam not almışsın, hem de tek başına çalışarak."),
]

# (ad, önek). Önek cümleye BİTİŞİK eklenir.
CANDIDATES: list[tuple[str, str, str]] = [
    ("duz", "", "taban — token yok"),
    ("surprise", "<|emotion:surprise|>", "şu an canlıda olan"),
    ("awe", "<|emotion:awe|>", "hayret / afallama"),
    ("arousal", "<|emotion:arousal|>", "uyarılmışlık, yüksek enerji"),
    ("elation", "<|emotion:elation|>", "coşku (27 Tem'de TEMİZ çıktı)"),
    ("expressive_high", "<|prosody:expressive_high|>", "duygu değil, ifade gücü"),
    ("surprise+pitch_high", "<|emotion:surprise|><|prosody:pitch_high|>",
     "KOMBO — ÖLÇÜLMEDİ"),
    ("surprise+expressive_high", "<|emotion:surprise|><|prosody:expressive_high|>",
     "KOMBO — ÖLÇÜLMEDİ"),
]


def _slug(name: str) -> str:
    return name.replace("+", "_").replace(":", "_")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cumleler = []
    hatalar = 0
    for kod, cnot, cumle in SENTENCES:
        adaylar = []
        for ad, prefix, anot in CANDIDATES:
            gonderilen = f"{prefix}{cumle}"
            wav_ad = f"{kod}-{_slug(ad)}.wav"
            try:
                pcm, sr, wall = synth(gonderilen)
            except Exception as exc:  # noqa: BLE001 — HTTP hatası koşul bilgisidir
                hatalar += 1
                adaylar.append({"ad": ad, "not": anot, "gonderilen": gonderilen,
                                "wav": wav_ad, "hata": f"{type(exc).__name__}: {exc}"})
                print(f"  {kod}/{ad:26s} HATA {exc}", flush=True)
                continue
            (OUT / wav_ad).write_bytes(_wav(pcm, sr))
            sure = round(len(pcm) / 2 / sr, 3)
            adaylar.append({"ad": ad, "not": anot, "gonderilen": gonderilen,
                            "wav": wav_ad, "sure_s": sure, "duvar_s": round(wall, 2)})
            print(f"  {kod}/{ad:26s} {sure:5.2f}s  {gonderilen!r}", flush=True)
        taban = next((a.get("sure_s") for a in adaylar if a["ad"] == "duz"), None)
        for a in adaylar:
            if taban and a.get("sure_s"):
                a["sure_delta_s"] = round(a["sure_s"] - taban, 3)
        cumleler.append({"kod": kod, "not": cnot, "cumle": cumle, "adaylar": adaylar})

    (HERE / "out" / "surprise.json").write_text(
        json.dumps({"cumleler": cumleler}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"\nyazıldı: {HERE / 'out' / 'surprise.json'}  (hata: {hatalar})")


if __name__ == "__main__":
    main()
