#!/usr/bin/env bash
# Sunucuda üretilen wav'ları ve raporu geri çeker (ASR/kulak Mac'te yapılır).
set -euo pipefail
cd "$(dirname "$0")"

HOST=${PIPER_HOST:-root@192.168.0.25}
DEST=${PIPER_DEST:-/opt/piper-exp}

mkdir -p out
rsync -a "$HOST:$DEST/out/" out/
echo "OK ← $HOST:$DEST/out"
du -sh out
