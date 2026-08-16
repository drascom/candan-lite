#!/usr/bin/env bash
# Otomatik deploy — `origin/main`'e her push, sunucuda canlıya iner.
#
# NEREDE KOŞAR: sunucu (root@192.168.0.25), `candan-autodeploy.timer` ile 60 sn'de bir.
# NE YAPAR: origin/main ilerlemişse önce ALTYAPI ÖN KONTROLÜ, sonra kapıda statik
#           kontrol, temizse `git reset --hard` + `systemctl restart candan-worker`,
#           sonra sağlık bakar. Tutmazsa geri alır, üst üste 3 kez tutmazsa kendini kapatır.
#
# NEDEN VAR: 2026-08-14'e kadar deploy scp ile dosya kopyalıyordu (bkz.
#   deploy-turn-identity.sh). Git'i atladığı için sunucu 3 HAFTA commit'i olmayan kod
#   çalıştırdı; "sunucuda ne var" sorusunun cevabı kayboldu. Artık tek doğru kaynak
#   `origin/main`: sunucu ondan başka bir şey çalıştıramaz.
#
# ── 2026-08-14 KAZASI (bu script 11 dakika canlıyı düşürdü) ───────────────────
# Zincir: repo temizliğinde `handoff/` sunucudan silindi → `candan-worker.service`
# içinde `ReadWritePaths=/opt/candan-lite/handoff` var ve `ProtectSystem=strict` ile
# o dizinin VAR OLMASI zorunlu → çalışan süreç namespace'ini çoktan kurmuştu, silme
# anında hiçbir şey olmadı; bomba İLK RESTART'ta patladı (`status=226/NAMESPACE`).
# O restart'ı bu script'in uçtan uca testi tetikledi. Sağlık kontrolü doğru çalıştı
# (kayıt yok → geri aldı) ama geri alma da AYNI sebepten tutmadı → `kritik`.
# Ve timer 2 dakikada bir aynı şeyi baştan denedi. Worker 130 kez açılmayı denedi.
#
# Ders: sağlık/geri alma mantığı doğruydu; eksik olan iki şey vardı, ikisi de artık
# burada:
#   1. ÖN KONTROL (onkontrol) — restart'a gitmeden önce systemd'nin ihtiyaç duyduğu
#      yollar dosya sisteminde gerçekten var mı diye bak. Bu bir GİT sorusu değil,
#      DOSYA SİSTEMİ sorusu: yeni commit'in ağacına değil, sunucunun o anki haline
#      bakılır. Yol eksikse deploy hiç başlamaz.
#   2. DEVRE KESİCİ — art arda 3 başarısız tur olursa timer'ı kapat. Kırılmış bir
#      ortamda sonsuz tekrar, hasarı onarmaz; büyütür.
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
#   • Kırmızı bir commit TEKRAR DENENMEZ. Kapıdan geçemeyen kod 60 sn'de bir yeniden
#     denense sonuç değişmez, sadece kayıt dosyası şişer. Commit değişince tekrar bakar.
#
# KULLANIM:
#   scripts/autodeploy.sh              → normal tur (sunucuda timer bunu çağırır)
#   scripts/autodeploy.sh --dry-run    → fetch + ön kontrol + ruff + py_compile koşar,
#                                        reset/restart YAPMAZ, kayıt dosyasına YAZMAZ.
#                                        Ön kontrolü ve kapıyı canlıyı kırmadan sınamak için.
#
# TEST KANCASI: AUTODEPLOY_TEST_EXTRA_PATH=/olmayan/yol ile ön kontrol listesine
#   sahte bir yol eklenir. Ön kontrolün gerçekten durdurduğunu, gerçek dizinleri
#   SİLMEDEN göstermek için var. Sadece sınama amaçlı; timer bunu asla set etmez.
#   AUTODEPLOY_UNIT_HEDEF=/tmp/sahte-etc ile unit senkronu gerçek /etc/systemd/system
#   yerine sahte bir dizine bakar — senkronu canlıyı kırmadan sınamak için.
#
# ── UNIT SENKRONU (2026-08-16) ────────────────────────────────────────────────
# Bu script kodu deploy ediyordu ama systemd unit dosyalarını TAŞIMIYORDU. Repo'daki
# worker/systemd/ ile sunucudaki /etc/systemd/system/ sessizce ayrışıyordu: units
# elle scp'leniyor, kimse unutunca "sunucuda hangi unit koşuyor" sorusu yine
# cevapsız kalıyordu — git'i tek doğru kaynak yapmanın yarım kalmış tarafı.
# Artık `git reset --hard`tan SONRA worker/systemd/ altındaki *.service / *.timer ve
# *.service.d/*.conf hedefle içerik olarak karşılaştırılır, farklı olan kopyalanır,
# en az bir dosya değiştiyse `daemon-reload` yapılır — worker restart'ından ÖNCE,
# ki yeni tanımla açılsın. Geri alma yolunda da aynısı koşar: kod eski sürüme
# dönerken unit yeni kalırsa ikisi uyumsuz olur.
#
# DİKKAT — kendi unit'ini güncelleme tuzağı: candan-autodeploy.service/.timer
# değişirse daemon-reload yapılır ama timer RESTART EDİLMEZ; o an koşan tur (yani
# bu süreç) kendini öldürür. Yeni tanım bir sonraki turda zaten geçerli olur.
# candan-worker.service değişirse restart adımı zaten var, ek bir şey gerekmez.
#
# Kayıt: worker/logs/deploy.jsonl (satır başına bir JSON). logs/ gitignore'da.
# Durum: worker/logs/.autodeploy-state.json (ardışık hata sayacı + son kırmızı commit).
#
# Uçtan uca ilk doğrulama: 2026-08-14, bu yorum satırının push'u ile yapıldı.
set -euo pipefail

KOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVIS="${CANDAN_WORKER_SERVICE:-candan-worker}"
TIMER="${CANDAN_AUTODEPLOY_TIMER:-candan-autodeploy.timer}"
VENV="$KOK/worker/.venv/bin"
KAYIT="$KOK/worker/logs/deploy.jsonl"
DURUM="$KOK/worker/logs/.autodeploy-state.json"
CALISMA="/tmp/candan-check"
KILIT="/var/lock/candan-autodeploy.lock"
SAGLIK_SANIYE="${AUTODEPLOY_SAGLIK_SANIYE:-60}"
# Art arda kaç başarısız tur (kritik / geri_alindi) sonra timer kendini kapatsın.
HATA_SINIRI="${AUTODEPLOY_HATA_SINIRI:-3}"
# Son bu kadar saniye içinde job logu varsa "oturum açık" sayılır. Yanlış pozitifin
# bedeli sadece 60 sn gecikme; yanlış negatifin bedeli kesilmiş konuşma. Cömert tut.
OTURUM_PENCERE="${AUTODEPLOY_OTURUM_PENCERE:-180}"
# Unit senkronu: kaynak repo'daki dizin, hedef systemd'nin okuduğu dizin.
UNIT_KAYNAK="$KOK/worker/systemd"
UNIT_HEDEF="${AUTODEPLOY_UNIT_HEDEF:-/etc/systemd/system}"
# Kayda geçecek liste; unit_senkron dolduruyor. kaydet() her satıra bunu yazar.
UNIT_DEGISEN="yok"

KURU=0
for arg in "$@"; do
  case "$arg" in
    --dry-run|-n) KURU=1 ;;
    *) echo "bilinmeyen argüman: $arg" >&2; exit 64 ;;
  esac
done

# ── Aynı anda iki kopya çalışmasın ────────────────────────────────────────────
# Timer 60 sn'de bir tetikliyor; yavaş bir deploy (restart + 60 sn sağlık bekleme)
# bir sonraki turu yakalar. flock olmadan iki `git reset --hard` çakışır.
if [[ -z "${AUTODEPLOY_KILITLI:-}" ]]; then
  export AUTODEPLOY_KILITLI=1
  exec flock -n "$KILIT" "$0" "$@"
fi

BASLANGIC=$SECONDS

# Kuru turda ne olup bittiğini anlat; normal turda tamamen sessiz.
# NOT: `(( KURU )) && echo` YAZMA — KURU=0 iken fonksiyon 1 döner ve `set -e`
# script'i öldürür. Bu dosyada aynı tuzağa birkaç yerde daha dikkat edildi.
anlat() { if (( KURU )); then echo "[kuru] $*"; fi; }

# ── Durum dosyası ─────────────────────────────────────────────────────────────
# Tek satır JSON. jq sunucuda garanti değil; alanlar sabit ve düz olduğu için
# sed ile okumak yeterli. Alanlar:
#   ardisik_hata        : üst üste kaç kritik/geri_alindi turu oldu (başarı sıfırlar)
#   son_kirmizi_commit  : kapıdan geçemeyen son commit — o commit tekrar denenmez
#   son_onkontrol       : son ön kontrol hatasının metni — aynı hata her turda
#                         kayda tekrar yazılmasın diye (60 sn'de bir aynı satır = gürültü)
DURUM_ARDISIK=0
DURUM_KIRMIZI=""
DURUM_ONKONTROL=""

durum_al() {
  local anahtar="$1"
  [[ -f "$DURUM" ]] || return 0
  sed -n "s/.*\"$anahtar\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" "$DURUM" | head -n1
}
durum_al_sayi() {
  local anahtar="$1"
  [[ -f "$DURUM" ]] || return 0
  sed -n "s/.*\"$anahtar\"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p" "$DURUM" | head -n1
}

durum_yukle() {
  DURUM_ARDISIK="$(durum_al_sayi ardisik_hata)"; DURUM_ARDISIK="${DURUM_ARDISIK:-0}"
  DURUM_KIRMIZI="$(durum_al son_kirmizi_commit)"
  DURUM_ONKONTROL="$(durum_al son_onkontrol)"
}

durum_yaz() {
  local ardisik="$1" kirmizi="$2" onkontrol="$3"
  if (( KURU )); then
    echo "[kuru] DURUM YAZILMADI → ardisik_hata=$ardisik son_kirmizi_commit=${kirmizi:0:7}"
    return 0
  fi
  [[ "$ardisik" =~ ^[0-9]+$ ]] || ardisik=0
  mkdir -p "$(dirname "$DURUM")"
  printf '{"ardisik_hata":%d,"son_kirmizi_commit":"%s","son_onkontrol":"%s","son_guncelleme":"%s"}\n' \
    "$ardisik" "$kirmizi" "${onkontrol//\"/\'}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$DURUM"
}

# JSON kaydı yaz. Alanlar: zaman, commit, onceki_commit, ruff, py_compile, sonuc,
# sure_sn, ardisik_hata, unit_senkron, not.  Kuru turda hiçbir şey yazılmaz — kayıt
# dosyası gerçekten olan biteni anlatsın, denemeleri değil.
# unit_senkron global $UNIT_DEGISEN'den gelir ("yok" ya da virgüllü dosya listesi):
# çağıran taraf her yerde aynı şeyi tekrar etmesin diye parametre değil.
kaydet() {
  local sonuc="$1" ruff="$2" pyc="$3" commit="$4" onceki="$5" aciklama="$6" ardisik="${7:-$DURUM_ARDISIK}"
  [[ "$ardisik" =~ ^[0-9]+$ ]] || ardisik=0
  if (( KURU )); then
    echo "[kuru] KAYIT YAZILMADI → sonuc=$sonuc ruff=$ruff py_compile=$pyc ardisik_hata=$ardisik unit_senkron=$UNIT_DEGISEN not=$aciklama"
    return 0
  fi
  mkdir -p "$(dirname "$KAYIT")"
  printf '{"zaman":"%s","commit":"%s","onceki_commit":"%s","ruff":"%s","py_compile":"%s","sonuc":"%s","sure_sn":%d,"ardisik_hata":%d,"unit_senkron":"%s","not":"%s"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$commit" "$onceki" "$ruff" "$pyc" "$sonuc" \
    "$((SECONDS - BASLANGIC))" "$ardisik" "${UNIT_DEGISEN//\"/\'}" "${aciklama//\"/\'}" >> "$KAYIT"
}

# ── UNIT SENKRONU ─────────────────────────────────────────────────────────────
# worker/systemd/ → $UNIT_HEDEF. Sadece İÇERİĞİ farklı olan dosya kopyalanır:
# her turda körlemesine kopyalamak mtime'ı değiştirir, gereksiz daemon-reload
# doğurur ve "ne zaman gerçekten değişti" bilgisini yok eder.
# Repo'da olmayan unit'lere DOKUNULMAZ (candan-brain, candan-dashboard gibi
# sunucuda elle duran şeyler var); bu tek yönlü bir kopyalama, ayna değil.
# Kopyalananların listesini stdout'a virgüllü yazar; hiçbiri değişmediyse boş.
unit_senkron() {
  local kopyalanan="" src ad hedef dizin unit
  [[ -d "$UNIT_KAYNAK" ]] || { printf ''; return 0; }

  local eski_nullglob=0
  if shopt -q nullglob; then eski_nullglob=1; fi
  shopt -s nullglob

  for src in "$UNIT_KAYNAK"/*.service "$UNIT_KAYNAK"/*.timer; do
    ad="$(basename "$src")"
    hedef="$UNIT_HEDEF/$ad"
    if cmp -s "$src" "$hedef"; then continue; fi
    kopyalanan="${kopyalanan:+$kopyalanan,}$ad"
    if (( KURU )); then
      anlat "  unit FARKLI (kopyalanmadi): $ad" >&2
    else
      install -m 0644 "$src" "$hedef"
    fi
  done

  # Drop-in'ler: worker/systemd/<unit>.service.d/*.conf → $UNIT_HEDEF/<unit>.service.d/
  for dizin in "$UNIT_KAYNAK"/*.service.d; do
    unit="$(basename "$dizin")"
    for src in "$dizin"/*.conf; do
      ad="$unit/$(basename "$src")"
      hedef="$UNIT_HEDEF/$ad"
      if cmp -s "$src" "$hedef"; then continue; fi
      kopyalanan="${kopyalanan:+$kopyalanan,}$ad"
      if (( KURU )); then
        anlat "  unit FARKLI (kopyalanmadi): $ad" >&2
      else
        mkdir -p "$UNIT_HEDEF/$unit"
        install -m 0644 "$src" "$hedef"
      fi
    done
  done

  if (( ! eski_nullglob )); then shopt -u nullglob; fi
  printf '%s' "$kopyalanan"
}

# Senkronu koştur, sonucu $UNIT_DEGISEN'e yaz, gerekiyorsa daemon-reload et.
# Kendi timer'ını RESTART ETMEZ — bkz. başlıktaki "kendi unit'ini güncelleme tuzağı".
unit_senkron_uygula() {
  local liste
  liste="$(unit_senkron)"
  if [[ -z "$liste" ]]; then
    UNIT_DEGISEN="yok"
    anlat "unit senkronu: fark yok"
    return 0
  fi
  UNIT_DEGISEN="$liste"
  if (( KURU )); then
    anlat "unit senkronu: FARKLI dosyalar var → $liste (normal turda kopyalanip daemon-reload yapilirdi)"
    return 0
  fi
  systemctl daemon-reload
  case "$liste" in
    *candan-autodeploy*)
      # Kayda düş ama timer'a dokunma: restart bu süreci öldürür.
      UNIT_DEGISEN="$liste (kendi timer'i degisti - restart YOK, sonraki tur gecerli)" ;;
  esac
}

temizle() {
  # Worktree her yolda gitmeli — kalırsa bir sonraki tur "already exists" ile patlar.
  git -C "$KOK" worktree remove --force "$CALISMA" >/dev/null 2>&1 || true
  rm -rf "$CALISMA"
  git -C "$KOK" worktree prune >/dev/null 2>&1 || true
}

# ── ÖN KONTROL ────────────────────────────────────────────────────────────────
# 2026-08-14 kazasının panzehiri. `ProtectSystem=strict` + `ReadWritePaths=` olan bir
# unit, listedeki yollardan biri yoksa mount namespace'ini kuramaz ve servis
# `status=226/NAMESPACE` ile açılmaz. Bu hata ÇALIŞAN sürece çarpmaz — namespace
# zaten kurulmuştur — sadece bir sonraki restart'ta patlar. Yani restart'ı tetikleyen
# taraf (biz) bunu önceden kontrol etmezse, mayına kendi ayağıyla basar.
#
# systemctl show drop-in'leri de birleştirilmiş halde verir; ayrıca .d/ okumaya
# gerek yok.  Kontrol edilen: ReadWritePaths, WorkingDirectory, ExecStart yorumlayıcısı.
onkontrol() {
  local eksik="" yol rwp exec_yol calisma

  rwp="$(systemctl show "$SERVIS" -p ReadWritePaths --value 2>/dev/null || true)"
  for yol in $rwp; do
    # systemd yol öneki: '-' (yoksa görmezden gel), '+' (root'a göre), '!' / '!!'.
    # '-' önekli yolun VAR OLMASI zorunlu değil — systemd de atlar, biz de atlarız.
    if [[ "$yol" == -* ]]; then continue; fi
    while [[ "$yol" == "+"* || "$yol" == "!"* ]]; do yol="${yol#?}"; done
    [[ -e "$yol" ]] || eksik="${eksik:+$eksik, }ReadWritePaths:$yol"
  done

  calisma="$(systemctl show "$SERVIS" -p WorkingDirectory --value 2>/dev/null || true)"
  calisma="${calisma#-}"
  if [[ -n "$calisma" && "$calisma" != "~" ]]; then
    [[ -d "$calisma" ]] || eksik="${eksik:+$eksik, }WorkingDirectory:$calisma"
  fi

  # ExecStart `{ path=... ; argv[]=... }` biçiminde gelir; bizi ilgilendiren
  # yorumlayıcı, yani path=. Venv silinmiş/bozulmuşsa servis yine açılmaz.
  exec_yol="$(systemctl show "$SERVIS" -p ExecStart --value 2>/dev/null \
    | sed -n 's/.*[{;] *path=\([^ ;]*\).*/\1/p' | head -n1)"
  if [[ -n "$exec_yol" ]]; then
    [[ -x "$exec_yol" ]] || eksik="${eksik:+$eksik, }ExecStart:$exec_yol"
  else
    eksik="${eksik:+$eksik, }ExecStart:okunamadi"
  fi

  # Sınama kancası — bkz. başlıktaki TEST KANCASI notu.
  if [[ -n "${AUTODEPLOY_TEST_EXTRA_PATH:-}" ]]; then
    [[ -e "$AUTODEPLOY_TEST_EXTRA_PATH" ]] \
      || eksik="${eksik:+$eksik, }TEST:$AUTODEPLOY_TEST_EXTRA_PATH"
  fi

  printf '%s' "$eksik"
}

cd "$KOK"
durum_yukle

# ── 0. Devre kesik mi? ────────────────────────────────────────────────────────
# Timer disable edilmiş olsa bile elle çağrılabilir; sayaç doluysa uyar ama engelleme
# (elle çalıştırmak bilinçli bir eylemdir). Kuru turda da sadece bilgi.
if (( DURUM_ARDISIK >= HATA_SINIRI )); then
  anlat "UYARI: ardisik_hata=$DURUM_ARDISIK (sinir $HATA_SINIRI) — devre kesilmis durumda."
fi

# ── 1. Yeni commit var mı? ────────────────────────────────────────────────────
# Repo anonim okunabiliyor; kimlik/token gerekmiyor. Ağ yoksa sessizce çık:
# her dakika koşan bir timer'ın geçici ağ hatası için gürültü üretmesi anlamsız.
if ! git fetch --quiet origin main; then
  anlat "git fetch basarisiz (ag?) — normal turda sessizce cikilir"
  exit 0
fi
HEDEF="$(git rev-parse origin/main)"
MEVCUT="$(git rev-parse HEAD)"
anlat "HEAD=$MEVCUT  origin/main=$HEDEF"

if [[ "$HEDEF" == "$MEVCUT" ]]; then
  # Kuru turda yeni commit olmaması testi engellemesin: ön kontrol ve kapı yine
  # koşsun, sadece deploy edilecek bir şey olmadığını söylesin.
  if (( ! KURU )); then exit 0; fi
  anlat "yeni commit yok — kontroller yine de kosuluyor"
fi

# ── 2. ÖN KONTROL: sistem deploy'u kaldırabilir mi? ───────────────────────────
EKSIK="$(onkontrol)"
if [[ -n "$EKSIK" ]]; then
  anlat "ON KONTROL KIRMIZI → deploy YOK. Eksik: $EKSIK"
  if [[ "$EKSIK" != "$DURUM_ONKONTROL" ]]; then
    kaydet "onkontrol_hatasi" "atlandi" "atlandi" "$HEDEF" "$MEVCUT" "sistem yolu eksik: $EKSIK"
    durum_yaz "$DURUM_ARDISIK" "$DURUM_KIRMIZI" "$EKSIK"
  fi
  exit 1
fi
anlat "on kontrol YESIL (ReadWritePaths + WorkingDirectory + ExecStart yerinde)"
# Ön kontrol düzeldiyse hatırayı temizle ki bir dahaki bozulma yeniden kayda geçsin.
if [[ -n "$DURUM_ONKONTROL" ]]; then
  durum_yaz "$DURUM_ARDISIK" "$DURUM_KIRMIZI" ""
  DURUM_ONKONTROL=""
fi

# ── 3. Bu commit zaten kırmızı aldı mı? ───────────────────────────────────────
if [[ "$HEDEF" == "$DURUM_KIRMIZI" && "$HEDEF" != "$MEVCUT" ]]; then
  anlat "bu commit daha once kapidan gecemedi — tekrar denenmiyor"
  exit 1
fi

# ── 4. Kontrol için ayrı worktree ─────────────────────────────────────────────
# sparse-checkout cone worktree'ye de uygulanır (yalnız worker/pi/server/scripts/tools
# iner) — bu kasıtlı, kontrol edilen küme canlıda duran kümeyle aynı olsun.
temizle
if ! git worktree add --detach "$CALISMA" "$HEDEF" >/dev/null 2>&1; then
  kaydet "kirmizi" "atlandi" "atlandi" "$HEDEF" "$MEVCUT" "worktree kurulamadi"
  exit 1
fi

# ── 5. Kapıdaki statik kontroller ─────────────────────────────────────────────
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
anlat "kapi: ruff=$RUFF_SONUC py_compile=$PYC_SONUC"

if [[ "$RUFF_SONUC" != "temiz" || "$PYC_SONUC" != "temiz" ]]; then
  # Kırmızı → hiçbir şeye dokunma. Eski sürüm çalışmaya devam eder; bir sonraki
  # push düzeltirse tur kendiliğinden yeşile döner. Aynı commit tekrar denenmez.
  temizle
  kaydet "kirmizi" "$RUFF_SONUC" "$PYC_SONUC" "$HEDEF" "$MEVCUT" "$HATA"
  durum_yaz "$DURUM_ARDISIK" "$HEDEF" ""
  exit 1
fi

temizle

# ── 6. Aktif oturum var mı? ───────────────────────────────────────────────────
# Tespit: worker journal'ında son OTURUM_PENCERE saniyede "job_id" geçen satır.
# Boşta duran worker job logu ÜRETMEZ; iş süreci (livekit job) her satıra job_id
# ekler. pgrep'ten daha güvenilir: job alt-süreci spawn_main ile açılıyor,
# komut satırında job_id GEÇMİYOR.
if journalctl -u "$SERVIS" --since "-${OTURUM_PENCERE}s" --no-pager 2>/dev/null \
     | grep -q '"job_id"'; then
  anlat "aktif oturum var — normal turda deploy ERTELENIR"
  kaydet "ertelendi" "temiz" "temiz" "$HEDEF" "$MEVCUT" "aktif oturum: son ${OTURUM_PENCERE}s icinde job logu var"
  exit 0
fi

# ── 7. Deploy ─────────────────────────────────────────────────────────────────
# .env / memory/ / worker/data/ / worker/logs/ ya gitignore'da ya takipsiz →
# reset --hard onlara DOKUNMAZ. Bu bilinçli; bozma.
if (( KURU )); then
  if [[ "$HEDEF" == "$MEVCUT" ]]; then
    anlat "SONUC: her sey yesil, deploy edilecek yeni commit yok."
  else
    anlat "SONUC: her sey yesil. Normal turda simdi yapilacakti:"
    anlat "  git reset --hard $HEDEF"
    anlat "  systemctl restart $SERVIS  (+ ${SAGLIK_SANIYE}s 'registered worker' beklemesi)"
  fi
  # Kuru turda henüz reset yapılmadığı için karşılaştırma ÇALIŞMA AĞACINDAKİ
  # unit'lerle olur; yine de "repo ile /etc ayrışmış mı" sorusunu cevaplar.
  unit_senkron_uygula
  anlat "reset/restart/unit kopyalama YAPILMADI, kayit YAZILMADI."
  exit 0
fi

ONCEKI="$MEVCUT"
ISARET="$(date '+%Y-%m-%d %H:%M:%S')"
git reset --hard --quiet "$HEDEF"
# Unit'ler reset'ten SONRA, restart'tan ÖNCE: yeni commit'in unit dosyaları diske
# inmiş olur ve worker yeni tanımla açılır.
unit_senkron_uygula
systemctl restart "$SERVIS"

# ── 8. Sağlık: worker LiveKit'e kaydolabildi mi? ──────────────────────────────
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
  # Başarı ardışık hata sayacını sıfırlar: devre kesici "art arda" içindir,
  # aylara yayılmış tekil hataları biriktirmemeli.
  durum_yaz 0 "" ""
  kaydet "basarili" "temiz" "temiz" "$HEDEF" "$ONCEKI" "kayit ${i}s icinde geldi" 0
  exit 0
fi

# ── 9. Geri alma ──────────────────────────────────────────────────────────────
ARDISIK=$(( DURUM_ARDISIK + 1 ))
GERI_ISARET="$(date '+%Y-%m-%d %H:%M:%S')"
git reset --hard --quiet "$ONCEKI"
# Geri alınan commit'in unit'leri de geri gelsin: kod eski, unit yeni kalırsa
# ikisi uyumsuz olur ve geri alma tam olarak eski çalışan hâle dönmez.
ILERI_UNIT="$UNIT_DEGISEN"
unit_senkron_uygula
# Kayıt hem ileri hem geri turda ne kopyalandığını göstersin; geri alma turunda
# "yok" yazıp ileri turda kopyalananları gizlemek yanıltıcı olur.
UNIT_DEGISEN="ileri: $ILERI_UNIT / geri: $UNIT_DEGISEN"
systemctl restart "$SERVIS"
SONUC="kritik"
NOT="geri alma sonrasi da kayit yok - ELLE BAK"
CIKIS=2
for ((i = 0; i < SAGLIK_SANIYE; i++)); do
  if journalctl -u "$SERVIS" --since "$GERI_ISARET" --no-pager 2>/dev/null \
       | grep -q "registered worker"; then
    SONUC="geri_alindi"
    NOT="yeni commit kaydolamadi, eski surume donuldu"
    CIKIS=1
    break
  fi
  sleep 1
done

# Geri alma da kaldıramadıysa (kritik) sorun deploy'da değil, ortamda. Elle bakılmalı.
durum_yaz "$ARDISIK" "$DURUM_KIRMIZI" ""
kaydet "$SONUC" "temiz" "temiz" "$HEDEF" "$ONCEKI" "$NOT" "$ARDISIK"

# ── 10. Devre kesici ──────────────────────────────────────────────────────────
# 2026-08-14: script her 2 dakikada bir aynı ölü deploy'u yeniden denedi ve worker'ı
# 130 kez restart etti. Tekrar, kırık bir ortamı onarmaz. Art arda HATA_SINIRI kadar
# başarısız turda timer kapanır; insan bakıp `systemctl enable --now` ile açar.
if (( ARDISIK >= HATA_SINIRI )); then
  systemctl disable --now "$TIMER" >/dev/null 2>&1 || true
  kaydet "devre_kesildi" "temiz" "temiz" "$HEDEF" "$ONCEKI" \
    "art arda $ARDISIK basarisiz tur - $TIMER kapatildi; elle bak, duzelince: systemctl enable --now $TIMER" \
    "$ARDISIK"
fi

exit "$CIKIS"
