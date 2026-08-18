# candan-lite

> Sıfırdan kurulum (Mac istemcisi, git'te olmayan dosyalar, sunucu/deploy): **[KURULUM.md](KURULUM.md)**

Candan sesli asistanı. Ağır Hermes+plugin yığınının yerine ince bir
**livekit-agents worker**'ı: ses hattı framework'ten, beyin uzak bir ajan,
STT/TTS kendi sunucularımızda.

## Mimari

```
istemci (worker/cli_client.py, ./go.sh)
        │  ses + metin
        ▼
   LiveKit  (wss://mate-livekit.drascom.uk, oda: candan-lite-dev)
        ▲
        │
   worker/  (192.168.0.25 · candan-worker.service)
        ├─ STT          → Wyoming faster-whisper   (.25:10300, GPU)
        ├─ TTS          → Higgs TTS 3 (4B)         (.25:8809, akış açık)
        ├─ VAD          → livekit silero
        ├─ tur sonu     → livekit turn-detector (çok dilli EOU)
        ├─ barge-in     → worker/barge.py  (ses değil ANLAM: yeni komut mu, geri bildirim mi)
        ├─ konuşmacı ID → ReDimNet2-B6  (worker/data/speakers-redimnet2.db)
        ├─ web arama    → SearXNG (.25:8888)
        └─ beyin        → pi ajanı (broker soketi) → uzak OpenAI Codex
                            normal: gpt-5.6-terra · dev: gpt-5.6-sol
```

## Klasörler (git'te olanlar)

| dizin | ne |
|---|---|
| `worker/` | livekit-agents worker'ı — beyin köprüsü, STT/TTS istemcileri, konuşmacı kimliği, hafıza araçları |
| `pi/` | pi ajanının uzantıları, personaları, becerileri (`AGENTS.md`, `personas/`, `extensions/`, `skills/`) |
| `server/` | Higgs TTS servisi (systemd unit + sunucu kodu) |
| `scripts/` | `autodeploy.sh` ve yardımcılar |
| `tools/` | `dashboard.py` — salt-okunur pano |

Repo bilerek yalın: `experiments/`, `web/`, `handoff/`, `bench/`, `docs/`, `assets/`
2026-08-14'te takipten çıkarıldı (dosyalar Mac'te duruyor, git izlemiyor).
`worker/tests/` de bilerek git dışında.

## Durum (18 Ağustos 2026)

Sistem **çalışır durumda**. Kurulum fazları tamamlandı.

- Beyin **uzak Codex**'te (14 Ağu). Yerel Gemma (`candan-brain.service`) emekli/disabled;
  GPU'da ~12,9 GB boşaldı.
- TTS **Higgs**, 10 duygu ön ayarıyla (`[mood:X]` → `<|emotion:*|>`, bkz. `docs/` dışı
  referans: `worker/higgs_tts.py` başlığı).
- Konuşmacı kimliği **ReDimNet2-B6** (14 Ağu kullanıcı kararı; CAM++/AS-norm hattı eski).
- Deploy **git tabanlı**: `git push` → 60 sn içinde canlı (aşağıya bak).

### Ölçülüp elenen adaylar
- **Supertonic TTS** — ses/hız iyi, ama duygu kontrolü yok ve proje arşivleniyor (16 Ağu).
- **audio.cpp** — Higgs 1.54× hızlı ve VRAM 22,8 → 8,2 GB, **ama akış yok**: ilk ses
  gecikmesi uzun cümlede 574 ms → 5997 ms. Ertelendi; tetikleyici `loader.cpp`'de
  Higgs için `RunMode::Streaming` (16 Ağu).

## Deploy

```bash
git push origin main      # gerisi otomatik
```

`candan-autodeploy.timer` 60 saniyede bir bakar → ön kontrol (systemd `ReadWritePaths`
yolları var mı) → `ruff` + `py_compile` kapısı → `git reset --hard` → systemd unit
senkronu → worker restart → 60 sn içinde `registered worker` gelmezse **otomatik geri
alma**. Üst üste 3 hatada devre kesici timer'ı kapatır.

Kayıt: `worker/logs/deploy.jsonl` (sunucuda). Elle `scp` **yapılmaz**
(`scripts/deploy-turn-identity.sh` emekli).

## Pano

```bash
python3 tools/dashboard.py        # http://<host>:8765
```

Salt-okunur, bağımlılık yok (yalnız stdlib), real-time değil — **sayfayı yenile**.
Sürekli çalışan bir servis değil; elle başlatılır. Varsayılan bind `0.0.0.0` ve
**kimlik doğrulama yok** (bilinçli karar, `tools/dashboard.py:27`) — açıkken ev ağındaki
herkes okuyabilir, işin bitince kapat.

- **Oturumlar** — `sessions/*.jsonl` pi transkriptleri. Silme YOK: onaylı şekilde
  `sessions/.trash/` içine **taşınır**.
- **Hafıza** — `memory/` (family.md, users/*, policy.json, events.db, conversations.db)
  + `worker/data/speakers-redimnet2.db`. SQLite'lar `mode=ro` ile açılır.

## Kalite kapısı

```bash
./check.sh        # ruff — F823 ("logging bombası") ve akrabalarını yakalar
```

Gerekçesi `ruff.toml` başında yazılı: `agent.py:255` canlıda `UnboundLocalError` ile
patladı ve testler yakalayamazdı, çünkü o kod yalnız gerçek bir LiveKit job'ında çalışır.
