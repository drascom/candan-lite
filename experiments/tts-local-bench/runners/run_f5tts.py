"""F5-TTS-Turkish (marduk-ra/F5-TTS-Turkish) — SWivid/F5-TTS mimarisi, TR fine-tune.

Model kartı: "You can set the number of nfe steps to 64 to produce better quality sound."
→ NFE_STEP = 64 (varsayılan 32 değil).

──────────────────────────────────────────────────────────────────────────────────────
REF-LEAK DÜZELTMESİ — neden bu modelde AYRI (kısa) referans kullanıyoruz
──────────────────────────────────────────────────────────────────────────────────────
İlk koşuda referans sesin kendi kelimeleri ("Biraz acelem var." gibi) üretilen her
cümlenin arasına sızdı. Sebep F5-TTS'in kendi ön-işlemesi:

  f5_tts/infer/utils_infer.py:318-346 → referans SESİ 12 saniyeye kırpıyor
  ("Audio is over 12s, clipping short."), ama `ref_text`'e DOKUNMUYOR.

Ortak referansımız 15 s. Ses 12 s'ye kırpıldı, ref_text ise hâlâ 15 s'lik TÜM cümleleri
(son cümle "Biraz acelem var." dahil) içeriyordu → model, karşılığı artık seste olmayan
kelimeleri üretmeye çalıştı → sızıntı.

Çözüm: F5'e özel, 12 s eşiğinin ALTINDA (10.0 s) ve cümle sınırında kesilmiş referans:
  refs/ayhan_ref_f5.wav  + refs/ayhan_ref_f5.txt  (transkript o klipten yeniden çıkarıldı,
  kelime kelime örtüşüyor; ses 9.48'de bitiyor, klip 10.0 s)

DİĞER setlerin referansı (refs/ayhan_ref.wav, 15 s) DEĞİŞMEDİ — onlarda sızıntı yok.
"""
from __future__ import annotations

import sys
from pathlib import Path

import torch
from huggingface_hub import hf_hub_download

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, bench  # noqa: E402

from f5_tts.api import F5TTS  # noqa: E402

REPO = "marduk-ra/F5-TTS-Turkish"
NFE_STEP = 64

# F5'e özel kısa referans (12 s eşiğinin altında kalmalı — yukarıdaki nota bak).
REF_F5 = ROOT / "refs" / "ayhan_ref_f5.wav"
REF_F5_TXT = ROOT / "refs" / "ayhan_ref_f5.txt"

ckpt = hf_hub_download(REPO, "f5_tts_turkish_1000000.safetensors")
vocab = hf_hub_download(REPO, "vocab.txt")

DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"
f5 = F5TTS(model="F5TTS_Base", ckpt_file=ckpt, vocab_file=vocab, device=DEVICE)

RT = REF_F5_TXT.read_text(encoding="utf-8").strip()
if not RT:
    sys.exit(f"{REF_F5_TXT} boş — ref_text olmadan F5 kendi ASR'ıyla tahmin eder, sızıntı riski artar.")
# F5 konvansiyonu: ref_text boşlukla bitmeli, yoksa üretilen metne yapışabiliyor.
if not RT.endswith(" "):
    RT += " "


def synth(text: str):
    wav, sr, _ = f5.infer(
        ref_file=str(REF_F5),
        ref_text=RT,
        gen_text=text,
        nfe_step=NFE_STEP,
        cross_fade_duration=0.15,  # yalnız çok parçalı uzun metinde devreye girer
        remove_silence=False,      # çıktıdaki doğal duraklamalar korunsun
    )
    return wav, sr


bench("f5tts", synth, variant="clone")
