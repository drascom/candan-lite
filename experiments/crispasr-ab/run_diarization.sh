#!/usr/bin/env bash
set -euo pipefail

experiment_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bin="${CRISPASR_BIN:?CRISPASR_BIN ayarlayın}"
model="${CRISPASR_MODEL:?CRISPASR_MODEL ayarlayın (önce auto ile dry-run yapın)}"
audio="${1:?Kullanım: $0 <ses.wav>}"
out_dir="$experiment_dir/runs/$(date -u +%Y%m%dT%H%M%SZ)-diarization"
mkdir -p "$out_dir"

"$bin" \
  --backend whisper -m "$model" -l tr -f "$audio" \
  --vad \
  --diarize --diarize-method pyannote --sherpa-segment-model auto \
  --diarize-embedder auto --diarize-max-speakers 2 \
  -ojf -of "$out_dir/result"

echo "Sonuç: $out_dir/result.json"
