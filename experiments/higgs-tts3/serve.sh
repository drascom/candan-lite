#!/usr/bin/env bash
# Dinleme sayfasını aç. compare.html out/manifest.json'u fetch ile okur;
# file:// üzerinden tarayıcı bunu ENGELLER, o yüzden küçük bir http sunucu şart.
set -euo pipefail
cd "$(dirname "$0")"

# Sayfa argümanla seçilir: `./serve.sh` → motor karşılaştırması,
# `./serve.sh tokens.html` → kontrol token'ı ölçümü (token_eval.json).
PAGE=${1:-compare.html}
PORT=${PORT:-8009}
URL="http://localhost:$PORT/$PAGE"
echo "→ $URL   (durdurmak için Ctrl-C)"
(sleep 1 && open "$URL" 2>/dev/null || true) &
exec python3 -m http.server "$PORT"
