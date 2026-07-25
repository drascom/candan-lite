"""Orpheus-TTS-Turkish (Karayakar/Orpheus-TTS-Turkish-PT-5000) + SNAC 24 kHz decoder.

Token protokolü model kartındaki referans `inference.py`'den birebir alındı:
  giriş : [128259] + text_ids + [128009, 128260, 128261, 128257]
  çıkış : audio token'ları (>=128266), 7'li gruplar hâlinde SNAC'ın 3 katmanına dağıtılır
  eos   : 128258

KLONLAMA YOK: bu checkpoint sentetik veriyle eğitilmiş SABİT bir sesi konuşuyor.
Model kartının `prepare_inputs`'u ref audio parametresi alıyor ama prompt'a KATMIYOR
(zeroprompt satırı kaynakta yorumlanmış). Yani refs/ayhan_ref.wav burada etkisiz —
FreyaTTS gibi "sabit ses" sütunu olarak okunmalı.
"""
from __future__ import annotations

import sys
from pathlib import Path

import torch
from snac import SNAC
from transformers import AutoModelForCausalLM, AutoTokenizer

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, bench  # noqa: E402

REPO = "Karayakar/Orpheus-TTS-Turkish-PT-5000"
EMOTION_FILE = ROOT / "sentences_emotion.json"
DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"

tokenizer = AutoTokenizer.from_pretrained(REPO)
model = AutoModelForCausalLM.from_pretrained(REPO, dtype=torch.float16).to(DEVICE).eval()
snac_model = SNAC.from_pretrained("hubertsiuzdak/snac_24khz").to(DEVICE).eval()

START = torch.tensor([[128259]], dtype=torch.int64)
END = torch.tensor([[128009, 128260, 128261, 128257]], dtype=torch.int64)


def _redistribute(code_list):
    """7'li token gruplarını SNAC'ın 3 katmanına dağıt (model kartı ile birebir)."""
    layer_1, layer_2, layer_3 = [], [], []
    for i in range(len(code_list) // 7):
        layer_1.append(code_list[7 * i])
        layer_2.append(code_list[7 * i + 1] - 4096)
        layer_3.append(code_list[7 * i + 2] - (2 * 4096))
        layer_3.append(code_list[7 * i + 3] - (3 * 4096))
        layer_2.append(code_list[7 * i + 4] - (4 * 4096))
        layer_3.append(code_list[7 * i + 5] - (5 * 4096))
        layer_3.append(code_list[7 * i + 6] - (6 * 4096))
    codes = [
        torch.tensor(layer_1, dtype=torch.int64).unsqueeze(0).to(DEVICE),
        torch.tensor(layer_2, dtype=torch.int64).unsqueeze(0).to(DEVICE),
        torch.tensor(layer_3, dtype=torch.int64).unsqueeze(0).to(DEVICE),
    ]
    with torch.inference_mode():
        return snac_model.decode(codes)


def synth(text: str):
    ids = tokenizer(text, return_tensors="pt").input_ids
    input_ids = torch.cat([START, ids, END], dim=1).to(DEVICE)
    attn = torch.ones_like(input_ids)

    with torch.no_grad():
        gen = model.generate(
            input_ids=input_ids,
            attention_mask=attn,
            max_new_tokens=2048,
            do_sample=True,
            temperature=0.2,
            top_k=10,
            top_p=0.9,
            repetition_penalty=1.9,
            num_return_sequences=1,
            eos_token_id=128258,
        )

    # 128257'den (start-of-audio) sonrasını al, eos'ları at, 7'nin katına kırp
    row = gen[0]
    hits = (row == 128257).nonzero(as_tuple=True)[0]
    row = row[hits[-1].item() + 1:] if len(hits) else row
    row = row[row != 128258]
    codes = [int(t) - 128266 for t in row[: (row.size(0) // 7) * 7]]
    if not codes:
        raise RuntimeError("Orpheus hiç audio token üretmedi")

    audio = _redistribute(codes)
    return audio.detach().squeeze().cpu().numpy(), 24000


# Orpheus klonlamaz → tek sabit ses = "default" seti.
# Ayrıca DUYGU ekseni: model <laugh>/<sigh> gibi etiketleri destekliyor (model kartı),
# etiketli cümleler ayrı sette üretilir.
selected = sys.argv[1:] or ["default", "emotion"]
for name in selected:
    if name == "default":
        bench("orpheus", synth, variant="default")
    elif name == "emotion":
        bench("orpheus", synth, variant="emotion", sentences_file=EMOTION_FILE)
    else:
        sys.exit(f"bilinmeyen set: {name} (geçerli: default, emotion)")
