#!/usr/bin/env python3
"""Speaker-encoder A/B harness — aynı-kişi kosinüs benzerliği.

Bir dizin dolusu WAV (aynı kişinin farklı klipleri) alır, her encoder'dan her WAV için
gömme çıkarır, L2-normalize eder, aynı-kişi ikili kosinüs benzerlik matrisi + özet basar.
Baseline campplus DA aynı harness'tan geçer (elmayla elma).

Encoder'lar:
  - campplus (baseline)      : sherpa-onnx, 192-dim, worker/models/campplus.onnx (SALT-OKUMA)
  - wespeaker resnet34-LM    : sherpa-onnx, 256-dim, models/wespeaker_en_voxceleb_resnet34_LM.onnx
  - ecapa-wavlm (OmniVoice)  : PyTorch+CUDA+s3prl — yalnız --ecapa-model-dir verilir ve
                               bağımlılıklar mevcutsa; yoksa ATLANIR (mesajla).

Kullanım:
    python ab.py <wav-dizini>
    python ab.py --self-test           # sentetik WAV üretir + çalıştırır (boru hattı kanıtı)
    python ab.py <dizin> --ecapa-model-dir /root/tts_eval_models   # sunucuda

Canlı sistemi BOZMAZ: campplus modeli salt okunur, DB'ye/memory'ye dokunulmaz.
"""

from __future__ import annotations

import argparse
import wave
from pathlib import Path
from statistics import median

import numpy as np

HERE = Path(__file__).resolve().parent
# worker/ kökü: campplus baseline'ı BURADAN salt-okur (kopyalamaz).
WORKER_DIR = HERE.parent.parent / "worker"

CAMPPLUS_PATH = WORKER_DIR / "models" / "campplus.onnx"
CAMPPLUS_MODEL_ID = "campplus_zh_en_advanced_v1"
WESPEAKER_PATH = HERE / "models" / "wespeaker_en_voxceleb_resnet34_LM.onnx"
WESPEAKER_MODEL_ID = "wespeaker_en_voxceleb_resnet34_LM"

TARGET_SR = 16000


# ---------------------------------------------------------------------------
# ses yükleme (stdlib wave + numpy; 16-bit PCM WAV → 16k mono float32)
# ---------------------------------------------------------------------------
def load_wav_16k_mono(path: Path) -> np.ndarray:
    """WAV → [-1,1] float32 mono @16k. Basit lineer resample (harici bağımlılık yok)."""
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        width = w.getsampwidth()
        n = w.getnframes()
        raw = w.readframes(n)
    if width == 2:
        a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        # 32-bit: float32 mı int32 mi bilinmez; WAV'ımız int16 üretir, ama güvenli ol.
        a = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 1:
        a = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"desteklenmeyen örnek genişliği: {width}")
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if sr != TARGET_SR and a.size:
        # lineer interpolasyon; encoder içi resample'a güvenmeyip harness'ta tek tipleştir.
        dst_n = round(a.size * TARGET_SR / sr)
        a = np.interp(
            np.linspace(0.0, a.size - 1, dst_n, dtype=np.float64),
            np.arange(a.size),
            a,
        ).astype(np.float32)
    return np.ascontiguousarray(a, dtype=np.float32)


def _l2(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


# ---------------------------------------------------------------------------
# encoder'lar — hepsi aynı arayüz: .name, .dim, .embed(samples16k) -> ham gömme
# ---------------------------------------------------------------------------
class OnnxEncoder:
    """sherpa-onnx SpeakerEmbeddingExtractor (campplus + wespeaker aynı sınıf)."""

    def __init__(self, name: str, model_path: Path, model_id: str):
        import sherpa_onnx

        cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(model_path), num_threads=1, provider="cpu"
        )
        self._ex = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
        self.name = name
        self.model_id = model_id
        self.dim = int(self._ex.dim)

    def embed(self, samples: np.ndarray) -> np.ndarray:
        stream = self._ex.create_stream()
        stream.accept_waveform(sample_rate=TARGET_SR, waveform=np.ascontiguousarray(samples))
        stream.input_finished()
        return np.array(self._ex.compute(stream), dtype=np.float32)


class EcapaWavlmEncoder:
    """OmniVoice ECAPA-TDNN + WavLM (PyTorch). torch+s3prl+ağırlık gerekir.

    sim.py ile aynı kurulum: ECAPA_TDNN_WAVLM(feat_dim=1024, emb_dim=256) +
    wavlm_large_finetune.pth. CUDA varsa cuda, yoksa cpu.
    """

    def __init__(self, name: str, model_dir: Path):
        import torch
        from omnivoice.eval.models.ecapa_tdnn_wavlm import ECAPA_TDNN_WAVLM

        self._torch = torch
        self.name = name
        self.model_id = "ecapa_wavlm_omnivoice"
        self.dim = 256
        sv = model_dir / "speaker_similarity" / "wavlm_large_finetune.pth"
        ssl = model_dir / "speaker_similarity" / "wavlm_large"
        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        self._m = ECAPA_TDNN_WAVLM(
            feat_dim=1024, channels=512, emb_dim=256, sr=TARGET_SR, ssl_model_path=str(ssl) + "/"
        )
        sd = torch.load(str(sv), map_location="cpu")
        self._m.load_state_dict(sd["model"], strict=False)
        self._m.to(self._device).eval()

    def embed(self, samples: np.ndarray) -> np.ndarray:
        t = self._torch.from_numpy(np.ascontiguousarray(samples)).to(self._device)
        with self._torch.no_grad():
            out = self._m([t])
        return out.squeeze(0).float().cpu().numpy().astype(np.float32)


def build_encoders(ecapa_model_dir: Path | None) -> list:
    encs: list = []
    # baseline campplus — salt-okuma, worker/models'ten
    if CAMPPLUS_PATH.is_file():
        try:
            encs.append(OnnxEncoder("campplus (baseline)", CAMPPLUS_PATH, CAMPPLUS_MODEL_ID))
        except Exception as e:  # noqa: BLE001
            print(f"[atla] campplus yüklenemedi: {e}")
    else:
        print(f"[atla] campplus yok: {CAMPPLUS_PATH}")
    # wespeaker
    if WESPEAKER_PATH.is_file():
        try:
            encs.append(OnnxEncoder("wespeaker resnet34-LM", WESPEAKER_PATH, WESPEAKER_MODEL_ID))
        except Exception as e:  # noqa: BLE001
            print(f"[atla] wespeaker yüklenemedi: {e}")
    else:
        print(f"[atla] wespeaker yok: {WESPEAKER_PATH} (indir: README)")
    # ecapa-wavlm — yalnız istendi + bağımlılıklar mevcutsa
    if ecapa_model_dir is not None:
        try:
            encs.append(EcapaWavlmEncoder("ecapa-wavlm (OmniVoice)", ecapa_model_dir))
        except Exception as e:  # noqa: BLE001
            print(f"[atla] ecapa-wavlm yüklenemedi (torch/s3prl/ağırlık?): {e}")
    return encs


# ---------------------------------------------------------------------------
# benzerlik + rapor
# ---------------------------------------------------------------------------
def pairwise(embs: list[np.ndarray]) -> np.ndarray:
    m = np.stack([_l2(e) for e in embs])
    return m @ m.T  # NxN kosinüs (L2-normalize → nokta çarpım)


def upper_vals(sim: np.ndarray) -> list[float]:
    n = sim.shape[0]
    return [float(sim[i, j]) for i in range(n) for j in range(i + 1, n)]


def print_matrix(name: str, labels: list[str], sim: np.ndarray) -> None:
    print(f"\n### {name} — aynı-kişi kosinüs matrisi")
    w = max(len(x) for x in labels)
    head = " " * (w + 2) + "  ".join(f"{i:>5d}" for i in range(len(labels)))
    print(head)
    for i, lab in enumerate(labels):
        row = "  ".join(f"{sim[i, j]:5.3f}" for j in range(len(labels)))
        print(f"{lab:<{w}}  {row}")


def summarize(vals: list[float]) -> tuple[float, float, float, float]:
    if not vals:
        return (0.0, 0.0, 0.0, 0.0)
    return (float(np.mean(vals)), float(median(vals)), min(vals), max(vals))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("audio_dir", nargs="?", help="aynı kişinin WAV klipleri dizini")
    p.add_argument("--ecapa-model-dir", type=Path, default=None,
                   help="ECAPA-WavLM model dizini (ör. /root/tts_eval_models) — sunucuda")
    p.add_argument("--self-test", action="store_true",
                   help="sentetik WAV üret + çalıştır (boru hattı kanıtı)")
    a = p.parse_args(argv)

    if a.self_test:
        audio_dir = HERE / "audio" / "_selftest"
        make_synthetic(audio_dir)
    elif a.audio_dir:
        audio_dir = Path(a.audio_dir)
    else:
        p.error("audio_dir ver ya da --self-test kullan")
        return 2

    wavs = sorted(audio_dir.glob("*.wav"))
    if len(wavs) < 2:
        print(f"En az 2 WAV lazım (bulundu {len(wavs)}): {audio_dir}")
        return 1
    labels = [w.stem for w in wavs]
    print(f"Ses: {audio_dir}  ({len(wavs)} klip)")

    encs = build_encoders(a.ecapa_model_dir)
    if not encs:
        print("Hiç encoder yüklenemedi.")
        return 1

    samples = [load_wav_16k_mono(w) for w in wavs]
    rows = []
    for enc in encs:
        embs = [enc.embed(s) for s in samples]
        sim = pairwise(embs)
        print_matrix(enc.name, labels, sim)
        mean, med, lo, hi = summarize(upper_vals(sim))
        rows.append((enc.name, mean, med, lo, hi, enc.dim))

    # karşılaştırma tablosu
    print("\n### Karşılaştırma (aynı-kişi ikili benzerlik)")
    print(f"{'encoder':<26} {'ort':>6} {'medyan':>7} {'min':>6} {'maks':>6} {'boyut':>6}")
    print("-" * 62)
    for name, mean, med, lo, hi, dim in rows:
        print(f"{name:<26} {mean:6.3f} {med:7.3f} {lo:6.3f} {hi:6.3f} {dim:6d}")
    return 0


# ---------------------------------------------------------------------------
# sentetik WAV üretimi (yalnız boru hattı kanıtı; gerçek ses kullanıcıdan gelir)
# ---------------------------------------------------------------------------
def _write_wav(path: Path, samples: np.ndarray, sr: int = TARGET_SR) -> None:
    pcm = np.clip(samples, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)


def make_synthetic(out_dir: Path) -> None:
    """Sesli formant taklidi: iki 'sözde konuşmacı' — A grubu birbirine yakın, B farklı.
    Amaç GERÇEK doğruluk DEĞİL, harness'ın matris+özet üretmesini göstermek."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)
    dur = 3.0
    t = np.linspace(0, dur, int(TARGET_SR * dur), endpoint=False)

    def voice(f0: float, formants: list[float], jitter: float) -> np.ndarray:
        sig = np.zeros_like(t)
        for k in range(1, 12):  # harmonikler
            sig += (1.0 / k) * np.sin(2 * np.pi * f0 * k * t)
        for fr in formants:  # formant rezonansları
            sig += 0.5 * np.sin(2 * np.pi * fr * t)
        sig += jitter * rng.standard_normal(t.shape)
        return 0.2 * sig / (np.max(np.abs(sig)) + 1e-9)

    # A grubu (aynı "kişi"): aynı f0/formant, farklı gürültü tohumu
    for i in range(3):
        _write_wav(out_dir / f"A{i}.wav", voice(120.0, [700, 1200, 2600], 0.02))
    # B grubu (farklı "kişi"): farklı f0/formant
    for i in range(2):
        _write_wav(out_dir / f"B{i}.wav", voice(210.0, [500, 1800, 3000], 0.02))
    print(f"Sentetik WAV üretildi → {out_dir} (A0-2 aynı, B0-1 farklı)")


if __name__ == "__main__":
    raise SystemExit(main())
