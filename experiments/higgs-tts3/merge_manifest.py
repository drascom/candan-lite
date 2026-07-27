"""out/<set>/*.wav + timings.json → out/manifest.json (compare.html bunu okur).

    python3 merge_manifest.py

Bağımlılık: yok (stdlib). Mac'te de sunucuda da çalışır.
"""
from __future__ import annotations

import json
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"

# Sütun grubu → compare.html'de renk/başlık. Sıra da buradan.
GROUPS = {
    "omnivoice-server": ("omnivoice", 0),
    "higgs-default": ("higgs-default", 1),
    "higgs-clone": ("higgs-clone", 2),
    "higgs-clone-trnorm": ("higgs-clone-trnorm", 3),
}


def wav_info(p: Path) -> dict | None:
    try:
        with wave.open(str(p), "rb") as w:
            return {"duration_s": round(w.getnframes() / w.getframerate(), 3),
                    "sample_rate": w.getframerate()}
    except Exception:  # noqa: BLE001 — bozuk wav manifest'te "yok" görünsün
        return None


def main() -> None:
    sentences = json.loads((ROOT / "sentences.json").read_text(encoding="utf-8"))["sentences"]

    models, timings = [], {}
    for d in sorted((p for p in OUT.iterdir() if p.is_dir()),
                    key=lambda p: GROUPS.get(p.name, ("zz", 99))[1]):
        if not list(d.glob("*.wav")):
            continue
        models.append(d.name)
        tj = d / "timings.json"
        if tj.exists():
            timings[d.name] = json.loads(tj.read_text(encoding="utf-8"))

    report_path = OUT / "higgs_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}

    asr_path = OUT / "asr_eval.json"
    asr = json.loads(asr_path.read_text(encoding="utf-8")) if asr_path.exists() else {}

    manifest = {
        "models": models,
        "groups": {m: GROUPS.get(m, ("diger", 99))[0] for m in models},
        "ozet": {
            m: {k: t.get(k) for k in
                ("count_ok", "count_fail", "total_wall_s", "total_audio_s", "mean_rtf",
                 "uretim", "ilk_ses_gecikmesi", "device")}
            for m, t in timings.items()
        },
        "yukleme": report.get("yukleme"),
        "referans": report.get("referans"),
        "asr": {
            "taban": asr.get("taban"),
            "setler": {k: {kk: v[kk] for kk in ("ortalama_wer", "toplam_atlanan_kelime", "cumle")}
                       for k, v in (asr.get("setler") or {}).items()},
            "detay": asr.get("setler") or {},
        } if asr else None,
        "sentences": [
            {
                "id": s["id"], "text": s["text"], "kategori": s["kategori"],
                "hedef": s["hedef"],
                "beklenen": s.get("expected_spoken"),
                "wavs": {m: info for m in models
                         if (info := wav_info(OUT / m / f"{s['id']}.wav")) is not None},
                "rtf": {m: next((i.get("rtf") for i in (timings.get(m, {}).get("items") or [])
                                 if i["id"] == s["id"]), None) for m in models},
            }
            for s in sentences
        ],
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    n = sum(len(s["wavs"]) for s in manifest["sentences"])
    print(f"{len(models)} set · {len(manifest['sentences'])} cümle · {n} wav")
    for m, t in manifest["ozet"].items():
        print(f"  {m:20s} ok={t.get('count_ok')} fail={t.get('count_fail')} "
              f"rtf={t.get('mean_rtf')}")


if __name__ == "__main__":
    main()
