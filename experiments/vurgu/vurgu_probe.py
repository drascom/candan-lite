"""Vurgu yollarını CANLI streaming ucundan üret (koşul başına N örnek).

`token_probe.py` deseninin aynısı; tek fark koşulların (cümle × yol) çarpımı
olması. Ses `POST /api/tts/stream` (:8809) üzerinden, referans klonuyla,
canlı yolun ta kendisinden alınır — deney koşumu yanıltır (eski `elation`
yanlış teşhisi tam oradan çıkmıştı).

    python3 vurgu_probe.py                       # 4 cümle × 6 yol × 8 örnek
    python3 vurgu_probe.py --yollar kombo        # sonradan eklenen yol
    python3 vurgu_probe.py --n 2 --yollar taban  # hızlı deneme

Üretilmiş wav yeniden üretilmez (yarım koşum kaldığı yerden devam eder);
baştan istenirse `--yenile`.

Çıktı: out/wav/<cümle>-<yol>/NN.wav + out/vurgu_probe.json (ham, ASR'siz).
Ölçüm ikinci adımda: `vurgu_eval.py`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "higgs-tts3"))

from token_probe import _wav, synth  # noqa: E402

import vurgu_set  # noqa: E402

OUT = HERE / "out"
WAV = OUT / "wav"


def _slug(ad: str) -> str:
    return ad.replace("/", "-")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=8, help="koşul başına örnek (spec: ≥8)")
    ap.add_argument("--yollar", nargs="*", default=None, help="yalnız bu yollar")
    ap.add_argument("--cumleler", nargs="*", default=None, help="yalnız bu cümleler")
    ap.add_argument("--yenile", action="store_true")
    args = ap.parse_args()

    rows = vurgu_set.satirlar(args.yollar)
    if args.cumleler:
        rows = [r for r in rows if r["cumle_id"] in args.cumleler]
    if not rows:
        raise SystemExit("eşleşen koşul yok")

    WAV.mkdir(parents=True, exist_ok=True)
    path = OUT / "vurgu_probe.json"
    rapor: dict = {"n": args.n, "kosullar": {}}
    if path.exists():
        try:
            rapor["kosullar"] = json.loads(
                path.read_text(encoding="utf-8")).get("kosullar", {})
        except Exception:  # noqa: BLE001 — bozuk rapor koşumu durdurmasın
            pass

    for idx, row in enumerate(rows, 1):
        d = WAV / _slug(row["ad"])
        d.mkdir(parents=True, exist_ok=True)
        items = []
        for i in range(args.n):
            wav = d / f"{i:02d}.wav"
            if wav.exists() and not args.yenile and wav.stat().st_size > 44:
                raw = wav.stat().st_size - 44
                items.append({"i": i, "wav": str(wav.relative_to(HERE)),
                              "sure_s": round(raw / 2 / 24000, 3)})
                continue
            # HATA OLURSA DUR: yarım koşum kaldığı yerden devam edebilir.
            pcm, sr, _ = synth(row["metin"])
            wav.write_bytes(_wav(pcm, sr))
            items.append({"i": i, "wav": str(wav.relative_to(HERE)),
                          "sure_s": round(len(pcm) / 2 / sr, 3)})
        durs = [it["sure_s"] for it in items]
        rapor["kosullar"][row["ad"]] = dict(
            row, n=args.n, items=items,
            ort_sure_s=round(sum(durs) / len(durs), 3) if durs else None)
        print(f"[{idx}/{len(rows)}] {row['ad']:26s} "
              f"ort {rapor['kosullar'][row['ad']]['ort_sure_s']}s", flush=True)
        # Her koşuldan sonra yaz: koşum yarıda kalırsa kayıp olmasın.
        path.write_text(json.dumps(rapor, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    print(f"\nyazıldı: {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
