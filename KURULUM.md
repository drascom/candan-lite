# KURULUM — sıfırdan Mac istemcisi + sunucu kontrol listesi

Bu belge, Mac'teki her şey silindiğinde (venv, kurulumlar) sistemi tekrar ayağa
kaldırmak içindir. Komutlar kopyala-yapıştır çalışır. Sunucu tarafındaki komutlar
`(doğrulanmadı)` diye işaretlidir — bu belge yazılırken sunucuya dokunulmadı.

## 0. İki ayrı ortam — EN KRİTİK AYRIM

| | Mac (bu makine) | Sunucu 192.168.0.25 |
|---|---|---|
| rolü | **istemci** | **worker (asıl iş)** |
| çalışan | `worker/cli_client.py` (`./go.sh`) | `agent.py` + servisler |
| bağımlılık | `worker/requirements-client.txt` (~80 MB) | `worker/requirements.txt` (torch, livekit-agents, turn-detector, wyoming, scipy — GB'lar) |

**Mac'te worker ÇALIŞMAZ.** Gerekçe `worker/go.sh` başlığında: bu Mac'te pi CLI kurulu
değil → beyin her turda `FileNotFoundError` ile çöker, oturum sessiz kalır.
(Kaçış yolu var ama pi CLI kurulmadan işe yaramaz: `GO_SH_LOKAL_WORKER=1 ./go.sh`.)

**KARIŞTIRMA:** Mac'e `requirements.txt` kurmak boşuna ~2 GB torch indirir.

## 1. Mac kurulumu (birincil yol)

```bash
cd worker
python3 -m venv .venv
.venv/bin/pip install -r requirements-client.txt
./go.sh --list-devices      # doğrulama: ses aygıtları listelenmeli
./go.sh                     # sesli oturum (iş sunucudaki worker'a gider)
```

`./go.sh --list-devices` bu belge yazılırken çalıştırıldı, çıkışı 0 (Python 3.13.15).

Python 3.13 notu: `audioop` stdlib'den kaldırıldı; `audioop-lts` requirements'ta var.
Yalnız `--dump-audio` için gerekir, normal oturumu etkilemez.

`ruff` (kapı aracı) venv'e ayrıca kurulur — `./check.sh` onu `worker/.venv/bin/ruff`
yolunda arar:

```bash
worker/.venv/bin/pip install ruff
./check.sh                  # statik analiz kapısı (ruff + tsc); CI/pre-commit YOK
```

## 2. Git'te OLMAYAN, ayrıca temin edilmesi gerekenler

Bunlar `.gitignore`'da. Yeni kurulumda EKSİK olurlar ve unutulursa sistem **sessizce**
çalışmaz.

- **`worker/.env`** — ZORUNLU. `go.sh` yoksa çalışmayı reddeder (`HATA: worker/.env yok.`).
  Şablon: `worker/.env.example` (36 KB, 124 anahtar, açıklamalı). Kopyalayıp doldur:
  `cp worker/.env.example worker/.env`. En az gerekenler: `LIVEKIT_URL`,
  `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `MATE_LIVEKIT_ROOM`, `STT_HOST/PORT`, `TTS_HOST`.
- **`assets/voice/default-ref.wav`** — Candan'ın ses kimliği (Higgs TTS her cümleyi
  bundan klonlar). Depo PUBLIC olduğu için `.gitignore` bunu *biyometrik veri* sayıp
  dışlıyor; ses-klon referansını public geçmişe yazmak **geri alınamaz**. Dosyanın
  künyesi (md5 `429e867b…`, 48 kHz mono 7.20 sn, 691.244 bayt) ve geri getirme yolu
  `assets/voice/README.md`'de. İstenirse: `git add -f assets/voice/default-ref.wav`.
- **Sunucuda pi kimliği** — `/var/lib/candan/.pi/agent/auth.json` (OAuth; git'te olamaz).
- **Sunucuda model ağırlıkları** — ReDimNet2 torch cache, Whisper, Higgs. Turn-detector
  ağırlıkları pip'le gelmez: `python -m livekit.agents download-files` (~460 MB, HF cache).
- **`worker/tests/`** — bilerek git dışı (kullanıcı kararı, `.gitignore:52`). Sunucuda da
  yok; autodeploy test koşmaz.
- Ayrıca git dışı: `logs/`, `sessions/`, `memory/`, `worker/models/`, `worker/data/`,
  `docs/`, `handoff/`, `web/`, `experiments/`, `bench/` (2026-08-14 kararı: repo çalışan
  koda indirildi).

## 3. Sunucu tarafı (kontrol listesi — sıfırdan kurulum beklenmiyor)

Kod: `root@192.168.0.25:/opt/candan-lite`. Sunucuda **sparse-checkout** aktif: yalnız
`worker pi server scripts tools` + kök dosyalar iner (`scripts/autodeploy.sh:359`).

Servisler (systemd adları):

| unit | ne |
|---|---|
| `candan-worker` | asıl worker (`worker/.venv/bin/python agent.py dev`) |
| `higgs-tts` | TTS |
| `whisper` | STT (wyoming, `.25:10300`) |
| `pi-service` | pi broker (normal mod) |
| `pi-dev` | pi broker (dev mod) |
| `searxng` | arama |
| `candan-autodeploy.timer` | deploy tetikleyicisi (60 sn) |
| ~~`candan-brain`~~ | eski yerel Gemma — **EMEKLİ/disabled** |

Unit dosyaları repoda: `worker/systemd/` (`candan-worker.service` + `.service.d/`,
`pi-service.service`, `candan-autodeploy.service/.timer`). Deploy bunları sunucuya
senkronlar; elle scp gerekmez.

Hızlı sağlık (doğrulanmadı — sunucuda koşulur):

```bash
ssh root@192.168.0.25 'systemctl is-active candan-worker higgs-tts whisper pi-service'
ssh root@192.168.0.25 'tail -1 /opt/candan-lite/worker/logs/deploy.jsonl'
```

`candan-worker.service` `ProtectSystem=strict` + `ReadWritePaths` kullanıyor: listedeki
dizinlerin (`memory/`, `sessions/`, `worker/data`, `worker/logs`, `handoff/`, `voice/`)
sunucuda **var olması zorunlu**. Yoksa servis `status=226/NAMESPACE` ile açılmaz —
2026-08-14'te canlıyı 11 dk düşüren kaza buydu.

## 4. Deploy — nasıl çalışıyor

```
git push origin main
   → candan-autodeploy.timer (60 sn'de bir) origin/main ilerledi mi bakar
   → ön kontrol: ReadWritePaths yolları dosya sisteminde var mı
   → kapı: ayrı worktree'de ruff + py_compile
   → git reset --hard
   → systemd unit senkronu (worker/systemd/ → /etc/systemd/system) + daemon-reload
   → systemctl restart candan-worker
   → 60 sn içinde "registered worker" yoksa OTOMATİK GERİ ALMA
```

- Kayıt: `worker/logs/deploy.jsonl` (sunucuda; `logs/` gitignore'da).
- Durum: `worker/logs/.autodeploy-state.json` (ardışık hata sayacı, son kırmızı commit).
- Aktif oturum varsa deploy bir tur ertelenir. Kapıdan geçemeyen commit tekrar denenmez.
- Üst üste 3 başarısız tur → timer kendini kapatır (devre kesici).
- **Elle scp YAPILMAZ.** `scripts/deploy-turn-identity.sh` emekli.

Canlıyı kırmadan sınama: `scripts/autodeploy.sh --dry-run` (sunucuda; reset/restart yapmaz).

## 5. Sorun giderme — "Candan sessiz" teşhis sırası

Tümü sunucuda koşar (doğrulanmadı):

1. `journalctl -u candan-worker | grep turn_metric` → hiç yoksa beyin cevap vermemiş.
2. `pi` süreci var ama CPU 0 + ağ bağlantısı 0 → kimlik sorunu (auth.json).
3. `stat -c %s /var/lib/candan/.pi/agent/auth.json` → 2 bayt (`{}`) ise kimlik YOK.
4. **Uyanma kapısı:** uyanma sözcüğü olmayan söz metrikten ÖNCE düşer, iz bırakmaz
   (`worker/pi_brain.py:3399`, `_wake_decide`). Test ederken cümleye **"Candan,"** ile başla.

## 6. Pano

```bash
python3 tools/dashboard.py        # http://<host>:8765
```

Salt-okunur, bağımlılık yok (yalnız stdlib). Pano **sunucuda** koşuyor (baktığı veri
orada): varsayılan bind `0.0.0.0`, yani `http://192.168.0.25:8765`. Mac'te yerel
çalıştırmak için `DASHBOARD_HOST=127.0.0.1 python3 tools/dashboard.py`; port meşguldeyse
`DASHBOARD_PORT=8766`.
