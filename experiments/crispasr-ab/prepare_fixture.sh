#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
out_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/fixtures"
mkdir -p "$out_dir"

ayhan_1="${AYHAN_1:-$root_dir/worker/data/expression-samples/ayhan/20260718T193543/06-serbest.wav}"
ayhan_2="${AYHAN_2:-$root_dir/worker/data/expression-samples/ayhan/20260718T193449/01-neseli.wav}"
havi_1="${HAVI_1:-$root_dir/worker/data/expression-samples/havi/20260718T204718/06-serbest.wav}"
havi_2="${HAVI_2:-$root_dir/worker/data/expression-samples/havi/20260718T204554/01-neseli.wav}"

for input in "$ayhan_1" "$havi_1" "$ayhan_2" "$havi_2"; do
  [[ -f "$input" ]] || { echo "Eksik örnek: $input" >&2; exit 1; }
done

# Sıra: Ayhan → Havi → Ayhan → Havi. Aradaki 0.8 sn sessizlik, gerçek odadaki
# kısa konuşmacı değişimini taklit eder; çıktı yalnız test artefaktıdır. Girdi
# WAV'ları zaten 16 kHz mono s16 olduğundan FFmpeg gerektirmeden stdlib ile birleşir.
python3 "$(dirname "${BASH_SOURCE[0]}")/make_fixture.py" \
  --output "$out_dir/ayhan-havi-sequential.wav" \
  "$ayhan_1" "$havi_1" "$ayhan_2" "$havi_2"

echo "Hazır: $out_dir/ayhan-havi-sequential.wav"
