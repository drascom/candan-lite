"""`token_probe.py` çıktısını Whisper geri-dönüşüyle değerlendir → TEMİZ / BOZUK.

Karar tek bir sayıya değil ÜÇ ölçüte dayanıyor; `elation`'ın bozukluğu üçünden
ikisinde birden görünüyordu (boş çıktı + baştaki kelimenin düşmesi), WER ortalaması
tek başına bunu yumuşatıp gizleyebilir:

  1. **boş / çok kısa çıktı** — hiç ses yok ya da medyanın yarısından kısa.
  2. **cümlenin BAŞINI yeme** — beklenen ilk kelime transkriptte yok.
  3. **UYDURMA konuşma** — medyanın 2 katından uzun çıktı. `speed_very_slow` 24
     örnekten birinde cümlenin ÖNÜNE 4 saniyelik anlamsız gevezelik ekledi
     ("Main testi Enya, İvet ve İyali devirsiniz…"). Bu tek örnek WER ortalamasında
     kaybolur ama canlıda felakettir — o yüzden ayrı ölçüt.
  4. **anlaşılırlık** — örnek başına WER ≤ EŞİK ise o örnek "anlaşıldı" sayılır.

Bir koşul TEMİZ sayılır: anlaşılan örnek oranı ≥ %90 ve yukarıdaki üç kusurdan
hiçbiri yok. Sınırda kalanlar ŞÜPHELİ — canlıya yalnız TEMİZ olanlar alınır.

    ... token_eval.py --yeniden-karar     # transkript ETME, kayıtlı sonuçtan yeniden karar ver

WER/normalizasyon `tts-local-bench/runners/asr_eval.py`'den import edilir (kopya
değil): bütün TTS ölçümlerimiz aynı sayıyı üretsin.

    experiments/tts-local-bench/venvs/whisper/bin/python token_eval.py
    ... token_eval.py --only "emotion:elation" baseline

Çıktı: out/token_eval.json + ekrana tablo.
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

# Örnek başına anlaşılırlık eşiği. 5-6 kelimelik cümlede tek kelime hatası
# WER 0.17-0.20 ediyor; 0.25 "bir kelime kayabilir ama cümle anlaşıldı"yı geçirir,
# iki kelime kaybını geçirmez.
WER_OK = 0.25
TEMIZ_ORAN = 0.90


def decide(rows: list[dict], n: int, empty: int) -> tuple[str, dict]:
    """Örnek satırlarından KOŞUL kararı — transkript gerektirmez (yeniden karar için)."""
    ok = sum(1 for r in rows if r.get("wer") is not None and r["wer"] <= WER_OK)
    short = sum(1 for r in rows if r.get("kisa"))
    long_ = sum(1 for r in rows if r.get("uzun"))
    eaten = sum(1 for r in rows if r.get("bas_yendi"))
    temiz = (ok / n >= TEMIZ_ORAN) and not (empty or short or long_ or eaten)
    supheli = (not temiz) and ok / n >= 0.75
    return ("TEMİZ" if temiz else "ŞÜPHELİ" if supheli else "BOZUK"), {
        "anlasilan": ok, "cok_kisa": short, "uydurma": long_, "bas_yendi": eaten,
    }


def evaluate(cond: dict, name: str) -> dict:
    exp_words = norm_words(cond["beklenen"])
    first = exp_words[0] if exp_words else ""
    rows, wers = [], []
    empty = 0
    durs = [it["sure_s"] for it in cond["items"] if "sure_s" in it]
    med = sorted(durs)[len(durs) // 2] if durs else 0.0

    for it in cond["items"]:
        if "wav" not in it:
            rows.append({"i": it["i"], "hata": it.get("hata")})
            empty += 1
            continue
        if it["bayt"] == 0:
            empty += 1
            rows.append({"i": it["i"], "bos": True})
            continue
        hyp = transcribe(HERE / it["wav"])
        hw = norm_words(hyp)
        if cond.get("onek_serbest") and first in hw:
            # sfx taklidi ("Haha"/"Hah"/"Aaa") — Whisper onu değişken yazıyor ya da
            # hiç yazmıyor. Bu ÖLÇÜM ARTEFAKTI; cümlenin ilk kelimesinden başla.
            hw = hw[hw.index(first):]
        w = wer(exp_words, hw)[0]
        wers.append(w)
        rows.append({"i": it["i"], "wer": round(w, 4), "sure_s": it["sure_s"],
                     "kisa": it["sure_s"] < med * 0.5,
                     "uzun": it["sure_s"] > med * 2.0,
                     "bas_yendi": bool(first) and first not in hw,
                     "atlanan": missing_words(exp_words, hw), "transkript": hyp})

    n = len(cond["items"])
    karar, sayim = decide(rows, n, empty)
    return {
        "ad": name, "sinif": cond["sinif"], "cumle": cond["cumle"],
        "n": n, "bos": empty, **sayim,
        "ort_wer": round(sum(wers) / len(wers), 4) if wers else None,
        "ort_sure_s": cond.get("ort_sure_s"),
        "karar": karar, "items": rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--yeniden-karar", action="store_true",
                    help="transkript ETME; kayıtlı sonuçtan yalnız kararı yenile "
                         "(ölçüt değişince 400+ wav'ı yeniden çevirmeyelim)")
    args = ap.parse_args()

    probe = json.loads((HERE / "out" / "token_probe.json").read_text(encoding="utf-8"))
    conds = probe["kosullar"]
    names = [n for n in conds if not args.only or n in args.only]

    out_path = HERE / "out" / "token_eval.json"
    report = {"wer_esigi": WER_OK, "temiz_oran": TEMIZ_ORAN, "kosullar": {}}
    if out_path.exists():
        try:
            report["kosullar"] = json.loads(
                out_path.read_text(encoding="utf-8")).get("kosullar", {})
        except Exception:  # noqa: BLE001
            pass

    for i, name in enumerate(names, 1):
        if args.yeniden_karar:
            res = report["kosullar"].get(name)
            if res is None:
                continue
            durs = [r["sure_s"] for r in res["items"] if "sure_s" in r]
            med = sorted(durs)[len(durs) // 2] if durs else 0.0
            for r in res["items"]:
                if "sure_s" in r:
                    r["kisa"] = r["sure_s"] < med * 0.5
                    r["uzun"] = r["sure_s"] > med * 2.0
            res["karar"], sayim = decide(res["items"], res["n"], res["bos"])
            res.update(sayim)
        else:
            res = evaluate(conds[name], name)
        report["kosullar"][name] = res
        print(f"[{i}/{len(names)}] {name:34s} {res['karar']:8s} "
              f"anlaşılan {res['anlasilan']}/{res['n']}  WER {res['ort_wer']}  "
              f"boş {res['bos']}  baş-yendi {res['bas_yendi']}  "
              f"süre {res['ort_sure_s']}s", flush=True)
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    print(f"\nyazıldı: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
