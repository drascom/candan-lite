"""Kulak seti — ölçümü TEMİZ çıkan kombolar, tekil parçaları ve tabanla yan yana.

NEDEN: `kombo_probe.py` + `token_eval.py` "anlaşılıyor mu"yu cevapladı (10 koşulun
onu da TEMİZ). Geriye "kombo tekilden GERÇEKTEN daha iyi mi" sorusu kaldı; onu
yalnız kulak cevaplar. Bu sayfa her satırda dört sesi yan yana koyar:

    kombo (emotion+prosody) · yalnız emotion · yalnız prosody · taban (etiketsiz)

Bütün wav yolları bu dosyanın klasörüne göredir.
Kombo ve taban sesleri ÖLÇÜM koşumundan alınır — yeniden üretilmez; her koşulun
12 örneğinden süresi MEDYANA en yakın olanı seçilir (uç örnek dinletmeyelim).
Yalnız tekil parçalar burada üretilir. Ters sıra (`<|prosody|><|emotion|>`) da
dinlenebilsin diye ayrı düğme olarak konur; ölçümde ikisi de temizdi.

    python3 kombo_set.py           # ya da ../../worker/.venv/bin/python

Üretilmiş wav yeniden üretilmez; baştan istenirse `--yenile`.

Çıktı: out/kombo/<ad>.wav + out/kombo.json → `./serve.sh kombo.html`
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from kombo_probe import CUMLE, KOMBOLAR, _slug  # noqa: E402
from token_probe import _wav, synth  # noqa: E402

OUT = HERE / "out" / "kombo"

# Kullanıcının atlastaki kulak notu — sayfada hatırlatma olarak durur.
KULAK_NOTU = {
    ("surprise+expressive_high", "U1"): "atlasta «iyi»; A/B'de ünlemli cümlede seçilmişti",
    ("surprise+expressive_high", "U2"): "atlasta «iyi» — şaşırmanın zor sınavı",
    ("pride+expressive_high", "P1"): "atlasta «mükemmel»",
    ("contentment+expressive_low", "C1"): "atlasta «iyi»",
    ("awe+expressive_high", "U2"): "kombo «iyi» ama TEKİL awe «kötü» (şuh kalmış, "
                                   "heyecan yok) — canlıda şu an tekil awe var",
}


def _medyan_ornek(kosul: dict) -> str | None:
    """Koşulun 12 örneğinden süresi medyana en yakın olanın wav yolu."""
    items = [it for it in kosul["items"] if it.get("sure_s")]
    if not items:
        return None
    durs = sorted(it["sure_s"] for it in items)
    med = durs[len(durs) // 2]
    return min(items, key=lambda it: abs(it["sure_s"] - med))["wav"]


def _uret(metin: str, hedef: Path, yenile: bool) -> float | None:
    if hedef.exists() and not yenile and hedef.stat().st_size > 44:
        raw = hedef.read_bytes()
        return round(int.from_bytes(raw[40:44], "little") / 2
                     / int.from_bytes(raw[24:28], "little"), 3)
    pcm, sr, _ = synth(metin)
    hedef.write_bytes(_wav(pcm, sr))
    return round(len(pcm) / 2 / sr, 3)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yenile", action="store_true")
    args = ap.parse_args()

    probe = json.loads((HERE / "out" / "token_probe.json").read_text(encoding="utf-8"))
    ev_path = HERE / "out" / "token_eval.json"
    ev = json.loads(ev_path.read_text(encoding="utf-8"))["kosullar"] \
        if ev_path.exists() else {}

    OUT.mkdir(parents=True, exist_ok=True)
    satirlar = []
    for no, (ad, emo, pro, kod, nt) in enumerate(KOMBOLAR, 1):
        cumle = CUMLE[kod]
        eo, oe = f"kombo:{ad}@{kod}/eo", f"kombo:{ad}@{kod}/oe"
        taban_ad = f"kb:{kod}"
        # Ölçüm koşumundan seç — yeniden üretme.
        wav_eo = _medyan_ornek(probe["kosullar"][eo])
        wav_oe = _medyan_ornek(probe["kosullar"][oe])
        wav_taban = _medyan_ornek(probe["kosullar"][taban_ad])
        # Yalnız tekil parçalar burada üretiliyor.
        s_emo = _slug(f"emotion:{emo}@{kod}")
        s_pro = _slug(f"prosody:{pro}@{kod}")
        sure_emo = _uret(f"<|emotion:{emo}|>{cumle}", OUT / f"{s_emo}.wav", args.yenile)
        sure_pro = _uret(f"<|prosody:{pro}|>{cumle}", OUT / f"{s_pro}.wav", args.yenile)

        o = ev.get(eo, {})
        t = ev.get(taban_ad, {})
        satirlar.append({
            "no": no, "ad": ad, "emotion": emo, "prosody": pro,
            "cumle_kod": kod, "cumle": cumle, "ne_icin": nt,
            "kulak_notu": KULAK_NOTU.get((ad, kod), ""),
            "gonderilen": f"<|emotion:{emo}|><|prosody:{pro}|>{cumle}",
            "wav": {
                "kombo": wav_eo, "kombo_ters": wav_oe,
                "emotion": f"out/kombo/{s_emo}.wav",
                "prosody": f"out/kombo/{s_pro}.wav",
                "taban": wav_taban,
            },
            "sure_s": {
                "kombo": o.get("ort_sure_s"), "emotion": sure_emo,
                "prosody": sure_pro, "taban": t.get("ort_sure_s"),
            },
            "olcum": {
                "karar": o.get("karar"), "anlasilan": o.get("anlasilan"),
                "n": o.get("n"), "wer": o.get("ort_wer"),
                "delta_s": round(o["ort_sure_s"] - t["ort_sure_s"], 2)
                if o.get("ort_sure_s") and t.get("ort_sure_s") else None,
                "ters_karar": ev.get(oe, {}).get("karar"),
            },
        })
        print(f"[{no}/{len(KOMBOLAR)}] {ad}@{kod}  emotion {sure_emo}s  "
              f"prosody {sure_pro}s", flush=True)

    path = HERE / "out" / "kombo.json"
    path.write_text(json.dumps({"satirlar": satirlar}, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print(f"\nyazıldı: {path}  →  ./serve.sh kombo.html")


if __name__ == "__main__":
    main()
