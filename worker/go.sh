#!/usr/bin/env bash
# Candan — tek komutla sesli test.
#
#   ./go.sh              → worker'ı arka planda başlat + terminal istemcisini aç
#   ./go.sh worker       → yalnız worker (ön planda, log terminale de aksın)
#   ./go.sh client       → yalnız istemci (worker başka yerde çalışıyorsa)
#   ./go.sh --list-devices, ./go.sh client --sample-rate 16000 → argümanlar istemciye geçer
#
# Neden bu script: venv + doğru dizin + worker'ı bekleme üçlüsünü elle yapmak
# her seferinde hataya davetiye. Worker arka planda çünkü log'u zaten
# logs/agent.log'a yazıyor → terminali istemciye bırakıyoruz.
#
# .env'i BİLEREK source ETMİYORUZ: agent.py ve cli_client.py ikisi de kendileri
# load_dotenv() yapıyor. `source .env` bash'e bağımlılık getirir ve boşluklu bir
# değer (ör. PI_COLD_NOTICE_TEXT="Bir saniye, ...") script'i kırar — kırdı da.
set -euo pipefail
cd "$(dirname "$0")"

VENV=".venv/bin/python"
LOG="logs/agent.log"

[[ -x "$VENV" ]] || { echo "HATA: $VENV yok. venv kurulu mu?" >&2; exit 1; }
[[ -f ".env"  ]] || { echo "HATA: worker/.env yok." >&2; exit 1; }

MOD="${1:-all}"; [[ $# -gt 0 ]] && shift || true

worker_calisiyor() { pgrep -f "python agent.py dev" >/dev/null 2>&1; }

baslat_worker_arkada() {
  if worker_calisiyor; then
    echo "• worker zaten çalışıyor (yeniden başlatılmadı)"
    return
  fi
  echo "• worker başlatılıyor (log: $LOG)"
  "$VENV" agent.py dev >/dev/null 2>&1 &
  WORKER_PID=$!
  # "registered worker" satırını bekle — erken bağlanan istemci job alamaz.
  for _ in $(seq 1 40); do
    kill -0 "$WORKER_PID" 2>/dev/null || { echo "HATA: worker öldü. $LOG'a bak." >&2; exit 1; }
    grep -q "registered worker" "$LOG" 2>/dev/null && { echo "• worker hazır"; return; }
    sleep 0.5
  done
  echo "UYARI: worker 20 sn'de kaydolmadı, yine de devam ediliyor ($LOG'a bak)" >&2
}

case "$MOD" in
  worker)
    exec "$VENV" agent.py dev "$@"
    ;;
  client|--*)
    # '--' ile başlıyorsa mod değil argümandır → istemciye geri ver.
    [[ "$MOD" == --* ]] && set -- "$MOD" "$@"
    exec "$VENV" cli_client.py "$@"
    ;;
  all)
    baslat_worker_arkada
    # Script'ten çıkarken BİZİM başlattığımız worker'ı da indir (zaten çalışanı değil).
    trap '[[ -n "${WORKER_PID:-}" ]] && kill "$WORKER_PID" 2>/dev/null || true' EXIT
    echo "• istemci açılıyor — çıkmak için Ctrl+C"
    echo
    # --me geçmiyoruz: istemci kendi .env'inden MATE_CLI_NAME'i okuyor.
    "$VENV" cli_client.py "$@"
    ;;
  *)
    echo "Bilinmeyen mod: $MOD (worker | client | all)" >&2; exit 1
    ;;
esac
