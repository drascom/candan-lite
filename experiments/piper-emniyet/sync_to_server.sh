#!/usr/bin/env bash
# Deney kodunu sunucuya (root@192.168.0.25:/opt/piper-exp) gönderir.
# Çıktılar (out/) gönderilmez — sunucuda üretilir, fetch_outputs.sh geri çeker.
set -euo pipefail
cd "$(dirname "$0")"

HOST=${PIPER_HOST:-root@192.168.0.25}
DEST=${PIPER_DEST:-/opt/piper-exp}

rsync -a --exclude out --exclude __pycache__ ./ "$HOST:$DEST/"
# Ölçüm zemini tek kaynaktan: cümleler Higgs bench'inden, trnorm canlı worker'dan.
rsync -a ../higgs-tts3/sentences.json "$HOST:$DEST/"
rsync -a ../../worker/trnorm.py "$HOST:$DEST/"

echo "OK → $HOST:$DEST"
