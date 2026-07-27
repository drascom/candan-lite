#!/usr/bin/env bash
# Deney kodunu sunucuya (root@192.168.0.25:/opt/higgs-exp) gönderir.
# Çıktılar (out/, refs/) gönderilmez — onlar sunucuda üretilir, fetch_outputs.sh geri çeker.
set -euo pipefail
cd "$(dirname "$0")"

HOST=${HIGGS_HOST:-root@192.168.0.25}
DEST=${HIGGS_DEST:-/opt/higgs-exp}

rsync -a --exclude out --exclude refs --exclude __pycache__ ./ "$HOST:$DEST/"
# trnorm tek kaynaktan: tts-local-bench. Sunucuda o dizin yok, yanına kopyalanır.
rsync -a ../tts-local-bench/trnorm.py "$HOST:$DEST/"

echo "OK → $HOST:$DEST"
