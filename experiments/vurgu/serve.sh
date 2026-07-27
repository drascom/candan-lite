#!/usr/bin/env bash
# Kulak setini aç. Sayfa out/vurgu_eval.json'u fetch ile okur; file:// üzerinden
# tarayıcı bunu ENGELLER, o yüzden küçük bir http sunucu şart.
set -euo pipefail
cd "$(dirname "$0")"

PAGE=${1:-vurgu-seti.html}
PORT=${PORT:-8012}
URL="http://localhost:$PORT/$PAGE"
echo "→ $URL   (durdurmak için Ctrl-C)"
(sleep 1 && open "$URL" 2>/dev/null || true) &
exec python3 -m http.server "$PORT"
