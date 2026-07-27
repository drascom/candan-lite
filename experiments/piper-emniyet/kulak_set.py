"""Kulak seti — aynı cümle HIGGS ve PIPER ile yan yana.

Soru tek: **"Higgs komple ölürse bu sesle idare edilir mi?"** WER onu cevaplamıyor;
`piper-dfki-trnorm` ile `higgs-clone-trnorm` ölçümde birebir aynı çıktı (0.028), oysa
biri Candan'ın klonlanmış sesi + duygu token'ları, diğeri tek sabit robotik ses.
Farkı yalnız kulak görür.

Sekiz satır: altısı ölçüm cümlelerinden (`higgs-tts3/sentences.json` — sayı/tarih/para,
akıcılık, tonlama), ikisi duygu atlasından (Piper'da duygu token'ı YOK; bu iki satır
kaybın boyutunu duyurur).

Ses kaynakları YENİDEN ÜRETİLMEZ, var olan koşumlardan kopyalanır:
  • Higgs → `../higgs-tts3/out/higgs-clone-trnorm/` (canlıdaki kurulumla aynı: Candan
    referansı + trnorm) ve `../duygu-atlasi/out/wav/` (canlı streaming ucundan alınmış)
  • Piper → `out/piper-dfki-trnorm/` (dört Türkçe sesin WER'de kazananı)
Duygu satırlarının Piper karşılığı `piper_duygu.py` ile sunucuda üretilir.

    python3 kulak_set.py && ./serve.sh
"""
from __future__ import annotations

import json
import shutil
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
KULAK = OUT / "kulak"
HIGGS = HERE.parent / "higgs-tts3"
ATLAS = HERE.parent / "duygu-atlasi" / "out" / "wav"
PIPER_SET = "piper-dfki-trnorm"

# (id, başlık, higgs kaynağı, piper kaynağı, kulak notu)
ROWS: list[dict] = [
    {"id": "04-saat", "baslik": "Saat + kesme ekli",
     "higgs": HIGGS / "out/higgs-clone-trnorm/04-saat.wav",
     "piper": OUT / PIPER_SET / "04-saat.wav",
     "not": "14:30'da — ham metinden okunuş. Her iki motor da doğru; ton nasıl?"},
    {"id": "06-yuzde-para", "baslik": "Yüzde + binlik ayıraç",
     "higgs": HIGGS / "out/higgs-clone-trnorm/06-yuzde-para.wav",
     "piper": OUT / PIPER_SET / "06-yuzde-para.wav",
     "not": "%25 · 3.500 TL · 2.625 TL — rakam yığını akıyor mu?"},
    {"id": "n04-binlik-buyuk", "baslik": "Büyük sayı",
     "higgs": HIGGS / "out/higgs-clone-trnorm/n04-binlik-buyuk.wav",
     "piper": OUT / PIPER_SET / "n04-binlik-buyuk.wav",
     "not": "1.250.000 TL — uzun sayıda nefes/duraklama."},
    {"id": "09-uzun-akici", "baslik": "Uzun akıcı cümle",
     "higgs": HIGGS / "out/higgs-clone-trnorm/09-uzun-akici.wav",
     "piper": OUT / PIPER_SET / "09-uzun-akici.wav",
     "not": "40+ kelime, virgüllü. Piper'ın en zorlandığı yer burası olmalı: "
            "sabit tempo, virgülde soluk yok."},
    {"id": "12-soru-kisa", "baslik": "Kısa soru",
     "higgs": HIGGS / "out/higgs-clone-trnorm/12-soru-kisa.wav",
     "piper": OUT / PIPER_SET / "12-soru-kisa.wav",
     "not": "\"Bunu sen mi yaptın?\" — soru tonlaması var mı?"},
    {"id": "13-unlem", "baslik": "Ünlem",
     "higgs": HIGGS / "out/higgs-clone-trnorm/13-unlem.wav",
     "piper": OUT / PIPER_SET / "13-unlem.wav",
     "not": "\"Hadi ya! Gerçekten mi?\" — şaşkınlık duyuluyor mu?"},
    {"id": "duygu-affection", "baslik": "DUYGU · şefkat",
     "higgs": ATLAS / "emotion_affection.wav",
     "piper": OUT / "kulak-piper-ham" / "duygu-affection.wav",
     "not": "Higgs tarafında <|emotion:affection|> var. Piper'da duygu token'ı YOK — "
            "kaybın boyutu bu satırda.",
     "higgs_token": "<|emotion:affection|>"},
    {"id": "duygu-laughter", "baslik": "DUYGU · gülme",
     "higgs": ATLAS / "sfx_laughter.wav",
     "piper": OUT / "kulak-piper-ham" / "duygu-laughter.wav",
     "not": "Higgs <|sfx:laughter|> ile gerçekten gülüyor. Piper \"Haha\"yı sadece "
            "okur.",
     "higgs_token": "<|sfx:laughter|>"},
]

# Duygu satırlarının düz metni (Piper'a token'sız gider — zaten anlamazdı).
DUYGU_METIN = {
    "duygu-affection": "Bugün kendine iyi bakmayı unutma olur mu, ben hep buradayım.",
    "duygu-laughter": "Haha, çorabını yine ters giymişsin.",
}


def sure(p: Path) -> float:
    with wave.open(str(p), "rb") as w:
        return round(w.getnframes() / w.getframerate(), 2)


def main() -> None:
    sents = {s["id"]: s for s in json.loads(
        (HIGGS / "sentences.json").read_text(encoding="utf-8"))["sentences"]}
    rapor = json.loads((OUT / "piper_report.json").read_text(encoding="utf-8"))
    metinler = {i["id"]: i["text"] for i in rapor["setler"][PIPER_SET]["items"]}

    KULAK.mkdir(parents=True, exist_ok=True)
    satirlar, eksik = [], []
    for r in ROWS:
        row = {"id": r["id"], "baslik": r["baslik"], "not": r["not"],
               "metin": sents[r["id"]]["text"] if r["id"] in sents
                        else DUYGU_METIN.get(r["id"], ""),
               "piper_metin": metinler.get(r["id"], DUYGU_METIN.get(r["id"], "")),
               "higgs_token": r.get("higgs_token", "")}
        for motor in ("higgs", "piper"):
            src: Path = r[motor]
            if not src.exists():
                eksik.append(str(src))
                continue
            dst = KULAK / f"{r['id']}-{motor}.wav"
            shutil.copyfile(src, dst)
            row[motor] = {"wav": f"kulak/{dst.name}", "sure_s": sure(dst)}
        satirlar.append(row)

    if eksik:
        print("EKSİK KAYNAK:\n  " + "\n  ".join(eksik))

    (OUT / "kulak.json").write_text(json.dumps({
        "aciklama": "Higgs (canlı kurulum: Candan klonu + trnorm + duygu token'ı) "
                    "karşısında Piper (tr_TR-dfki-medium + trnorm, tek sabit ses).",
        "piper_set": PIPER_SET, "satirlar": satirlar,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(satirlar)} satır → {OUT / 'kulak.json'}   (./serve.sh)")


if __name__ == "__main__":
    main()
