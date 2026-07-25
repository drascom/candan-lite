"""OmniVoice — "beğenilen ses" referansıyla klon + ince ayar deneyleri.

Referans: `refs/omnipick.wav` = out/omnivoice-default/09-uzun-akici.wav (11.57 s, 24 kHz).
Kullanıcı bu erkek sesi beğendi. Auto modda üretildiği için seed'i yok, tekrar üretilemez —
ama wav elimizde, o yüzden KLON REFERANSI olarak kullanıyoruz.
ref_text ASR ile çıkarılmadı: cümle zaten sentences.json'da (09-uzun-akici), birebir alındı.

Prompt bir kez `create_voice_clone_prompt()` ile üretilip `refs/omnipick.omniprompt.pt`'ye
yazılır, tüm setlerde `.load()` edilir (production'da yapılacak şeklin aynısı).

Setler:
  omnipick-clone     15 cümle, varsayılan (num_step=32, guidance_scale=2.0, normalize_text=False)
  omnipick-norm      aynı + normalize_text=True   → rakam/tarih/saat kaynağında düzeliyor mu
  omnipick-step64    num_step=64, 5 cümle          → kalite/gecikme takası
  omnipick-instruct  instruct A/B testi (aşağıya bak)

── instruct A/B — DETERMİNİSTİK ────────────────────────────────────────────────────────
OmniVoice üretimi stokastik: aynı girdiyle iki koşu farklı dalga formu verir (ölçüldü).
Bu yüzden "a ile b aynı mı" sorusu ancak SEED SABİTLENİRSE anlamlı. `manual_seed` ile
üretimin bit-bit tekrarlanabilir olduğu doğrulandı (aynı seed → array_equal=True).
Her cümle için sabit seed kullanılır, üç varyant AYNI seed'i görür:

  a-noinstruct   klon prompt + instruct YOK
  b-highpitch    klon prompt + instruct="high pitch"
  c-designonly   klon prompt YOK, sadece instruct="high pitch" (voice-design — kontrol)

a == b (bit-bit) ise → referans varken instruct YOK SAYILIYOR.
c farklı ise → instruct genel olarak çalışıyor, ama klonla birlikte çalışmıyor.
Bu, serverdeki `worker/omnivoice_tts.py` MOOD_PRESETS'inin (instruct="high/low pitch")
gerçekten iş yapıp yapmadığını belirler.

Kullanım: run_omnipick.py [clone|norm|step64|instruct ...]   (argümansız: hepsi)
"""
from __future__ import annotations

import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, bench  # noqa: E402

from omnivoice import OmniVoice  # noqa: E402
from omnivoice.models.omnivoice import OmniVoiceGenerationConfig, VoiceClonePrompt  # noqa: E402

DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"
REF = ROOT / "refs" / "omnipick.wav"
REF_TXT = ROOT / "refs" / "omnipick.txt"
PROMPT_PATH = ROOT / "refs" / "omnipick.omniprompt.pt"
NORM_FILE = ROOT / "sentences_norm.json"
# ASR değerlendirmesinde eksik kelime çıkan cümleler → speed=0.9 hipotez testi
SLOW_IDS = ["n01-yuzde-para", "n04-binlik-buyuk", "n06-tarih", "n07-saat-sifir",
            "n10-para-simge", "n13-ingilizce"]

# step64 / instruct alt-kümeleri
STEP64_IDS = ["01-simdi", "02-isine", "03-problemli-karisik", "09-uzun-akici", "04-saat"]
INSTRUCT_IDS = ["01-simdi", "02-isine", "04-saat", "09-uzun-akici"]
SEED_BASE = 4242  # cümle başına sabit seed: SEED_BASE + sıra

model = OmniVoice.from_pretrained("k2-fsa/OmniVoice", device_map=DEVICE, dtype=torch.float32)


def get_prompt() -> VoiceClonePrompt:
    """Prompt'u bir kez üret + diske yaz; sonra hep diskten yükle."""
    if not PROMPT_PATH.exists():
        p = model.create_voice_clone_prompt(
            ref_audio=str(REF), ref_text=REF_TXT.read_text(encoding="utf-8").strip()
        )
        p.save(str(PROMPT_PATH))
        print(f"[prompt] üretildi → {PROMPT_PATH.name} ({PROMPT_PATH.stat().st_size / 1024:.0f} KB)")
    return VoiceClonePrompt.load(str(PROMPT_PATH), map_location=DEVICE)


PROMPT = get_prompt()


def make_synth(*, normalize_text=False, num_step=32, instruct=None, use_prompt=True, seeded=False):
    counter = {"i": 0}

    def synth(text: str):
        kwargs: dict = {"text": text, "language": "Turkish", "normalize_text": normalize_text}
        if use_prompt:
            kwargs["voice_clone_prompt"] = PROMPT
        if instruct is not None:
            kwargs |= {"instruct": instruct, "speed": 1.0}  # speed SABİT: hız etkisini ayıkla
        elif seeded:
            kwargs["speed"] = 1.0  # a-noinstruct de aynı speed'i görsün, tek değişken instruct olsun
        if num_step != 32:
            kwargs["generation_config"] = OmniVoiceGenerationConfig(num_step=num_step)
        if seeded:
            seed = SEED_BASE + counter["i"]
            torch.manual_seed(seed)
            if DEVICE == "mps":
                torch.mps.manual_seed(seed)
        counter["i"] += 1
        return model.generate(**kwargs)[0], 24000

    return synth


def make_trnorm_synth(speed: float | None = None):
    """Metni KENDİ normalizer'ımızdan geçir, OmniVoice'un dahilisini KAPAT (çifte normalizasyon yok)."""
    sys.path.insert(0, str(ROOT))
    from trnorm import normalize_tr

    def synth(text: str):
        kwargs: dict = {
            "text": normalize_tr(text),
            "language": "Turkish",
            "normalize_text": False,
            "voice_clone_prompt": PROMPT,
        }
        if speed is not None:
            kwargs["speed"] = speed
        return model.generate(**kwargs)[0], 24000

    return synth


selected = sys.argv[1:] or ["clone", "norm", "step64", "instruct", "trnorm", "norm2"]  # trnorm-slow elle çağrılır

for name in selected:
    if name == "clone":
        bench("omnipick", make_synth(), variant="clone")
    elif name == "norm":
        bench("omnipick", make_synth(normalize_text=True), variant="norm")
    elif name == "step64":
        bench("omnipick", make_synth(num_step=64), variant="step64", only=STEP64_IDS)
    elif name == "trnorm":
        # BİZİM normalizer'ımız + OmniVoice dahili normalizasyon KAPALI
        bench("omnipick", make_trnorm_synth(), variant="trnorm", sentences_file=NORM_FILE)
    elif name == "trnorm-slow":
        # Hipotez: kelime düşmesi süre tahmini yetersizliğinden → speed=0.9 ile model
        # daha fazla frame alır, düşme azalır mı?
        bench("omnipick", make_trnorm_synth(speed=0.9), variant="trnorm-slow",
              sentences_file=NORM_FILE, only=SLOW_IDS)
    elif name == "norm2":
        # Karşılaştırma: OmniVoice'un DAHİLİ normalizasyonu, aynı cümleler
        bench("omnipick", make_synth(normalize_text=True), variant="norm2", sentences_file=NORM_FILE)
    elif name == "instruct":
        for sub, kw in [
            ("a-noinstruct", {"seeded": True}),
            ("b-highpitch", {"seeded": True, "instruct": "high pitch"}),
            ("c-designonly", {"seeded": True, "instruct": "high pitch", "use_prompt": False}),
        ]:
            bench("omnipick", make_synth(**kw), variant=f"instruct-{sub}", only=INSTRUCT_IDS)
    else:
        sys.exit(f"bilinmeyen set: {name} (geçerli: clone, norm, step64, instruct, trnorm, trnorm-slow, norm2)")
