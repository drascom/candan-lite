#!/usr/bin/env bash
# Tüm bench'i baştan sona koşar: kurulum (idempotent) → sentez → timings/manifest.
#
# Kullanım:
#   ./run_all.sh              # hepsi
#   ./run_all.sh chatterbox   # tek model (omnivoice|chatterbox|f5tts|freya|orpheus)
#
# Bir model patlarsa ATLANIR, bench devam eder (nedeni README'ye düşülür).
# Kurulumlar SIRAYLA koşar: 5 paralel torch indirmesi bağlantıyı doyurup
# "address not available" ile düşürüyordu.
set -uo pipefail
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:$PATH"

MODELS=("$@")
[ ${#MODELS[@]} -eq 0 ] && MODELS=(piper freya omnivoice chatterbox orpheus)   # f5tts elendi (README)

for m in "${MODELS[@]}"; do
  echo "======== $m: kurulum ========"
  if ! "./runners/setup_${m}.sh"; then
    echo "ATLANDI ($m): kurulum başarısız" >&2
    continue
  fi
  echo "======== $m: sentez ========"
  # Runner'lar argümansız çağrıldığında kendi set listelerinin tamamını üretir
  # (ör. chatterbox 4 set, omnivoice 2 set, orpheus default+emotion).
  if ! "venvs/${m}/bin/python" "runners/run_${m}.py"; then
    echo "ATLANDI ($m): sentez başarısız" >&2
  fi
done

echo "======== timings + manifest ========"
python3 runners/merge_timings.py

cat <<'EOF'

Dinlemek için (file:// ile fetch engellenir, http gerekir):
    python3 -m http.server 8009
    → http://localhost:8009/compare.html
EOF
