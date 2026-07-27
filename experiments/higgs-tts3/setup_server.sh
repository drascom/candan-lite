#!/usr/bin/env bash
# Higgs TTS 3 (4B) — sunucu kurulumu (root@192.168.0.25). Idempotent, tekrar koşulabilir.
#
# İZOLASYON: kendi venv'i (/opt/higgs-venv) + kendi model kökü (/opt/higgs-models).
# /opt/omnivoice-venv (torch 2.8) ve /opt/candan-lite/worker/.venv'e DOKUNMAZ.
#
# Bu betik SERVİS DURDURMAZ / BAŞLATMAZ — VRAM takası ayrı ve bilinçli bir adım,
# run_all.sh'ta.
set -euo pipefail
cd "$(dirname "$0")"

VENV=/opt/higgs-venv
MODELS=/opt/higgs-models
export HF_HOME="$MODELS/hf"
export PATH="/root/.local/bin:$PATH"

# transformers'ta `higgs_multimodal_qwen3` mimarisi YOK (5.14.1 ve main dahil).
# Bu repo aynı ağırlıkların `trust_remote_code` paketlemesi; ağırlık baytları birebir
# `bosonai/higgs-tts-3-4b` ile aynı (9309834930 B). Kodek ayrı repodan geliyor.
MODEL_REPO=multimodalart/higgs-audio-v3-tts-4b-transformers
CODEC_REPO=bosonai/higgs-audio-v2-tokenizer

mkdir -p "$MODELS"

echo "── venv ($VENV) ──"
# --system-site-packages KULLANMA. Denendi ve zincirleme kırdı: sistem scipy'si venv
# numpy 2.x ile (numpy.Inf kalkmış), ardından sistem torchvision'ı venv torch 2.13 ile
# uyuşmadı. Tam izolasyon ~4.8 GB; disk bol.
uv venv --python 3.12 --allow-existing "$VENV"
VIRTUAL_ENV="$VENV" uv pip install "transformers>=5.13,<6" "accelerate>=1.12" soundfile scipy
# torchaudio SADECE referans sesi 48→24 kHz indirmek için gerekli ama modeling kodu onu
# koşulsuz import ediyor. torch ile AYNI cu sürümünden gelmeli, yoksa libtorchaudio.so
# yüklenmiyor. --no-deps: torch'u yeniden çekmesin.
VIRTUAL_ENV="$VENV" uv pip install --no-deps --index-url https://download.pytorch.org/whl/cu130 torchaudio

echo "── ağırlıklar ($MODELS, ~9.3 GB + 0.77 GB) ──"
hf download "$MODEL_REPO"
hf download "$CODEC_REPO"

echo "── doğrulama ──"
"$VENV/bin/python" - <<'PY'
import scipy, torch, torchaudio, transformers
print("torch       ", torch.__version__, "cuda:", torch.cuda.is_available())
print("torchaudio  ", torchaudio.__version__)
print("transformers", transformers.__version__)
print("scipy       ", scipy.__version__)
assert torch.cuda.is_available(), "CUDA yok — sürücü/torch uyuşmazlığı"
PY

cat <<EOF

OK.
  venv    : $VENV
  modeller: $MODELS  (HF_HOME=$HF_HOME)

Bench:  ./run_all.sh        (omnivoice-bridge'i durdurur ve GERİ BAŞLATIR)
EOF
