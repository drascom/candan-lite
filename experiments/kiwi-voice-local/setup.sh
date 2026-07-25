#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
python_bin="${PYTHON_BIN:-python3.11}"

"$python_bin" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

upstream_dir=".upstream/kiwi-voice"
upstream_commit="1da13fdaccb99ad32b72fc0d6cb5619953a5b468"
if [[ ! -d "$upstream_dir/.git" ]]; then
  mkdir -p .upstream
  git clone --filter=blob:none https://github.com/ekleziast/kiwi-voice.git "$upstream_dir"
fi
git -C "$upstream_dir" fetch --depth 1 origin "$upstream_commit"
git -C "$upstream_dir" checkout --detach "$upstream_commit"

# Kiwi'nin eski gated pyannote/embedding modeli yerine aynı pyannote API'siyle
# çalışan açık WeSpeaker checkpoint'ini yerel uyumluluk modeli olarak hazırla.
model_dir="$upstream_dir/models/pyannote-embedding"
if [[ ! -f "$model_dir/pytorch_model.bin" ]]; then
  .venv/bin/hf download pyannote/wespeaker-voxceleb-resnet34-LM \
    --local-dir "$model_dir"
fi

# Hugging Face hesabında gated model erişimi verilmişse Kiwi'nin birebir upstream
# encoder'ını da ayrı dizine indir. Erişim yoksa kurulum açık WeSpeaker ile sürer.
upstream_model_dir="$upstream_dir/models/pyannote-embedding-upstream"
if [[ ! -f "$upstream_model_dir/pytorch_model.bin" ]]; then
  if ! .venv/bin/hf download pyannote/embedding --local-dir "$upstream_model_dir"; then
    echo "UYARI: pyannote/embedding erişimi yok; açık WeSpeaker uyumluluk modeli kullanılacak."
  fi
fi

echo "Hazır. Kontrol için: .venv/bin/python mic_test.py doctor"
