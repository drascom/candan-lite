#!/usr/bin/env bash
set -euo pipefail

experiment_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bin="${CRISPASR_BIN:?CRISPASR_BIN ayarlayın}"
model="${CRISPASR_MODEL:?CRISPASR_MODEL ayarlayın}"
audio="${1:?Kullanım: $0 <ses.wav>}"
ayhan_ref="${AYHAN_REFERENCE:?AYHAN_REFERENCE ayarlayın}"
havi_ref="${HAVI_REFERENCE:?HAVI_REFERENCE ayarlayın}"
speaker_threshold="${CRISPASR_SPEAKER_THRESHOLD:-0.70}"
out_dir="$experiment_dir/runs/$(date -u +%Y%m%dT%H%M%SZ)-closed-roster"
db_dir="$experiment_dir/voiceprints"
mkdir -p "$out_dir" "$db_dir"

# CrispASR'ın kendi kapalı-roster yolu: bu çağrı, ses izi saklama için açık rıza
# bayrağını kayda geçirir. Üretim profillerine veya worker/speakers.db'ye dokunmaz.
"$bin" -f "$ayhan_ref" --enroll-speaker Ayhan --speaker-db "$db_dir" --speaker-db-consent
"$bin" -f "$havi_ref" --enroll-speaker Havi --speaker-db "$db_dir" --speaker-db-consent

"$bin" \
  --backend whisper -m "$model" -l tr -f "$audio" \
  --vad --diarize-speakers --diarize-max-speakers 2 \
  --speaker-db "$db_dir" --expect-speakers "Ayhan,Havi" \
  --speaker-threshold "$speaker_threshold" --speaker-db-consent \
  -ojf -of "$out_dir/result"

echo "Sonuç: $out_dir/result.json"
