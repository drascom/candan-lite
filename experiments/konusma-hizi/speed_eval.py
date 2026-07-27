"""`speed_probe.py` çıktısını Whisper geri-dönüşüyle değerlendir → kelime/s + WER.

`token_eval.py` deseninin AYNISI, tek farkı asıl sayının **kelime/saniye** olması:
"Δsüre" sorusu yanlış soruydu — kullanıcının şikâyeti "duyulur biçimde hızlandı mı".

    kelime/s = beklenen metnin kelime sayısı / ses süresi
    kazanç   = koşulun kelime/s'si ÷ tabanın kelime/s'si − 1

Anlaşılırlık AYNI eşikle (`token_eval.WER_OK = 0.25`) ölçülür: hız kazancı
anlaşılırlığı bozarak alınıyorsa kazanç değildir.

    ../tts-local-bench/venvs/whisper/bin/python speed_eval.py
    ... speed_eval.py --only taban tempo:1.15

Çıktı: out/speed_eval.json + ekrana tablo.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent / "tts-local-bench"
sys.path.insert(0, str(BENCH / "runners"))
sys.path.insert(0, str(BENCH))

from asr_eval import missing_words, norm_words, transcribe, wer  # noqa: E402

WER_OK = 0.25          # token_eval.py ile AYNI eşik — sayılar kıyaslanabilir kalsın


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args()

    probe = json.loads((HERE / "out" / "speed_probe.json").read_text(encoding="utf-8"))
    metinler = probe["metinler"]
    kelime = {k: len(norm_words(v)) for k, v in metinler.items()}

    out_path = HERE / "out" / "speed_eval.json"
    rapor: dict = {"wer_esigi": WER_OK, "kosullar": {}}
    if out_path.exists():
        try:
            rapor["kosullar"] = json.loads(out_path.read_text(encoding="utf-8"))["kosullar"]
        except Exception:  # noqa: BLE001
            pass

    adlar = [a for a in probe["kosullar"] if not args.only or a in args.only]
    for idx, ad in enumerate(adlar, 1):
        kosul = probe["kosullar"][ad]
        satirlar, wers, hizlar = [], [], []
        for it in kosul["items"]:
            beklenen = norm_words(metinler[it["metin"]])
            hyp = transcribe(HERE / it["wav"])
            hw = norm_words(hyp)
            w = wer(beklenen, hw)[0]
            hiz = kelime[it["metin"]] / it["sure_s"] if it["sure_s"] else None
            wers.append(w)
            if hiz:
                hizlar.append(hiz)
            satirlar.append({
                "metin": it["metin"], "i": it["i"], "sure_s": it["sure_s"],
                "kelime_s": round(hiz, 3) if hiz else None, "wer": round(w, 4),
                "bas_yendi": bool(beklenen) and beklenen[0] not in hw,
                "atlanan": missing_words(beklenen, hw), "transkript": hyp,
            })
        n = len(satirlar)
        anlasilan = sum(1 for r in satirlar if r["wer"] <= WER_OK)
        rapor["kosullar"][ad] = {
            "ad": ad, "yol": kosul.get("yol"), "n": n,
            "kelime_s": round(sum(hizlar) / len(hizlar), 3) if hizlar else None,
            "ort_sure_s": round(sum(r["sure_s"] for r in satirlar) / n, 3) if n else None,
            "ort_wer": round(sum(wers) / len(wers), 4) if wers else None,
            "anlasilan": anlasilan,
            "bas_yendi": sum(1 for r in satirlar if r["bas_yendi"]),
            "items": satirlar,
        }
        r = rapor["kosullar"][ad]
        print(f"[{idx}/{len(adlar)}] {ad:22s} kelime/s {r['kelime_s']}  "
              f"süre {r['ort_sure_s']}s  WER {r['ort_wer']}  "
              f"anlaşılan {anlasilan}/{n}  baş-yendi {r['bas_yendi']}", flush=True)
        out_path.write_text(json.dumps(rapor, ensure_ascii=False, indent=2),
                            encoding="utf-8")

    taban = rapor["kosullar"].get("taban", {}).get("kelime_s")
    if taban:
        print(f"\n{'koşul':22s} {'kelime/s':>9s} {'kazanç':>8s} {'WER':>7s} {'anlaşılan':>10s}")
        for ad, r in rapor["kosullar"].items():
            if not r.get("kelime_s"):
                continue
            print(f"{ad:22s} {r['kelime_s']:9.3f} {r['kelime_s'] / taban - 1:+7.1%} "
                  f"{r['ort_wer']:7.3f} {r['anlasilan']:6d}/{r['n']}")
    print(f"\nyazıldı: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
