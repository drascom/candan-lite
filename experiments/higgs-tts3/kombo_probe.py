"""KOMBO ölçümü — kulakla beğenilen `emotion+prosody` çiftleri canlıya girmeden önce.

NEDEN: duygu atlasında (27 Tem) kullanıcı 8 komboyu kulakla dinledi; dördü canlıya
aday çıktı. Ama **hiçbir kombo ölçülmedi** — kural ayakta: ölçülmemiş token canlıya
girmez. Tekil token'ın TEMİZ olması komboyu temiz yapmaz; iki kontrol token'ı arka
arkaya gelince model onları metin sanabilir, cümlenin başını yiyebilir.

`token_probe.py`'nin AYNI takımını kullanır (`synth` + `_wav`) ve ölçümü AYNI
dosyaya (`out/token_probe.json`) ekler; böylece değerlendirme `token_eval.py` ile
hiç değiştirilmeden yapılır ve sayılar 43 token'lık tabloyla kıyaslanabilir olur.

CÜMLELER gerçek kullanım cümleleri — atlasın dersi buydu, nötr tek cümle bir duyguyu
gösteremez. `surprise` iki cümlede birden ölçülür: ünlemli (kelime şaşkınlığı zaten
taşıyor) ve ünlemsiz (şaşkınlığı YALNIZ ton taşıyor — zor sınav).

TOKEN SIRASI bir değişken: `<|emotion:X|><|prosody:Y|>` ile ters sırası aynı
sonucu vermeyebilir. Her kombonun iki sırası da ayrı koşul olarak ölçülür.

    python3 kombo_probe.py                      # tam koşum, kombo başına 12 örnek
    python3 kombo_probe.py --n 4 --only kb:U2   # hızlı deneme

Üretilmiş wav yeniden üretilmez (yarım kalan koşum kaldığı yerden devam eder).

Çıktı: out/tokens/<kosul>/NN.wav + out/token_probe.json →
       `venvs/whisper/bin/python token_eval.py --only <koşullar>`
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from token_probe import OUT, _wav, synth  # noqa: E402


def _slug(ad: str) -> str:
    """Koşul adı → klasör adı. `/` KLASÖR AÇARDI, o yüzden o da temizleniyor."""
    for a, b in ((":", "_"), ("@", "-"), ("/", "-"), ("+", "-")):
        ad = ad.replace(a, b)
    return ad

# (kod, cümle, not) — atlastaki kombo satırlarının cümleleriyle AYNI olsun ki
# kulakla verilen not ile ölçüm aynı sesi anlatsın.
CUMLE: dict[str, str] = {
    "U1": "Vay canına! Kargon tam bir gün erken gelmiş.",
    "U2": "Sınavdan tam not almışsın, hem de tek başına çalışarak.",
    "P1": "Gerçekten başardın işte, hem de tek başına.",
    "C1": "Acelesi yok, her şey yolunda. Önce bir nefes al.",
}

# (kısa ad, emotion, prosody, cümle kodu, ne için)
KOMBOLAR: list[tuple[str, str, str, str, str]] = [
    ("surprise+expressive_high", "surprise", "expressive_high", "U1",
     "[surprise-*] eşlemesi — ünlemli cümle"),
    ("surprise+expressive_high", "surprise", "expressive_high", "U2",
     "[surprise-*] eşlemesi — ünlemsiz cümle (zor sınav)"),
    ("pride+expressive_high", "pride", "expressive_high", "P1",
     "[mood:proud] yükseltmesi — kullanıcı 'mükemmel' dedi"),
    ("contentment+expressive_low", "contentment", "expressive_low", "C1",
     "[mood:calm] gözden geçirmesi"),
    ("awe+expressive_high", "awe", "expressive_high", "U2",
     "yedek aday — tek başına awe KÖTÜ çıkmıştı"),
]


def conditions(siralar: tuple[str, ...] = ("eo", "oe")) -> list[dict]:
    """Koşul listesi: cümle tabanları + her kombo, istenen token sıralarıyla.

    `eo` = emotion önce (atlasta dinlenen sıra), `oe` = prosody önce.
    """
    rows: list[dict] = []
    for kod, cumle in CUMLE.items():
        rows.append({"ad": f"kb:{kod}", "metin": cumle, "beklenen": cumle,
                     "sinif": "kombo-taban", "cumle": kod, "not": "etiketsiz taban"})
    for ad, emo, pro, kod, nt in KOMBOLAR:
        cumle = CUMLE[kod]
        e, p = f"<|emotion:{emo}|>", f"<|prosody:{pro}|>"
        for sira in siralar:
            onek = e + p if sira == "eo" else p + e
            rows.append({
                "ad": f"kombo:{ad}@{kod}/{sira}",
                "metin": onek + cumle, "beklenen": cumle,
                "sinif": "kombo", "cumle": kod, "not": nt,
            })
    return rows


def _var_olan(d: Path, i: int, sr_varsayilan: int = 24000) -> dict | None:
    """Var olan wav'ı yeniden üretme — yarım koşum kaldığı yerden devam etsin."""
    wav = d / f"{i:02d}.wav"
    if not wav.exists():
        return None
    raw = wav.read_bytes()
    if len(raw) <= 44:
        return None
    sr = struct.unpack("<I", raw[24:28])[0] or sr_varsayilan
    veri = struct.unpack("<I", raw[40:44])[0]
    return {"i": i, "wav": str(wav.relative_to(HERE)), "bayt": veri,
            "sure_s": round(veri / 2 / sr, 3), "duvar_s": None}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=12, help="kombo başına örnek (spec: ≥12)")
    ap.add_argument("--only", nargs="*", default=None, help="yalnız bu koşullar")
    ap.add_argument("--sira", nargs="*", default=["eo", "oe"],
                    choices=["eo", "oe"], help="ölçülecek token sıraları")
    args = ap.parse_args()

    rows = conditions(tuple(args.sira))
    if args.only:
        want = set(args.only)
        rows = [r for r in rows if r["ad"] in want or _slug(r["ad"]) in want]
        if not rows:
            raise SystemExit(f"eşleşen koşul yok: {args.only}")

    path = HERE / "out" / "token_probe.json"
    report = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    report.setdefault("kosullar", {})
    report["kombo_cumleler"] = CUMLE

    OUT.mkdir(parents=True, exist_ok=True)
    for idx, row in enumerate(rows, 1):
        d = OUT / _slug(row["ad"])
        d.mkdir(parents=True, exist_ok=True)
        items, fails, yeni = [], 0, 0
        for i in range(args.n):
            eski = _var_olan(d, i)
            if eski is not None:
                items.append(eski)
                continue
            try:
                pcm, sr, wall = synth(row["metin"])
            except Exception as exc:  # noqa: BLE001 — HTTP hatası koşul bilgisidir
                fails += 1
                items.append({"i": i, "hata": f"{type(exc).__name__}: {exc}"})
                continue
            (d / f"{i:02d}.wav").write_bytes(_wav(pcm, sr))
            yeni += 1
            items.append({"i": i, "wav": str((d / f"{i:02d}.wav").relative_to(HERE)),
                          "bayt": len(pcm), "sure_s": round(len(pcm) / 2 / sr, 3),
                          "duvar_s": round(wall, 2)})
        durs = [it["sure_s"] for it in items if "sure_s" in it]
        report["kosullar"][row["ad"]] = {
            "metin": row["metin"], "beklenen": row["beklenen"],
            "sinif": row["sinif"], "cumle": row["cumle"], "not": row["not"],
            "n": args.n, "http_hata": fails,
            "bos": sum(1 for it in items if it.get("bayt") == 0),
            "ort_sure_s": round(sum(durs) / len(durs), 3) if durs else None,
            "min_sure_s": round(min(durs), 3) if durs else None,
            "items": items,
        }
        print(f"[{idx}/{len(rows)}] {row['ad']:44s} "
              f"ort {report['kosullar'][row['ad']]['ort_sure_s']}s "
              f"yeni {yeni} hata {fails}", flush=True)
        # Her koşuldan sonra yaz: koşum yarıda kalırsa kayıp olmasın.
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    print(f"\nyazıldı: {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
