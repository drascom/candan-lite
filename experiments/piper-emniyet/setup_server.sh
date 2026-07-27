#!/usr/bin/env bash
# Piper (emniyet ağı adayı) — sunucu kurulumu (root@192.168.0.25). Idempotent.
#
# İZOLASYON: kendi venv'i (/opt/piper-venv) + kendi model kökü (/opt/piper/voices).
# Sistem paketlerine, /opt/higgs-venv'e, worker/.venv'e DOKUNMAZ.
# CPU/ONNX — GPU'ya hiç bakmaz, VRAM harcamaz (Higgs'in yanında yaşayabilmesi şart).
#
# systemd servisi KURMAZ. Bu tur boot'a girmiyor, canlıya bağlanmıyor.
set -euo pipefail

VENV=/opt/piper-venv
ROOT=/opt/piper
VOICES=$ROOT/voices
export PATH="/root/.local/bin:$PATH"
export HF_HOME="$ROOT/hf"

mkdir -p "$VOICES"

echo "── venv ($VENV) ──"
uv venv --python 3.12 --allow-existing "$VENV"
# piper-tts, onnxruntime'ı (CPU) kendi çeker. huggingface_hub indirme için.
VIRTUAL_ENV="$VENV" uv pip install piper-tts huggingface_hub numpy

echo "── Türkçe sesler ($VOICES) ──"
# HF'de bulunan TÜM Türkçe piper sesleri (28 Tem 2026 taraması):
#   dfki      rhasspy/piper-voices tr/tr_TR/dfki/medium      — resmi katalogdaki tek TR ses
#   fahrettin speaches-ai/piper-tr_TR-fahrettin-medium       — topluluk
#   fettah    speaches-ai/piper-tr_TR-fettah-medium          — topluluk
#   eren      99eren99/piper-turkish-tts                     — topluluk (tts-local-bench'te kullanılan)
# Hepsi 63 MB VITS/medium. Dördü de indirilir, ölçümde kıyaslanır.
dl() {  # <ad> <repo> <onnx-yolu> <json-yolu>
  local ad=$1 repo=$2 onnx=$3 cfg=$4
  mkdir -p "$VOICES/$ad"
  [ -s "$VOICES/$ad/model.onnx" ] || \
    "$VENV/bin/hf" download "$repo" "$onnx" --local-dir "$VOICES/$ad/.dl" >/dev/null
  [ -s "$VOICES/$ad/model.onnx" ] || cp "$VOICES/$ad/.dl/$onnx" "$VOICES/$ad/model.onnx"
  [ -s "$VOICES/$ad/model.onnx.json" ] || \
    "$VENV/bin/hf" download "$repo" "$cfg" --local-dir "$VOICES/$ad/.dl" >/dev/null
  [ -s "$VOICES/$ad/model.onnx.json" ] || cp "$VOICES/$ad/.dl/$cfg" "$VOICES/$ad/model.onnx.json"
  rm -rf "$VOICES/$ad/.dl"
  echo "  $ad: $(du -h "$VOICES/$ad/model.onnx" | cut -f1)"
}
dl dfki      rhasspy/piper-voices tr/tr_TR/dfki/medium/tr_TR-dfki-medium.onnx tr/tr_TR/dfki/medium/tr_TR-dfki-medium.onnx.json
dl fahrettin speaches-ai/piper-tr_TR-fahrettin-medium model.onnx config.json
dl fettah    speaches-ai/piper-tr_TR-fettah-medium    model.onnx config.json
dl eren      99eren99/piper-turkish-tts               model.onnx config.json

echo "── doğrulama ──"
"$VENV/bin/python" - <<'PY'
import onnxruntime, numpy
from piper import PiperVoice
print("onnxruntime", onnxruntime.__version__, onnxruntime.get_available_providers())
v = PiperVoice.load("/opt/piper/voices/dfki/model.onnx",
                    config_path="/opt/piper/voices/dfki/model.onnx.json", use_cuda=False)
n = sum(len(c.audio_int16_bytes) for c in v.synthesize("Merhaba, bu bir testtir."))
print("dfki sentez baytı:", n)
assert n > 0
PY

echo
echo "OK.  venv=$VENV  sesler=$VOICES"
du -sh "$VENV" "$ROOT"
