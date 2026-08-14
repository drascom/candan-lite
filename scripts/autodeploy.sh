#!/usr/bin/env bash
# Otomatik deploy — `origin/main`'e her push, sunucuda canlıya iner.
#
# NEREDE KOŞAR: sunucu (root@192.168.0.25), `candan-autodeploy.timer` ile 60 sn'de bir.
# NE YAPAR: origin/main ilerlemişse kapıda statik kontrol koşar, temizse
#           `git reset --hard` + `systemctl restart candan-worker`, sonra sağlık bakar.
#
# NEDEN VAR: 2026-08-14'e kadar deploy scp ile dosya kopyalıyordu (bkz.
#   deploy-turn-identity.sh). Git'i atladığı için sunucu 3 HAFTA commit'i olmayan kod
#   çalıştırdı; "sunucuda ne var" sorusunun cevabı kayboldu. Artık tek doğru kaynak
#   `origin/main`: sunucu ondan başka bir şey çalıştıramaz.
#
# TASARIM KARARLARI (bilerek, tartışıldı):
#   • Tetikleyici = her push. VERSION dosyası / sürüm karşılaştırması YOK; origin/main
#     commit'i değiştiyse deploy. Bu kutu staging, tören gerekmiyor.
#   • Testler KOŞMAZ: worker/tests/ bilerek git dışında (.gitignore:52), sunucuda yok.
#     Test gerektiğinde dosyalar elle yollanır. Buraya test adımı EKLEME — olmayan
#     dizini arayıp her turda kırmızı yanar.
#   • Kapı = ruff + py_compile. ruff'ın gerekçesi ruff.toml'da: canlıda patlayan F823
#     ("logging bombası") tam olarak bu kapının yakaladığı şey.
#   • Kontrol AYRI worktree'de koşar: kırmızı kod hiçbir an /opt/candan-lite'a inmez,
#     eski sürüm kesintisiz çalışmaya devam eder.
#   • Aktif oturum varsa deploy ERTELENİR. Konuşmanın ortasında worker restart etmek
#     kullanıcıyı yarıda keser; bir tur beklemek (60 sn) bedava.
#
# Kayıt: worker/logs/deploy.jsonl (satır başına bir JSON). logs/ gitignore'da.
#
# Uçtan uca ilk doğrulama: 2026-08-14, bu yorum satırının push'u ile yapıldı.
set -euo pipefail

KOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVIS="${CANDAN_WORKER_SERVICE:-candan-worker}"
VENV="$KOK/worker/.venv/bin"
KAYIT="$KOK/worker/logs/deploy.jsonl"
CALISMA="/tmp/candan-check"
KILIT="/var/lock/candan-autodeploy.lock"
SAGLIK_SANIYE="${AUTODEPLOY_SAGLIK_SANIYE:-60}"
# Son bu kadar saniye içinde job logu varsa "oturum açık" sayılır. Yanlış pozitifin
# bedeli sadece 60 sn gecikme; yanlış negatifin bedeli kesilmiş konuşma. Cömert tut.
OTURUM_PENCERE="${AUTODEPLOY_OTURUM_PENCERE:-180}"

# ── Aynı anda iki kopya çalışmasın ────────────────────────────────────────────
# Timer 60 sn'de bir tetikliyor; yavaş bir deploy (restart + 60 sn sağlık bekleme)
# bir sonraki turu yakalar. flock olmadan iki `git reset --hard` çakışır.
if [[ -z "${AUTODEPLOY_KILITLI:-}" ]]; then
  export AUTODEPLOY_KILITLI=1
  exec flock -n "$KILIT" "$0" "$@"
fi

BASLANGIC=$SECONDS

# JSON kaydı yaz. Alanlar görev tanımından: zaman, commit, onceki_commit, ruff,
# py_compile, sonuc, sure_sn, not.
kaydet() {
  local sonuc="$1" ruff="$2" pyc="$3" commit="$4" onceki="$5" aciklama="$6"
  mkdir -p "$(dirname "$KAYIT")"
  printf '{"zaman":"%s","commit":"%s","onceki_commit":"%s","ruff":"%s","py_compile":"%s","sonuc":"%s","sure_sn":%d,"not":"%s"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$commit" "$onceki" "$ruff" "$pyc" "$sonuc" \
    "$((SECONDS - BASLANGIC))" "${aciklama//\"/\'}" >> "$KAYIT"
}

temizle() {
  # Worktree her yolda gitmeli — kalırsa bir sonraki tur "already exists" ile patlar.
  git -C "$KOK" worktree remove --force "$CALISMA" >/dev/null 2>&1 || true
  rm -rf "$CALISMA"
  git -C "$KOK" worktree prune >/dev/null 2>&1 || true
}

cd "$KOK"

# ── 1. Yeni commit var mı? ────────────────────────────────────────────────────
# Repo anonim okunabiliyor; kimlik/token gerekmiyor. Ağ yoksa sessizce çık:
# her dakika koşan bir timer'ın geçici ağ hatası için gürültü üretmesi anlamsız.
git fetch --quiet origin main || exit 0
HEDEF="$(git rev-parse origin/main)"
MEVCUT="$(git rev-parse HEAD)"
[[ "$HEDEF" == "$MEVCUT" ]] && exit 0

# ── 2. Kontrol için ayrı worktree ─────────────────────────────────────────────
# sparse-checkout cone worktree'ye de uygulanır (yalnız worker/pi/server/scripts/tools
# iner) — bu kasıtlı, kontrol edilen küme canlıda duran kümeyle aynı olsun.
temizle
if ! git worktree add --detach "$CALISMA" "$HEDEF" >/dev/null 2>&1; then
  kaydet "kirmizi" "atlandi" "atlandi" "$HEDEF" "$MEVCUT" "worktree kurulamadi"
  exit 1
fi

# ── 3. Kapıdaki statik kontroller ─────────────────────────────────────────────
RUFF_SONUC="temiz"; PYC_SONUC="temiz"; HATA=""

if ! RUFF_CIKTI="$(cd "$CALISMA" && "$VENV/ruff" check worker pi 2>&1)"; then
  RUFF_SONUC="kirmizi"
  HATA="ruff: $(printf '%s' "$RUFF_CIKTI" | tail -n 1)"
fi

# py_compile yalnız worker/ kökündeki modüller için: canlıda import edilen küme bu.
if ! PYC_CIKTI="$(cd "$CALISMA" && "$VENV/python" -m py_compile worker/*.py 2>&1)"; then
  PYC_SONUC="kirmizi"
  HATA="${HATA:+$HATA | }py_compile: $(printf '%s' "$PYC_CIKTI" | tail -n 1)"
fi

if [[ "$RUFF_SONUC" != "temiz" || "$PYC_SONUC" != "temiz" ]]; then
  # Kırmızı → hiçbir şeye dokunma. Eski sürüm çalışmaya devam eder; bir sonraki
  # push düzeltirse tur kendiliğinden yeşile döner.
  temizle
  kaydet "kirmizi" "$RUFF_SONUC" "$PYC_SONUC" "$HEDEF" "$MEVCUT" "$HATA"
  exit 1
fi

temizle

# ── 4. Aktif oturum var mı? ───────────────────────────────────────────────────
# Tespit: worker journal'ında son OTURUM_PENCERE saniyede "job_id" geçen satır.
# Boşta duran worker job logu ÜRETMEZ; iş süreci (livekit job) her satıra job_id
# ekler. pgrep'ten daha güvenilir: job alt-süreci spawn_main ile açılıyor,
# komut satırında job_id GEÇMİYOR.
if journalctl -u "$SERVIS" --since "-${OTURUM_PENCERE}s" --no-pager 2>/dev/null \
     | grep -q '"job_id"'; then
  kaydet "ertelendi" "temiz" "temiz" "$HEDEF" "$MEVCUT" "aktif oturum: son ${OTURUM_PENCERE}s icinde job logu var"
  exit 0
fi

# ── 5. Deploy ─────────────────────────────────────────────────────────────────
# .env / memory/ / worker/data/ / worker/logs/ ya gitignore'da ya takipsiz →
# reset --hard onlara DOKUNMAZ. Bu bilinçli; bozma.
ONCEKI="$MEVCUT"
ISARET="$(date '+%Y-%m-%d %H:%M:%S')"
git reset --hard --quiet "$HEDEF"
systemctl restart "$SERVIS"

# ── 6. Sağlık: worker LiveKit'e kaydolabildi mi? ──────────────────────────────
# "registered worker" = agent gerçekten çalışıyor. `is-active` yetmez: import
# hatasında systemd Restart=always ile döngüye girer ve arada "active" görünür.
SAGLIKLI=0
for ((i = 0; i < SAGLIK_SANIYE; i++)); do
  if journalctl -u "$SERVIS" --since "$ISARET" --no-pager 2>/dev/null \
       | grep -q "registered worker"; then
    SAGLIKLI=1
    break
  fi
  sleep 1
done

if (( SAGLIKLI )); then
  kaydet "basarili" "temiz" "temiz" "$HEDEF" "$ONCEKI" "kayit ${i}s icinde geldi"
  exit 0
fi

# ── 7. Geri alma ──────────────────────────────────────────────────────────────
GERI_ISARET="$(date '+%Y-%m-%d %H:%M:%S')"
git reset --hard --quiet "$ONCEKI"
systemctl restart "$SERVIS"
for ((i = 0; i < SAGLIK_SANIYE; i++)); do
  if journalctl -u "$SERVIS" --since "$GERI_ISARET" --no-pager 2>/dev/null \
       | grep -q "registered worker"; then
    kaydet "geri_alindi" "temiz" "temiz" "$HEDEF" "$ONCEKI" "yeni commit kaydolamadi, eski surume donuldu"
    exit 1
  fi
  sleep 1
done

# Geri alma da kaldıramadı → sorun deploy'da değil, ortamda (broker, ağ, LiveKit).
# Elle bakılması gerekiyor.
kaydet "kritik" "temiz" "temiz" "$HEDEF" "$ONCEKI" "geri alma sonrasi da kayit yok - ELLE BAK"
exit 2
