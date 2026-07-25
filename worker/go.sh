#!/usr/bin/env bash
# Candan — tek komutla sesli test. LOKAL WORKER YOK: bu Mac yalnız KAYNAK/DEPLOY kaynağı.
#
#   ./go.sh              → terminal istemcisini aç (iş SUNUCUDAKİ worker'a gider)
#   ./go.sh client       → aynısı (açık ad)
#   ./go.sh worker       → ÇALIŞMAZ, hata basar (aşağıya bak)
#   ./go.sh --list-devices, ./go.sh client --sample-rate 16000 → argümanlar istemciye geçer
#
# NEDEN lokal worker devre dışı:
#   • Bu Mac'te pi CLI kurulu DEĞİL → beyin her turda FileNotFoundError ile çöküyor,
#     oturum sessiz kalıyor. (25 Tem'deki sessiz oturumun sebebi buydu; yarış ya da
#     agent adı uyuşmazlığı DEĞİLDİ.)
#   • Çalışma yeri sunucu: root@192.168.0.25:/opt/candan-lite, `candan-worker.service`.
#   Deploy: handoff/2026-07-25-deploy-tts-kisa-metin-ve-cache.md
#
# Bilerek lokal worker isteyen (pi CLI'ı kurduysa) tek kaçış:
#   GO_SH_LOKAL_WORKER=1 ./go.sh          → worker'ı arka planda başlat + istemciyi aç
#   GO_SH_LOKAL_WORKER=1 ./go.sh worker   → yalnız worker, ön planda
# Bu yolda kullanılan hazır-olma mantığı (baslat_worker_arkada / kayit_durumu /
# beklenen_agent_adi) BİLEREK duruyor — silinmedi.
#
# .env'i BİLEREK source ETMİYORUZ: agent.py ve cli_client.py ikisi de kendileri
# load_dotenv() yapıyor. `source .env` bash'e bağımlılık getirir ve boşluklu bir
# değer (ör. PI_COLD_NOTICE_TEXT="Bir saniye, ...") script'i kırar — kırdı da.
set -euo pipefail
cd "$(dirname "$0")"

VENV=".venv/bin/python"
LOG="logs/agent.log"
ERRLOG="logs/worker-stderr.log"
BEKLE_SANIYE="${GO_BEKLE_SANIYE:-20}"
DEPLOY_NOTU="handoff/2026-07-25-deploy-tts-kisa-metin-ve-cache.md"
# Lokal worker yalnız bu değişken set edilmişse çalışır (yukarıdaki gerekçe).
LOKAL_WORKER="${GO_SH_LOKAL_WORKER:-}"

# Testler bu script'i source edip fonksiyonları tek tek çağırabilsin (bkz.
# tests/test_go_readiness.sh). Sadece tanımlar yüklenir, aşağıdaki case ÇALIŞMAZ.
if [[ -z "${GO_SH_SADECE_TANIM:-}" ]]; then
  [[ -x "$VENV" ]] || { echo "HATA: $VENV yok. venv kurulu mu?" >&2; exit 1; }
  [[ -f ".env"  ]] || { echo "HATA: worker/.env yok." >&2; exit 1; }
fi

MOD="${1:-all}"; [[ $# -gt 0 ]] && shift || true

worker_calisiyor() { pgrep -f "python agent.py dev" >/dev/null 2>&1; }

# .env'den TEK anahtar oku — source ETMEDEN (başlıktaki gerekçe: boşluklu değerler
# script'i kırıyor). Yalnız `ANAHTAR=değer` biçimini alır, tırnakları soyar.
env_dosyasindan() {
  [[ -f ".env" ]] || return 0
  sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" .env \
    | tail -n 1 | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}

# agent.py'nin kaydolacağı adı BİREBİR aynı sırayla türet. agent.py:90
#   os.environ["LIVEKIT_AGENT_NAME"] or os.environ["AGENT_NAME"] or "candan"
# ve load_dotenv() var olan ortam değişkenini EZMEZ → kabuk ortamı .env'i yener.
beklenen_agent_adi() {
  local ad
  for ad in "${LIVEKIT_AGENT_NAME:-}" "$(env_dosyasindan LIVEKIT_AGENT_NAME)" \
            "${AGENT_NAME:-}" "$(env_dosyasindan AGENT_NAME)"; do
    [[ -n "$ad" ]] && { printf '%s\n' "$ad"; return 0; }
  done
  printf 'candan\n'
}

# $LOG'un BU çalıştırmada eklenen kısmında kayıt durumu. Bayt offset'i kullanıyoruz:
# satır sayısı, son satırda newline yoksa eksik sayıp eski bir satırı "yeni" gösterir —
# hatanın tam kaynağı buydu. Log kısalmışsa (rotasyon/truncate) baştan bakar.
#   "ok"           → doğru adla kaydoldu
#   "yanlis:<ad>"  → kaydoldu ama BAŞKA adla → dispatch eşleşmez, iş kimseye gitmez
#   "yok"          → bu çalıştırmada henüz kayıt satırı yok
kayit_durumu() {
  local offset="$1" beklenen="$2" boyut yeni gorulen
  [[ -f "$LOG" ]] || { printf 'yok\n'; return 0; }
  boyut=$(wc -c < "$LOG" | tr -d ' ')
  (( boyut < offset )) && offset=0
  yeni=$(tail -c "+$((offset + 1))" "$LOG" 2>/dev/null | grep -F "registered worker" || true)
  [[ -z "$yeni" ]] && { printf 'yok\n'; return 0; }
  # -F: ad regex olarak yorumlanmasın.
  if printf '%s\n' "$yeni" | grep -qF "\"agent_name\": \"$beklenen\""; then
    printf 'ok\n'
    return 0
  fi
  gorulen=$(printf '%s\n' "$yeni" | sed -n 's/.*"agent_name": "\([^"]*\)".*/\1/p' | tail -n 1)
  printf 'yanlis:%s\n' "${gorulen:-?}"
}

baslat_worker_arkada() {
  if worker_calisiyor; then
    echo "• worker zaten çalışıyor (yeniden başlatılmadı)"
    return
  fi

  local beklenen offset durum
  beklenen="$(beklenen_agent_adi)"
  mkdir -p logs
  # Başlatmadan ÖNCEKİ boyutu al → hazır-olma kontrolü yalnız bundan sonrasına bakar.
  offset=0
  [[ -f "$LOG" ]] && offset=$(wc -c < "$LOG" | tr -d ' ')

  # stdout/stderr'i ATMIYORUZ: logger'a hiç uğramayan traceback'ler (import hatası,
  # segfault mesajı) yalnız burada görünür. Dosya şişmesin diye son 2000 satır tutulur.
  if [[ -f "$ERRLOG" ]] && [[ $(wc -l < "$ERRLOG" | tr -d ' ') -gt 2000 ]]; then
    tail -n 2000 "$ERRLOG" > "$ERRLOG.tmp" && mv "$ERRLOG.tmp" "$ERRLOG"
  fi
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') worker başlıyor (agent_name beklenen: $beklenen) ===" >> "$ERRLOG"

  echo "• worker başlatılıyor (log: $LOG, stderr: $ERRLOG)"
  "$VENV" agent.py dev >> "$ERRLOG" 2>&1 &
  WORKER_PID=$!

  # "registered worker" satırını DOĞRU adla bekle — erken bağlanan istemci job alamaz.
  local tur=$(( BEKLE_SANIYE * 2 ))
  for _ in $(seq 1 "$tur"); do
    kill -0 "$WORKER_PID" 2>/dev/null || {
      echo "HATA: worker öldü. Son satırlar için: tail -n 40 $ERRLOG" >&2
      exit 1
    }
    durum="$(kayit_durumu "$offset" "$beklenen")"
    case "$durum" in
      ok) echo "• worker hazır (agent_name: $beklenen)"; return 0 ;;
      yanlis:*)
        # Ad uyuşmazlığı olursa dispatch eşleşmez. NOT: 25 Tem'deki sessiz oturumun
        # sebebi BU DEĞİLDİ (worker ve dispatch ikisi de "candan-dev" idi, doğrulandı);
        # sebep pi CLI'ın kurulu olmamasıydı. Bu kontrol yine de ucuz bir emniyet.
        echo "HATA: worker YANLIŞ adla kaydoldu: '${durum#yanlis:}' (beklenen '$beklenen')." >&2
        echo "      İstemci '$beklenen' için dispatch açar → iş kimseye gitmez, oturum sessiz kalır." >&2
        echo "      LIVEKIT_AGENT_NAME / AGENT_NAME / LIVEKIT_AGENT_NAME_OVERRIDE kabuk ortamını kontrol et." >&2
        kill "$WORKER_PID" 2>/dev/null || true
        exit 1
        ;;
    esac
    sleep 0.5
  done

  # ARTIK "yine de devam" YOK: hazır olmayan worker'la istemci açmak sessiz oturum demek.
  echo "HATA: worker ${BEKLE_SANIYE} sn'de '$beklenen' adıyla kaydolmadı." >&2
  echo "      Bak: tail -n 40 $LOG  ·  tail -n 40 $ERRLOG" >&2
  kill "$WORKER_PID" 2>/dev/null || true
  exit 1
}

# Lokal worker kapalıyken `worker` modunun bastığı açıklama. Sessizce istemciye
# düşmüyoruz: kullanıcı worker istediyse nerede çalıştığını ÖĞRENMELİ.
lokal_worker_kapali_hatasi() {
  cat >&2 <<EOF
HATA: lokal worker devre dışı — bu Mac'te worker ÇALIŞTIRILMIYOR.

  Sebep 1: pi CLI bu makinede kurulu değil → beyin her turda FileNotFoundError ile
           çöküyor, oturum sessiz kalıyor.
  Sebep 2: çalışma yeri sunucu → root@192.168.0.25:/opt/candan-lite (candan-worker.service)

  Yeni kodu çalıştırmak istiyorsan deploy et:  $DEPLOY_NOTU
  Sesli test için worker'a gerek yok:          ./go.sh          (istemci; iş sunucuya gider)
  Sunucudaki worker'ın durumu:                 systemctl status candan-worker  (sunucuda)

  Yine de lokal worker istiyorsan (pi CLI kurulu olmalı):
      GO_SH_LOKAL_WORKER=1 ./go.sh worker
EOF
  exit 1
}

# Test modunda buradan aşağısı çalışmaz.
[[ -n "${GO_SH_SADECE_TANIM:-}" ]] && return 0

case "$MOD" in
  worker)
    [[ -n "$LOKAL_WORKER" ]] || lokal_worker_kapali_hatasi
    exec "$VENV" agent.py dev "$@"
    ;;
  client|--*)
    # '--' ile başlıyorsa mod değil argümandır → istemciye geri ver.
    [[ "$MOD" == --* ]] && set -- "$MOD" "$@"
    exec "$VENV" cli_client.py "$@"
    ;;
  all)
    # VARSAYILAN: yalnız istemci. Worker sunucuda çalışıyor (başlıktaki gerekçe).
    if [[ -z "$LOKAL_WORKER" ]]; then
      echo "• istemci açılıyor (worker: sunucuda — 192.168.0.25/candan-worker.service)"
      echo
      # --me geçmiyoruz: istemci kendi .env'inden MATE_CLI_NAME'i okuyor.
      exec "$VENV" cli_client.py "$@"
    fi
    # Override yolu: eski davranış (worker arka planda + istemci).
    echo "• GO_SH_LOKAL_WORKER=1 → lokal worker başlatılıyor (pi CLI kurulu olmalı)"
    baslat_worker_arkada
    # Script'ten çıkarken BİZİM başlattığımız worker'ı da indir (zaten çalışanı değil).
    trap '[[ -n "${WORKER_PID:-}" ]] && kill "$WORKER_PID" 2>/dev/null || true' EXIT
    echo "• istemci açılıyor — çıkmak için Ctrl+C"
    echo
    "$VENV" cli_client.py "$@"
    ;;
  *)
    echo "Bilinmeyen mod: $MOD (client | worker | all)" >&2; exit 1
    ;;
esac
