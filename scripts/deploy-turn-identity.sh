#!/usr/bin/env bash
# Turn-safe speaker identity deploy. MOSS/Whisper seçimine dokunmaz.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${CANDAN_DEPLOY_HOST:-root@192.168.0.25}"
REMOTE_ROOT="${CANDAN_REMOTE_ROOT:-/opt/candan-lite}"
WORKER_SERVICE="${CANDAN_WORKER_SERVICE:-candan-worker.service}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="$REMOTE_ROOT/.deploy-backups/turn-identity-$STAMP"
REMOTE_TMP="/tmp/candan-turn-identity-$STAMP"

remote() {
  ssh -o BatchMode=yes -o ConnectTimeout=8 "$TARGET" "$@"
}

echo "• Yerel turn-safe regresyon testleri"
"$ROOT/worker/.venv/bin/python" -m unittest discover -s "$ROOT/worker/tests" -v
"$ROOT/worker/.venv/bin/python" -m py_compile \
  "$ROOT/worker/agent.py" \
  "$ROOT/worker/speaker_tap.py" \
  "$ROOT/worker/pi_brain.py" \
  "$ROOT/worker/name_parser.py" \
  "$ROOT/worker/cli_client.py"

echo "• Sunucu erişimi ve worker kontrolü: $TARGET"
remote test -x "$REMOTE_ROOT/worker/.venv/bin/python"
remote test -f "$REMOTE_ROOT/worker/.env"

BACKUP_READY=0
WORKER_TOUCHED=0

rollback() {
  local code=$?
  trap - ERR
  if (( BACKUP_READY && WORKER_TOUCHED )); then
    echo "HATA: turn-safe deploy tamamlanamadı; eski worker geri yükleniyor." >&2
    remote bash -s -- "$REMOTE_ROOT" "$BACKUP" "$WORKER_SERVICE" <<'REMOTE' || true
set -euo pipefail
root=$1
backup=$2
service=$3
systemctl stop "$service" || true
for file in agent.py speaker_tap.py pi_brain.py name_parser.py cli_client.py; do
  install -m 0644 "$backup/$file" "$root/worker/$file"
done
install -m 0600 "$backup/.env" "$root/worker/.env"
systemctl start "$service"
REMOTE
  fi
  exit "$code"
}
trap rollback ERR

echo "• Canlı dosyalar yedekleniyor: $BACKUP"
remote bash -s -- "$REMOTE_ROOT" "$BACKUP" <<'REMOTE'
set -euo pipefail
root=$1
backup=$2
install -d -m 0700 "$backup"
for file in agent.py speaker_tap.py pi_brain.py name_parser.py cli_client.py; do
  cp -a "$root/worker/$file" "$backup/$file"
done
cp -a "$root/worker/.env" "$backup/.env"
REMOTE
BACKUP_READY=1

echo "• Yeni dosyalar geçici alana yükleniyor"
remote install -d -m 0755 "$REMOTE_TMP/tests"
for file in agent.py speaker_tap.py pi_brain.py name_parser.py cli_client.py; do
  scp -q "$ROOT/worker/$file" "$TARGET:$REMOTE_TMP/$file"
done
scp -q "$ROOT/worker/tests/test_speaker_turn.py" "$TARGET:$REMOTE_TMP/tests/test_speaker_turn.py"
scp -q "$ROOT/worker/tests/test_identity_truth.py" "$TARGET:$REMOTE_TMP/tests/test_identity_truth.py"
scp -q "$ROOT/worker/tests/test_enrollment_trigger.py" "$TARGET:$REMOTE_TMP/tests/test_enrollment_trigger.py"
scp -q "$ROOT/worker/tests/test_cli_audio_filter.py" "$TARGET:$REMOTE_TMP/tests/test_cli_audio_filter.py"
remote "$REMOTE_ROOT/worker/.venv/bin/python" -m py_compile \
  "$REMOTE_TMP/agent.py" \
  "$REMOTE_TMP/speaker_tap.py" \
  "$REMOTE_TMP/pi_brain.py" \
  "$REMOTE_TMP/name_parser.py" \
  "$REMOTE_TMP/cli_client.py"

echo "• Worker durduruluyor; turn-safe dosyalar atomik kuruluyor"
WORKER_TOUCHED=1
remote systemctl stop "$WORKER_SERVICE"
for file in agent.py speaker_tap.py pi_brain.py name_parser.py cli_client.py; do
  remote install -m 0644 "$REMOTE_TMP/$file" "$REMOTE_ROOT/worker/$file"
done

echo "• Sunucuda regresyon testleri"
remote bash -s -- "$REMOTE_ROOT" "$REMOTE_TMP" <<'REMOTE'
set -euo pipefail
root=$1
tmp=$2
cd "$root/worker"
PYTHONPATH="$root/worker" .venv/bin/python -m unittest discover -s "$tmp/tests" -v
REMOTE

remote systemctl start "$WORKER_SERVICE"
for _ in $(seq 1 20); do
  [[ "$(remote systemctl is-active "$WORKER_SERVICE" 2>/dev/null || true)" == active ]] && break
  sleep 1
done
[[ "$(remote systemctl is-active "$WORKER_SERVICE" 2>/dev/null || true)" == active ]]

# Import ve varsayılan politika: turn-safe üç güncel pencere, MOSS seçimi korunmuş.
remote bash -s -- "$REMOTE_ROOT" <<'REMOTE'
set -euo pipefail
root=$1
cd "$root/worker"
.venv/bin/python -c 'import agent; assert agent.SPEAKER_TURN_CONFIRM_HITS >= 3; print("stt=whisper-wyoming", "turn_hits=" + str(agent.SPEAKER_TURN_CONFIRM_HITS))'
REMOTE

WORKER_TOUCHED=0
trap - ERR
remote rm -rf "$REMOTE_TMP"
echo "• Turn-safe kimlik deploy tamamlandı. Yedek: $BACKUP"
remote systemctl is-active "$WORKER_SERVICE"
