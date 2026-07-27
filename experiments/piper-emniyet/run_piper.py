"""Piper Türkçe — SUNUCUDA (CPU/ONNX) koşan ölçüm. Higgs ile aynı 29 cümle.

Higgs bench'iyle (experiments/higgs-tts3) karşılaştırılabilir olsun diye cümleler
`sentences.json`'dan BİREBİR okunur; wav'lar aynı isimle yazılır, ASR değerlendirmesi
aynı `asr_eval` mantığını kullanır.

Dört Türkçe ses × iki metin yolu ölçülür:
  • ham     — metin modele OLDUĞU GİBİ gider (Higgs'i seçme sebebimiz buydu: ham
              metinden `%25`, `14:30'da` doğru okunuyordu. Piper okuyabiliyor mu?)
  • trnorm  — metin önce `worker/trnorm.py`'den geçer

GPU'ya HİÇ dokunulmaz (onnxruntime CPU). Higgs servisi çalışırken koşulabilir.

    /opt/piper-venv/bin/python run_piper.py              # 4 ses × 2 yol
    /opt/piper-venv/bin/python run_piper.py dfki eren    # alt küme
    /opt/piper-venv/bin/python run_piper.py --only 04-saat 06-yuzde-para

Çıktı: out/piper-<ses>[-trnorm]/<id>.wav + out/piper_report.json
"""
from __future__ import annotations

import argparse
import json
import resource
import sys
import time
import wave
from pathlib import Path

import numpy as np
from piper import PiperVoice, SynthesisConfig

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from trnorm import normalize_tr  # noqa: E402

VOICES_DIR = Path("/opt/piper/voices")
OUT = HERE / "out"
SENTENCES = HERE / "sentences.json"

# Model kartlarındaki inference varsayılanları (tts-local-bench/run_piper.py ile aynı;
# eski ölçümle kıyas bozulmasın diye DEĞİŞTİRİLMEDİ).
CFG = SynthesisConfig(length_scale=1.2, noise_scale=0.4, noise_w_scale=0.3)

ALL_VOICES = ("dfki", "fahrettin", "fettah", "eren")


def rss_mib() -> int:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) // 1024
    return -1


def peak_rss_mib() -> int:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024


def write_wav(path: Path, audio: np.ndarray, sr: int) -> float:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(audio.tobytes())
    return len(audio) / float(sr)


def synth(voice: PiperVoice, text: str) -> tuple[np.ndarray, int, float]:
    """Ses + örnekleme hızı + İLK PARÇA gecikmesi (sn).

    Piper `synthesize()` bir üreteçtir; ilk parçanın gelme anı Higgs'in
    `first_block_s`'iyle aynı anlamda ölçülebilir.
    """
    t0 = time.perf_counter()
    first = None
    parts = []
    sr = 22050
    for c in voice.synthesize(text, syn_config=CFG):
        if first is None:
            first = time.perf_counter() - t0
        sr = c.sample_rate
        parts.append(np.frombuffer(c.audio_int16_bytes, dtype=np.int16))
    if not parts:
        raise RuntimeError("Piper hiç ses üretmedi")
    return np.concatenate(parts), sr, first or 0.0


def run_set(name: str, voice: PiperVoice, sents: list[dict], trnorm: bool,
            only: set[str] | None) -> dict:
    d = OUT / name
    rows, ok, fail = [], 0, 0
    print(f"── {name} " + "─" * (46 - len(name)))
    for s in sents:
        if only and s["id"] not in only:
            continue
        text = normalize_tr(s["text"]) if trnorm else s["text"]
        try:
            t0 = time.perf_counter()
            audio, sr, first_s = synth(voice, text)
            wall = time.perf_counter() - t0
            audio_s = write_wav(d / f"{s['id']}.wav", audio, sr)
            ok += 1
            rows.append({"id": s["id"], "wall_s": round(wall, 3),
                         "audio_s": round(audio_s, 3),
                         "rtf": round(wall / audio_s, 4) if audio_s else None,
                         "first_chunk_s": round(first_s, 4),
                         "sample_rate": sr, "text": text, "ok": True})
        except Exception as e:  # noqa: BLE001 — tek cümle patlarsa set devam etsin
            fail += 1
            rows.append({"id": s["id"], "ok": False, "hata": repr(e), "text": text})
            print(f"  {s['id']:20s} HATA {e!r}")
    tw = sum(r["wall_s"] for r in rows if r["ok"])
    ta = sum(r["audio_s"] for r in rows if r["ok"])
    mean_rtf = tw / ta if ta else None
    firsts = sorted(r["first_chunk_s"] for r in rows if r["ok"])
    print(f"  → {ok} ok / {fail} hata · toplam {tw:.2f} s iş, {ta:.1f} s ses · "
          f"RTF {mean_rtf:.4f} · ilk parça medyan {firsts[len(firsts)//2]:.3f} s")
    return {"count_ok": ok, "count_fail": fail, "total_wall_s": round(tw, 2),
            "total_audio_s": round(ta, 2),
            "mean_rtf": round(mean_rtf, 4) if mean_rtf else None,
            "first_chunk_median_s": round(firsts[len(firsts) // 2], 4) if firsts else None,
            "first_chunk_min_s": round(firsts[0], 4) if firsts else None,
            "trnorm": trnorm, "items": rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("voices", nargs="*", default=None)
    ap.add_argument("--only", nargs="*", default=None)
    a = ap.parse_args()
    voices = a.voices or list(ALL_VOICES)
    only = set(a.only) if a.only else None

    sents = json.loads(SENTENCES.read_text(encoding="utf-8"))["sentences"]
    OUT.mkdir(exist_ok=True)
    rss0 = rss_mib()
    report: dict = {"device": "cpu (onnxruntime) · sunucu 192.168.0.25",
                    "rss_bos_mib": rss0, "sesler": {}, "setler": {}}

    for v in voices:
        vd = VOICES_DIR / v
        t0 = time.perf_counter()
        pv = PiperVoice.load(str(vd / "model.onnx"),
                             config_path=str(vd / "model.onnx.json"), use_cuda=False)
        load_s = time.perf_counter() - t0
        rss_after = rss_mib()
        cfg = json.loads((vd / "model.onnx.json").read_text(encoding="utf-8"))
        report["sesler"][v] = {
            "load_s": round(load_s, 3),
            "onnx_mib": round((vd / "model.onnx").stat().st_size / 2**20, 1),
            "rss_yukleme_sonrasi_mib": rss_after,
            "sample_rate": cfg.get("audio", {}).get("sample_rate"),
            "num_speakers": cfg.get("num_speakers"),
            "dataset": cfg.get("dataset"),
            "quality": cfg.get("audio", {}).get("quality") or cfg.get("quality"),
        }
        print(f"\n[{v}] yükleme {load_s:.2f} s · RSS {rss_after} MiB")
        report["setler"][f"piper-{v}"] = run_set(f"piper-{v}", pv, sents, False, only)
        report["setler"][f"piper-{v}-trnorm"] = run_set(
            f"piper-{v}-trnorm", pv, sents, True, only)
        del pv

    report["rss_tepe_mib"] = peak_rss_mib()
    (OUT / "piper_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nRSS tepe (tüm süreç, 4 ses arka arkaya): {report['rss_tepe_mib']} MiB")
    print(f"yazıldı: {OUT / 'piper_report.json'}")


if __name__ == "__main__":
    main()
