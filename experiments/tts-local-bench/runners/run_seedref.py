"""Klon maliyetinden kaçış — iki deney.

Ölçülen durum: omnivoice-default (auto, referans YOK) RTF 0.89 · omnivoice-clone RTF 3.44.
3.4× fark. Kullanıcı iki klon setini dinledi, kalite farkı YOK → klonlamanın tek işlevi
sesi SABİT tutmak. Amaç: sabit sesi klon maliyetini ödemeden almak.

── DENEY A — sabit seed auto modda konuşmacıyı sabitliyor mu? ─────────────────────────
Bilinen: aynı metin + aynı seed → bit-bit aynı çıktı.
Bilinmeyen: FARKLI metinlerde aynı seed aynı konuşmacıyı mı veriyor?

  autoseed-fixed   15 cümle, auto mod (ref YOK, instruct YOK), her cümleden ÖNCE
                   torch.manual_seed(1234) — hepsi AYNI seed'i görür
  autoseed-random  15 cümle, auto mod, seed sabitlenmemiş (kontrol / alt taban)

── DENEY B — referans uzunluğu / hız takası ──────────────────────────────────────────
refs/omnipick_{3s,6s}.wav — cümle/kelime sınırında kesildi, ref_text her kesite göre
DÜZELTİLDİ (uyuşmazlık klon kalitesini düşürür). 11.57 s hâli = mevcut omnipick-clone.

  omnipick-clone-3s / omnipick-clone-6s

Kullanım: run_seedref.py [autoseed-fixed|autoseed-random|clone-3s|clone-6s ...]
"""
from __future__ import annotations

import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, bench  # noqa: E402

from omnivoice import OmniVoice  # noqa: E402
from omnivoice.models.omnivoice import VoiceClonePrompt  # noqa: E402

DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"
SEED = 1234

model = OmniVoice.from_pretrained("k2-fsa/OmniVoice", device_map=DEVICE, dtype=torch.float32)


def make_auto_synth(seeded: bool):
    """Auto mod: ref YOK, instruct YOK. seeded → her cümlede aynı seed."""
    def synth(text: str):
        if seeded:
            torch.manual_seed(SEED)
            if DEVICE == "mps":
                torch.mps.manual_seed(SEED)
        return model.generate(text=text, language="Turkish")[0], 24000

    return synth


def make_clone_synth(tag: str):
    """Kısaltılmış referansla klon. Prompt bir kez üretilip diske yazılır."""
    wav, txt = ROOT / "refs" / f"omnipick_{tag}.wav", ROOT / "refs" / f"omnipick_{tag}.txt"
    ppath = ROOT / "refs" / f"omnipick_{tag}.omniprompt.pt"
    if not ppath.exists():
        p = model.create_voice_clone_prompt(
            ref_audio=str(wav), ref_text=txt.read_text(encoding="utf-8").strip()
        )
        p.save(str(ppath))
        print(f"[prompt] {ppath.name} ({ppath.stat().st_size / 1024:.0f} KB)")
    prompt = VoiceClonePrompt.load(str(ppath), map_location=DEVICE)

    def synth(text: str):
        return model.generate(text=text, language="Turkish", voice_clone_prompt=prompt)[0], 24000

    return synth


selected = sys.argv[1:] or ["autoseed-fixed", "autoseed-random", "clone-3s", "clone-6s"]

for name in selected:
    if name == "autoseed-fixed":
        bench("autoseed", make_auto_synth(True), variant="fixed")
    elif name == "autoseed-random":
        bench("autoseed", make_auto_synth(False), variant="random")
    elif name in ("clone-3s", "clone-6s"):
        tag = name.split("-")[1]
        bench("omnipick", make_clone_synth(tag), variant=f"clone-{tag}")
    else:
        sys.exit(f"bilinmeyen set: {name}")
