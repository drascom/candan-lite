"""OmniVoice (k2-fsa) — serverdeki (.25:8808) TTS'in lokal karşılığı. BASELINE.

Üç set:
  • omnivoice-default      : modelin KENDİ seçtiği ses ("auto" modu — ref_audio/instruct YOK)
  • omnivoice-clone        : her cümlede ref_audio=<dosya yolu> → wav HER SEFERİNDE yeniden
                             okunuyor + kırpılıyor + tokenize ediliyor
                             (serverdeki `mode=clone` + `use_pinned` davranışının karşılığı)
  • omnivoice-clone-cached : referans BİR KEZ create_voice_clone_prompt() ile tokenize edilip
                             refs/ayhan_ref.omniprompt.pt'ye yazılıyor; 15 cümlenin HEPSİ aynı
                             VoiceClonePrompt nesnesini kullanıyor

`clone` vs `clone-cached` RTF farkı = "referans tokenizasyonunu cache'leyerek ne kazanıyoruz"
sorusunun cevabı. Prompt üretme+kaydetme maliyeti cümle ölçümlerinin DIŞINDA tutulur
(zaten amortize edilen tek seferlik maliyet) ve ayrıca raporlanır.

`normalize_text=False` BİLEREK: sayı/tarih/saat cümlelerinde modelin HAM Türkçesini görmek
istiyoruz. Serverdeki `/opt/omnivoice/pronounce_tr.json` telaffuz yaması da uygulanmıyor.

Kullanım:  run_omnivoice.py [default|clone|clone-cached ...]      (argümansız: üçü de)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import REF, ROOT, bench, ref_text  # noqa: E402

from omnivoice import OmniVoice  # noqa: E402
from omnivoice.models.omnivoice import VoiceClonePrompt  # noqa: E402

DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"
SETS = ("default", "clone", "clone-cached")
PROMPT_PATH = ROOT / "refs" / "ayhan_ref.omniprompt.pt"

selected = sys.argv[1:] or list(SETS)
model = OmniVoice.from_pretrained("k2-fsa/OmniVoice", device_map=DEVICE, dtype=torch.float32)
RT = ref_text()


def make_synth(clone: bool):
    def synth(text: str):
        kwargs = {"text": text, "language": "Turkish"}
        if clone:
            kwargs |= {"ref_audio": str(REF), "ref_text": RT}
        return model.generate(**kwargs)[0], 24000

    return synth


def make_cached_synth():
    """Referansı bir kez tokenize et, diske yaz, geri yükle; hepsinde aynı prompt'u kullan."""
    t0 = time.perf_counter()
    prompt = model.create_voice_clone_prompt(ref_audio=str(REF), ref_text=RT)
    build_s = time.perf_counter() - t0

    prompt.save(str(PROMPT_PATH))
    t0 = time.perf_counter()
    prompt = VoiceClonePrompt.load(str(PROMPT_PATH), map_location=DEVICE)
    load_s = time.perf_counter() - t0

    size_kb = PROMPT_PATH.stat().st_size / 1024
    print(
        f"[prompt] üretim {build_s:.2f}s · diskten yükleme {load_s:.3f}s · "
        f"{PROMPT_PATH.name} {size_kb:.0f} KB (bu maliyet cümle ölçümlerinin DIŞINDA)",
        flush=True,
    )

    def synth(text: str):
        audio = model.generate(text=text, language="Turkish", voice_clone_prompt=prompt)
        return audio[0], 24000

    return synth


for name in selected:
    if name not in SETS:
        sys.exit(f"bilinmeyen set: {name} (geçerli: {', '.join(SETS)})")
    fn = make_cached_synth() if name == "clone-cached" else make_synth(name == "clone")
    bench("omnivoice", fn, variant=name)
