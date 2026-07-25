"""Chatterbox Multilingual (ResembleAI) — 2 eksen: SES × EXPRESSIVITY.

Ses ekseni:
  • default : modelin KENDİ doğal sesi (audio_prompt_path yok, ckpt'teki gömülü conds)
  • clone   : refs/ayhan_ref.wav ile zero-shot klon

Expressivity ekseni:
  • (yok ek)  exaggeration=0.5, cfg_weight=0.5   → model varsayılanı
  • -exag     exaggeration=1.0, cfg_weight=0.3   → yüksek duygu
    (ResembleAI yüksek exaggeration'da cfg_weight'in düşürülmesini öneriyor; yoksa tempo hızlanıyor)

Üretilen setler: chatterbox-default, chatterbox-default-exag,
                 chatterbox-clone,   chatterbox-clone-exag

Kullanım:  run_chatterbox.py [set-adı ...]      (argümansız: hepsi)
"""
from __future__ import annotations

import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import REF, bench  # noqa: E402

from chatterbox.mtl_tts import ChatterboxMultilingualTTS  # noqa: E402

DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"

# ad → (referans ses | None, üretim parametreleri)
SETS: dict[str, tuple[str | None, dict]] = {
    "default":      (None,       {"exaggeration": 0.5, "cfg_weight": 0.5}),
    "default-exag": (None,       {"exaggeration": 1.0, "cfg_weight": 0.3}),
    "clone":        (str(REF),   {"exaggeration": 0.5, "cfg_weight": 0.5}),
    "clone-exag":   (str(REF),   {"exaggeration": 1.0, "cfg_weight": 0.3}),
}

selected = sys.argv[1:] or list(SETS)
model = ChatterboxMultilingualTTS.from_pretrained(device=DEVICE)


def make_synth(ref: str | None, params: dict):
    def synth(text: str):
        wav = model.generate(text, language_id="tr", audio_prompt_path=ref, **params)
        return wav.squeeze().detach().cpu().numpy(), model.sr

    return synth


for name in selected:
    if name not in SETS:
        sys.exit(f"bilinmeyen set: {name} (geçerli: {', '.join(SETS)})")
    ref, params = SETS[name]
    bench("chatterbox", make_synth(ref, params), variant=name)
