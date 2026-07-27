"""Kulak setinin iki duygu satırı için Piper karşılığını üretir (SUNUCUDA).

Bu cümleler `sentences.json`'da değil `../duygu-atlasi/atlas_set.py`'de tanımlı;
`run_piper.py` onları görmez. Piper'da duygu token'ı olmadığı için metin DÜZ gider —
karşılaştırmanın anlamı da bu: Higgs tarafında `<|emotion:affection|>` /
`<|sfx:laughter|>` var, Piper tarafında hiçbir şey.

    /opt/piper-venv/bin/python piper_duygu.py
Çıktı: out/kulak-piper-ham/*.wav  (fetch_outputs.sh geri çeker)
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
from piper import PiperVoice, SynthesisConfig

VOICE = Path("/opt/piper/voices/dfki")  # WER kazananı
OUT = Path(__file__).resolve().parent / "out" / "kulak-piper-ham"
CFG = SynthesisConfig(length_scale=1.2, noise_scale=0.4, noise_w_scale=0.3)

METINLER = {
    "duygu-affection": "Bugün kendine iyi bakmayı unutma olur mu, ben hep buradayım.",
    "duygu-laughter": "Haha, çorabını yine ters giymişsin.",
}


def main() -> None:
    pv = PiperVoice.load(str(VOICE / "model.onnx"),
                         config_path=str(VOICE / "model.onnx.json"), use_cuda=False)
    OUT.mkdir(parents=True, exist_ok=True)
    for k, t in METINLER.items():
        ch = list(pv.synthesize(t, syn_config=CFG))
        a = np.concatenate([np.frombuffer(c.audio_int16_bytes, dtype=np.int16) for c in ch])
        p = OUT / f"{k}.wav"
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(ch[0].sample_rate)
            w.writeframes(a.tobytes())
        print(k, round(len(a) / ch[0].sample_rate, 2), "s →", p)


if __name__ == "__main__":
    main()
