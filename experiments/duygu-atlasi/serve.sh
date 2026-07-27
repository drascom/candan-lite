#!/usr/bin/env bash
# Atlası aç. Sayfa out/atlas.json'u fetch ile okur; file:// üzerinden tarayıcı
# bunu ENGELLER, o yüzden küçük bir http sunucu şart.
set -euo pipefail
cd "$(dirname "$0")"

PAGE=${1:-duygu-atlasi.html}
PORT=${PORT:-8010}
URL="http://localhost:$PORT/$PAGE"
echo "→ $URL   (durdurmak için Ctrl-C)"
(sleep 1 && open "$URL" 2>/dev/null || true) &
exec python3 -m http.server "$PORT"
