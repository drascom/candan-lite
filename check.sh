#!/usr/bin/env bash
# Statik analiz kapısı — "logging bombası" ve akrabaları için.
#
# Kullanım:  ./check.sh
# Çıkış: 0 = temiz. Sıfırdan farklıysa aşağıdaki bulgulara bak.
#
# NE YAKALAR (ve neden):
#   worker/agent.py canlıda UnboundLocalError ile patladı — entrypoint() içindeki
#   gereksiz bir `import logging` yüzünden. Testler yakalayamazdı: entrypoint()
#   yalnız gerçek LiveKit job'ında çalışır, CLI alt-komutları oraya HİÇ girmez.
#   Statik analiz kod yolunu ÇALIŞTIRMADAN görür — bu yüzden burada.
#
#   ruff F823 = tam olarak o bomba (local referenced before assignment).
#   F811/F821/F841/F401, B (mutable default, closure tuzağı), PLW (shadowing),
#   RUF006 (kaybolan asyncio task'ı) da açık. Ayar: ruff.toml
#
# CI/pre-commit hook YOK — bilerek. Bu script ELLE koşulur.
set -uo pipefail
cd "$(dirname "$0")"

RUFF="worker/.venv/bin/ruff"
TSC="web/node_modules/.bin/tsc"
fail=0

echo "== ruff (python: worker/) =="
if [ -x "$RUFF" ]; then
  "$RUFF" check . || fail=1
else
  echo "  ATLANDI: $RUFF yok → kur: worker/.venv/bin/pip install ruff"
  fail=1
fi

echo
echo "== tsc --strict (pi/extensions/family-memory/events.ts) =="
if [ -x "$TSC" ]; then
  if "$TSC" -p pi/extensions/family-memory/tsconfig.json; then
    echo "  OK"
  else
    fail=1
  fi
else
  echo "  ATLANDI: $TSC yok → web/ içinde 'pnpm install'"
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "TEMİZ."
else
  echo "BULGU VAR (yukarı bak)."
fi
exit "$fail"
