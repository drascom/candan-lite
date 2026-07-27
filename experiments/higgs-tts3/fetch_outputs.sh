#!/usr/bin/env bash
# Sunucuda üretilen wav'ları + ölçümleri Mac'e çeker ve manifest'i tazeler.
# (Kullanıcı dinlemeyi Mac'te yapıyor.) Çıktılar .gitignore'da — commit edilmez.
set -euo pipefail
cd "$(dirname "$0")"

HOST=${HIGGS_HOST:-root@192.168.0.25}
DEST=${HIGGS_DEST:-/opt/higgs-exp}

mkdir -p out refs
rsync -a "$HOST:$DEST/out/" out/
rsync -a "$HOST:$DEST/refs/" refs/ 2>/dev/null || true

python3 merge_manifest.py
echo
echo "Dinlemek için:  ./serve.sh   → http://localhost:8009/compare.html"
