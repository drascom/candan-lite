# OmniVoice KALDIRILDI — 28 Temmuz 2026

Kullanıcı kararı: *"Önce OmniVoice'u komple silip kaldıralım. Sonra Piper'a bakalım."*
Kapsam: sunucu artıkları **ve** kod. Higgs artık **tek motor**; `TTS_ENGINE`
dallanması yok.

⚠️ **SİLME GERİ ALINAMAZ.** Model + venv birlikte 10+ GB; geri dönüş "yeniden indir +
yeniden kur" demektir, tek komut değildir. Bu belge geri dönüş için değil, **ne
kaybedildiğini ve ses kimliğinin nerede olduğunu** kayda geçirmek içindir.

---

## 1. Candan'ın ses kimliği — TAŞINDI (önce bu yapıldı)

`default-ref.wav` Higgs'in her cümlede klonladığı kaynak. Üçüncü bir servisin
dizininde (`/opt/omnivoice/`) durmasın diye **projenin içine** alındı:

| | |
|---|---|
| eski | `/opt/omnivoice/default-ref.wav` |
| **yeni** | `/opt/candan-lite/assets/voice/default-ref.wav` |
| bu Mac'te | `assets/voice/default-ref.wav` |
| md5 | `429e867bcad6cf6e8c4109efe88f5e6f` (üç eski kopyanın hepsi birebir aynıydı) |
| biçim | 48 kHz mono 16 bit · 7.20 sn · 691.244 bayt |

Eski üç kopya — `/opt/omnivoice/default-ref.wav`,
`/opt/omnivoice/default-ref.wav.bak-20260726`, `/opt/omnivoice-ref.wav` — **md5'leri
aynıydı** (ölçüldü), yani ayrı bir "eski referans" yoktu; tek dosyanın üç kopyasıydı.
Bu yüzden projeye tek kopya alındı, kalanlar OmniVoice ile birlikte silindi.

**Git:** dosya **commit EDİLMEDİ**, gerekçesi `assets/voice/README.md` içinde:
depo PUBLIC ve kökteki `.gitignore` referans wav'larını üç ayrı yerde *"biyometrik
veri, girmez"* diye dışlıyor. Ses klonu referansını public geçmişe yazmak geri
alınamaz. **Kullanıcı onayı bekliyor** — istenirse tek adım:
`git add -f assets/voice/default-ref.wav`.

### Referans kodları (`default-ref.codes.pt`) — wav'dan üretilebildiği ÖLÇÜLDÜ

Higgs çalışma anında wav'ı okumuyor: `HIGGS_REF_CODES`
(`/opt/higgs-tts/refs/default-ref.codes.pt`) önceden hesaplanmış kodları tutuyor.
Taşımanın gerçekten çalıştığını kanıtlamak için kod dosyası **bilerek silinip**
servis yeniden başlatıldı → servis **yeni yoldaki wav'dan** kodları yeniden üretti.

```
sha256 (eski kodlar)          : 812ae445d6e5f767a738f3652f112e020a26163345eb8a62f7d5bd6c496b6fc4
sha256 (wav'dan yeniden üretim): 812ae445d6e5f767a738f3652f112e020a26163345eb8a62f7d5bd6c496b6fc4  ← BIREBIR AYNI
```

Yeniden üretme komutu (belgelenmesi istenen yordam):

```bash
ssh root@192.168.0.25 'rm /opt/higgs-tts/refs/default-ref.codes.pt && \
  systemctl restart higgs-tts'
# log: "referans kodlandı: <wav> (N kare, X sn) → <codes.pt>"
```

Yedek: `/opt/higgs-tts/refs/default-ref.codes.pt.bak-20260728`.

### Ses kimliği restart ÖNCESİ / SONRASI

Aynı cümle (`"Merhaba, ben Candan. Bugün hava çok güzel görünüyor."`), 3'er üretim,
tam-WAV ucundan. Üretim örneklemeli (`temperature=0.8`) olduğu için birebir bayt
beklenmiyor; ölçü F0 + süre:

| | süre (s) | F0 ort (Hz) | F0 medyan (Hz) |
|---|---|---|---|
| önce | 3,60 · 3,52 · 3,40 (ort **3,51**) | 198,1 · 195,2 · 200,5 (ort **197,9**) | 193,5 · 189,0 · 192,8 (ort **191,8**) |
| sonra | 3,24 · 3,64 · 3,40 (ort **3,43**) | 190,9 · 187,6 · 208,7 (ort **195,7**) | 190,5 · 182,5 · 193,5 (ort **188,8**) |

Ayrıca sunucunun kendi `ref_fingerprint`'i (referans KODLARININ sha256'sı,
`GET /api/default`) **değişmedi**: `5248a2a9f6742732` → `5248a2a9f6742732`. Bu, kimliğin
"benziyor" değil **birebir aynı** olduğunun kanıtı; `tts_cache` anahtarları da
bu yüzden geçerliliğini korudu (cache temizlemeye gerek olmadı).

---

## 2. Sunucudan silinenler

| ne | boyut |
|---|---|
| `/opt/omnivoice-venv` | 7,3 GB |
| `/opt/hf-cache/hub/models--k2-fsa--OmniVoice` | 3,1 GB |
| `/opt/omnivoice/` (köprü sunucu, bridge.env, README, pronounce_tr.json, wav kopyaları) | 1,5 MB |
| `/opt/omnivoice-ref.wav` (wav'ın 3. kopyası) | 691 KB |
| `/opt/omnivoice-torch-install.log`, `/opt/omnivoice-pkg-install.log` | 34 KB |
| `omnivoice-bridge.service` (unit dosyası + `daemon-reload`) | — |

Disk: `149 GB` boş → **`159 GB` boş** (`10 GB` kazanıldı).

`higgs-tts.service`'ten `Conflicts=omnivoice-bridge.service` ve
`After=omnivoice-bridge.service` satırları da kaldırıldı (artık çakışacak bir şey yok).
Yedek: `/etc/systemd/system/higgs-tts.service.bak-omnivoice-20260728`.

`/opt/higgs-tts/higgs.env` ve `server.py` yeni referans yoluna çevrildi. Yedekler:
`higgs.env.bak-omnivoice-20260728`, `server.py.bak-omnivoice-20260728`.

### DOKUNULMAYANLAR (bilerek)

* `/opt/higgs-exp/` ve `/opt/candan-lite-selfdev/` — deney / self-dev alanları,
  içlerinde OmniVoice izleri var ama canlı değiller.
* `/opt/omni_*.wav`, `/opt/omni_*.log` (9 Tem kurulum testi çıktıları, ~1,5 MB) —
  OmniVoice artığı ama zararsız; silmek yer kazandırmıyor, bilerek bırakıldı.
* `/root/cakisma.py` — `journalctl -u omnivoice-bridge` okuyan tek seferlik analiz
  script'i. Artık çalışmaz (servis yok). Tarihî, dokunulmadı.
* `pi-service`, `candan-brain` — hiç dokunulmadı.

---

## 3. Koddan çıkanlar

* **silindi:** `worker/omnivoice_tts.py` (529 satır)
* `worker/agent.py`: `TTS_ENGINE` kolu, `OmniVoiceTTS` importu ve `if/else` dallanması
  gitti; `HiggsTTS` doğrudan kuruluyor. Kullanılmayan `TTS_PORT` de kalktı.
* `worker/.env.example`: `TTS_ENGINE=higgs` ve `TTS_PORT` satırları + "geri dönüş
  tek satır → OmniVoice" talimatı kaldırıldı.
* `worker/tts_cache.py`: `--prewarm` yolu `omnivoice_tts` yerine `higgs_tts`
  kullanıyor (varsayılan uç `:8809`), parmak izi `higgs_tts.ref_fingerprint`'ten
  alınıyor — motor önekli anahtarla aynı hizada olsun diye.
* `worker/requirements.txt`: `websockets` (yalnız OmniVoice WS köprüsü içindi) çıktı.
* `higgs_tts.py`, `log_utils.py`, `pi_brain.py`, `trnorm.py`, `truth_check.py`:
  yanıltıcı hâle gelen yorumlar düzeltildi.
* **Korunan tarihî kayıtlar** (silinmedi, bilerek): OmniVoice'ta `instruct`'ın ölü
  olması, cache anahtarında motor önekinin NEDEN şart olduğu, etiketleri harfi harfine
  okuma tuzağı, `wake_stt` fizibilitesinin OmniVoice sesiyle yapılmış olması.
  Bunlar ölçüm kaydı; bir sonraki motorda (Piper) aynı tuzaklar tekrar eder.

**Test: 399 → 378.** Fark, yalnızca OmniVoice eklentisini test eden
`tests/test_tts_short_guard.py` (21 test) dosyasının silinmesinden geliyor;
aynı davranışların Higgs karşılıkları `tests/test_higgs_tts.py` içinde zaten var
(boş metin → sessizlik, retry, cache anahtarı, nokta guard'ı). Higgs tarafında
kapsam KAYBI yok. `./check.sh`: yeni bulgu yok.

---

## 4. Deploy (yalnız `candan-worker`)

Gönderilen: `agent.py`, `higgs_tts.py`, `tts_cache.py`, `log_utils.py`, `pi_brain.py`,
`trnorm.py`, `truth_check.py`, `requirements.txt` — **8 dosyanın da md5'i lokalle
birebir aynı**. Sunucuda `worker/omnivoice_tts.py` silindi, `.env`'den `TTS_ENGINE`
ve `TTS_PORT` satırları çıktı. Yedekler: `worker/*.bak-omnivoice-20260728`.

* import doğrulaması: `agent, higgs_tts, tts_cache, trnorm, truth_check, log_utils`
  hepsi açılıyor; `import omnivoice_tts` → `ImportError` (beklenen).
* `systemctl restart candan-worker` → `active`, `registered worker` (agent_name candan).
* **Canlı ses:** deploy edilen `higgs_tts.py` üzerinden bir cümle sentezlendi →
  **9 blok / 134.400 bayt / 2,80 sn ses**. Ses geliyor.
* `pi-service` ve `candan-brain`'e **dokunulmadı**.

---

## 5. Geri dönüş

**OmniVoice'a geri dönüş YOKTUR** — model ve venv silindi (10+ GB yeniden indirme).
Kullanıcının kararı zaten buydu: TTS kalıcı arıza verirse yol **Piper**, ayrı iş.

Bu görevin kendi değişiklikleri için geri dönüş (ses kimliği YERİNDE kalır):

```bash
# Higgs'i eski referans YOLUNA döndür — ANLAMSIZ (/opt/omnivoice yok), yalnız
# kayıt: env/unit/server.py yedekleri duruyor.
ssh root@192.168.0.25 'cd /opt/higgs-tts && \
  cp -a higgs.env.bak-omnivoice-20260728 higgs.env && \
  cp -a server.py.bak-omnivoice-20260728 server.py && \
  cp -a /etc/systemd/system/higgs-tts.service.bak-omnivoice-20260728 \
        /etc/systemd/system/higgs-tts.service && \
  cp -a refs/default-ref.codes.pt.bak-20260728 refs/default-ref.codes.pt && \
  systemctl daemon-reload && systemctl restart higgs-tts'

# Worker kodunu geri al (yedekler sunucuda duruyor; omnivoice_tts.py GERİ GELMEZ,
# git'ten alınmalı: `git show HEAD~1:worker/omnivoice_tts.py`)
ssh root@192.168.0.25 'cd /opt/candan-lite/worker && \
  for f in agent higgs_tts tts_cache log_utils pi_brain trnorm truth_check; do \
    cp -a $f.py.bak-omnivoice-20260728 $f.py; done && \
  cp -a .env.bak-omnivoice-20260728 .env && systemctl restart candan-worker'
```

⚠️ **SES KİMLİĞİ NEREDE (en kritik satır):**
`/opt/candan-lite/assets/voice/default-ref.wav` **ve** bu Mac'te
`assets/voice/default-ref.wav` — md5 `429e867bcad6cf6e8c4109efe88f5e6f`.
İkisi de giderse Candan'ın sesi geri gelmez. Git'te DEĞİL (bkz. §1).
