"""Model ağırlıklarını ÖNDEN indirir (inference YOK).

Bench koşuları sırayla çalışıyor (eşzamanlı MPS işi wall-clock ölçümünü bozar);
indirmeler ise GPU kullanmadığı için beklerken yapılabilir. Bu script onu yapar.

Kullanım: <venv>/bin/python runners/prefetch_weights.py <hedef>
  hedef: f5tts | freya | orpheus
"""
import sys

from huggingface_hub import hf_hub_download, snapshot_download

target = sys.argv[1]

if target == "f5tts":
    hf_hub_download("marduk-ra/F5-TTS-Turkish", "f5_tts_turkish_1000000.safetensors")
    hf_hub_download("marduk-ra/F5-TTS-Turkish", "vocab.txt")
    snapshot_download("charactr/vocos-mel-24khz")  # F5-TTS varsayılan vocoder
elif target == "freya":
    snapshot_download("freyavoice/freya-tts")
    snapshot_download("openbmb/VoxCPM2", allow_patterns=["*audiovae*", "*.json", "*vae*"])
elif target == "orpheus":
    # allow_patterns ŞART: repo eğitim artıklarını da taşıyor (optimizer.pt ~12 GB,
    # rng_state/scheduler/trainer_state). Filtresiz snapshot_download 17 GB indiriyor,
    # inference için gereken ~6.5 GB.
    snapshot_download(
        "Karayakar/Orpheus-TTS-Turkish-PT-5000",
        allow_patterns=["*.safetensors", "*.json", "tokenizer*", "special_tokens_map.json"],
    )
    snapshot_download("hubertsiuzdak/snac_24khz")
else:
    sys.exit(f"bilinmeyen hedef: {target}")

print(f"OK: {target} ağırlıkları indirildi")
