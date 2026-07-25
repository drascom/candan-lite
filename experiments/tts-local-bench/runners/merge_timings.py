"""out/<model>/timings.json dosyalarını out/timings.json'a birleştirir.

Ayrıca compare.html'in okuduğu out/manifest.json'u üretir (hangi model hangi
cümle için wav üretmiş — sütunlar buradan kuruluyor).

Kullanım: python3 runners/merge_timings.py
Bağımlılık: yok (stdlib).
"""
from __future__ import annotations

import json
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"


def wav_info(p: Path) -> dict | None:
    try:
        with wave.open(str(p), "rb") as w:
            frames, rate = w.getnframes(), w.getframerate()
            return {"duration_s": round(frames / rate, 3), "sample_rate": rate, "channels": w.getnchannels()}
    except Exception:  # noqa: BLE001 — bozuk/yarım wav manifest'te "geçersiz" görünsün
        return None


def group_of(model: str) -> str:
    """Sütun grubu: modelin KENDİ sesi mi, ayhan klonu mu, omnipick mi, duygu seti mi?"""
    if model.endswith("-emotion"):
        return "duygu"
    if model.startswith("autoseed"):
        return "autoseed"  # sabit seed auto modda konuşmacıyı sabitliyor mu deneyi
    if any(k in model for k in ("trnorm", "norm2")):
        return "norm"  # metin normalizasyonu karşılaştırması (sentences_norm.json)
    if model.startswith("omnipick"):
        return "omnipick"  # beğenilen sesten çıkarılan yeni referans + ince ayar deneyleri
    return "klon" if "-clone" in model else "default"


def main() -> None:
    sentences = json.loads((ROOT / "sentences.json").read_text(encoding="utf-8"))["sentences"]
    for extra in ("sentences_emotion.json", "sentences_norm.json"):
        f = ROOT / extra
        if f.exists():
            sentences = sentences + json.loads(f.read_text(encoding="utf-8"))["sentences"]

    models, timings = [], {}

    for d in sorted(p for p in OUT.iterdir() if p.is_dir()):
        wavs = {w.stem: w for w in d.glob("*.wav")}
        if not wavs:
            continue
        models.append(d.name)
        tj = d / "timings.json"
        if tj.exists():
            timings[d.name] = json.loads(tj.read_text(encoding="utf-8"))

    manifest = {
        "models": models,
        "groups": {m: group_of(m) for m in models},
        "sentences": [
            {
                "id": s["id"],
                "text": s["text"],
                "kategori": s["kategori"],
                "hedef": s["hedef"],
                "wavs": {
                    m: info
                    for m in models
                    if (info := wav_info(OUT / m / f"{s['id']}.wav")) is not None
                },
            }
            for s in sentences
        ],
        "ref": "../refs/ayhan_ref.wav",
        "ref_omnipick": "../refs/omnipick.wav",
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "not": "MPS (Apple M4 Pro, 24 GB) ölçümleri. Serverdeki RTX 3090 değerleri DEĞİL.",
        "models": {
            m: {k: t[k] for k in ("count_ok", "count_fail", "total_wall_s", "total_audio_s", "mean_rtf") if k in t}
            for m, t in timings.items()
        },
        "detay": timings,
    }
    (OUT / "timings.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{len(models)} model, {sum(len(s['wavs']) for s in manifest['sentences'])} wav")
    for m, t in summary["models"].items():
        print(f"  {m:26s} ok={t.get('count_ok')} fail={t.get('count_fail')} "
              f"wall={t.get('total_wall_s')}s rtf={t.get('mean_rtf')}")


if __name__ == "__main__":
    main()
