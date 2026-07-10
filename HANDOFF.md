# candan-lite — HANDOFF (2026-07-10)

> ⚠️ **AŞAĞIDAKİ BÖLÜM GÜNCELDİR — önce bunu oku.** Altındaki eski bölümler tarihsel
> (özellikle "Beyin = OpenAI-uyumlu /v1 + PIDEV_BASE_URL" İPTAL — pivot edildi).

## 🟢 GÜNCEL DURUM (2026-07-10 session sonu) — hepsi çalışıyor, main'de push'lu

**Repo:** github.com/drascom/candan-lite (PUBLIC). `web/` absorbe edildi. `memory/` gitignored (kişisel veri public'e girmez) + içinde nested audit-git.

**Mimari (kilitli):**
- **Beyin = `pi` CLI**, warm `--mode rpc` alt-süreci (HTTP /v1 DEĞİL). Codex subscription. Model pin: `PI_MODEL=openai-codex/gpt-5.6-terra` (global `gpt-5.6-luna` bozuk). thinking=minimal. `worker/pi_brain.py`. Detay: `docs/pi-brain-design.md`.
- **Ses:** livekit-agents `AgentSession` — web → Whisper STT (wyoming .25:10300) → pi beyni → OmniVoice TTS (.25:8808, **24kHz**) → ses + barge-in.
- **Speaker-ID:** campplus (sherpa-onnx), konuşarak oto-enroll (bilinmeyen ses→"adını söyler misin?"→onay→kaydet). **Enrollment ODA sesinden olmalı** (CLI mic yolu tanımayla uyuşmaz). Sticky (`SPEAKER_VAD_RMS`/`SPEAKER_STICKY_MISSES`). Kullanıcı=slug → persona `pi/personas/<user>.md` + session `<user>` + `MEM_USER`.
- **Hafıza = PI-NATIVE (Hermes YOK):** `pi-hermes-memory` extension KALDIRILDI (`pi remove`). Kendi **lokal** extension'ımız `pi/extensions/mem/index.ts` (`memory_add`/`memory_search`, node:sqlite FTS5, per-user `memory/users/<user>/`, rol=policy.json). Worker `-e` ile SADECE kendi pi'sine yükler. Boot: profile.md+family.md enjeksiyon. Session-finalize (kapanışta 3-5 kalıcı not). Detay: `docs/hafiza-v2-plan.md` (⚠️ Hermes-çerçeveli, tarihsel) yerine gerçek = `pi/extensions/mem/`.

**Wake word tasarımı (worker-tarafı):**
- Wake word **"candan"**, akış: **tek başına "candan" → çan → sonra soru** (iki-adım). Konuşma penceresi 15s sessizlikte uyur. Uyurken sessiz (token yok). `WakeGate` PiBrain'de; `wake_match()` merkezi (izole/cümle ayrımlı **fuzzy** — izole yanlış-çevirileri "John Don/Can dan/Kandan" yakalar, cümlede sadece gerçek "candan").
- **Wake durumu web'e:** `candan.awake` participant attribute. Web: uyurken kullanıcı transcript'ini GİZLER + uyan/uyu **çanı** (Web Audio, %100). Transcript'i worker'da toggle ETME (TranscriptSynchronizer bozuluyor — web'de gizle).
- **Anlık wake:** `WAKE_STT` (VAD + kısa-pencere Whisper, ~200ms, SADECE uyurken) — erken çan. `wake_now()` idempotent.
- **Env (worker/.env):** `WAKE_ENABLED=true WAKE_WORD=candan WAKE_WINDOW_SECONDS=15 WAKE_VARIANTS=candan,kandan,... WAKE_STT_ENABLED=true WAKE_STT_WINDOW=1.5`.

**Web UI:** dark mode, LiveKit branding YOK, **auto-connect** (başlat/bitir butonu YOK), alt **debug durum satırı** (😴Uykuda/👂Dinliyorum/🧠Düşünüyorum/🗣️Konuşuyorum), waveform. **Kamera + ekran-paylaşımı butonları KALDI** (ileride görüntü/ekran-desteği — KULLANICI istedi, KALDIRMA).

**.25 GPU:** Qwen fallback (vllm.service) **durduruldu+disable+silindi** (14.6GB GPU + 9GB disk açıldı). Whisper+OmniVoice sağlam.

## Çalıştırma
- Worker (Mac): `cd worker && .venv/bin/python agent.py dev` (venv kurulu; sherpa-onnx/soundfile/sounddevice/websockets dahil).
- Web: `cd web && pnpm dev` → localhost:3000.
- Worker RESTART sonrası: tarayıcı sekmesini TAM KAPAT → yeniden bağlan (mevcut odaya oto-dispatch olmaz).

## 🔬 AÇIK İŞ: anlık wake için KWS "Jackie" (yeni session'da devam)
- **Türkçe "candan" onnx-KWS ile yakalanMIYOR** (GigaSpeech İngilizce akustik model). Whisper-"candan" (WAKE_STT) çalışıyor ama izole-candan Whisper'da zayıf → fuzzy ile telafi.
- **Fikir:** wake word'ü İngilizce **"Jackie"/"Hey Jackie"** yap → İngilizce KWS on-device anlık yakalar (Whisper'sız). Encode HAZIR, KWS mekanizması KANITLI (referans "forever" wav tetikliyor). Ama **OmniVoice sentetik İngilizcesi GigaSpeech'e uymuyor** → offline test geçersiz. **Karar GERÇEK insan sesiyle canlı testte verilecek.**
- **SIRADAKI ADIM — kullanıcı kendi sesiyle çalıştırsın:**
  `cd /Users/drascom/work/candan-lite/worker && .venv/bin/python /private/tmp/claude-501/-Users-drascom-work-candan-lite/1a2a38c8-7ee1-44d3-8585-79fe4db4f8cf/scratchpad/kws/live_mic_kws.py`
  (level metre + kontrol kelimeleri: önce **"forever"** de → tetiklerse pipeline sağlam; sonra "Hey Jackie"/"Jackie". Gerekirse `--threshold 0.10 --score 4.0 --device 3`.)
  - ⚠️ **ÖN KOŞUL — macOS mikrofon izni:** ilk denemede `level: 0.000` çıktı (hem kullanıcı terminali hem Claude süreci RMS≈0 = sessizlik). Sorun KWS değil, **terminal uygulamasına mikrofon izni verilmemiş**. Fix: macOS Ayarlar→Gizlilik→Mikrofon→terminal uygulamasını (Terminal/iTerm/VSCode) AÇ → terminali kapat-aç → tekrar dene. `level:` konuşunca dolmalı; DOLMADAN KWS testi anlamsız. (Tarayıcı mikrofonu ayrı izinle zaten çalışıyor.)
  NOT: scratchpad session'a özel — yeni session'da scratchpad yolu değişebilir; araç `scratchpad/kws/` altında, gerekirse yeniden üret. KWS modeli: `sherpa-onnx-kws-zipformer-gigaspeech-3.3M`.
- **Karar:** Jackie gerçek sesle tutarlı yakalanırsa → KWS'i `wake_stt` yerine/yanına on-device wake olarak entegre et, wake word="Jackie". Tutmazsa Whisper-"candan"da kal.

## Memory (index): delegation=Agent tool; kaldırmadan-önce-sor. Kişisel hafıza dosyaları `memory/` (gitignored, local).

---
Yeni session bununla devam eder. Özet: ağır **Hermes+plugin** yığını donduruldu;
Candan'ın hafif yeniden yapımı `candan-lite` başlatıldı. Beyin = **pi.dev agent**
(OpenAI-uyumlu `/chat`), ses = **livekit-agents** üstünde ince worker.

## Kararlar (kilitli)
- **Beyin:** pi.dev agent, **OpenAI-uyumlu `/v1`** → worker'da `openai.LLM(base_url=…)`, sıfır glue.
- **Beyin konumu:** şimdilik **local PC**; olgunlaşınca remote (tek değişen `PIDEV_BASE_URL`).
- **LiveKit server:** oracle-stage'de **kalıcı** (:7880). **STT/TTS:** .25 GPU (Whisper :10300 wyoming, OmniVoice :8808) — mevcut.
- **Client:** `web/` (LiveKit agent-starter-react). Ses+metin aynı worker→aynı beyin.
- **Ses worker'ı:** livekit-agents `AgentSession` — VAD/turn/barge-in framework'ten; sadece STT+TTS custom plugin. (Eski adapter.py ham rtc kullanıyordu, 137KB; onu kullanmıyoruz.)

## Yapıldı ✅
1. **oracle-stage Hermes durduruldu + disable:** `hermes-serve`, `hermes-gateway`, `hermes-webui` (boot'ta gelmez).
   - `livekit-server.service` **AÇIK bırakıldı** (bağımsız systemd; config: `/home/ubuntu/.hermes/mate_voice/livekit/livekit.yaml`; `use_external_ip:true`).
   - GERİ DÖNÜŞ: `ssh oracle-stage 'sudo systemctl enable --now hermes-serve hermes-gateway hermes-webui'`
2. **web/** — `livekit-web` komple taşındı (kendi `.git`'i içinde). **Hermes'ten koparıldı → direct-mint token:**
   - `.env.local`: `MATE_DISCOVERY_ENDPOINT` KALDIRILDI, `MATE_LIVEKIT_ROOM=candan-lite-dev` eklendi.
   - `pnpm build` **OK**. (Runtime testi kullanıcı yapar.)
3. **worker/** skeleton: `agent.py` (AgentSession + `WhisperWyomingSTT`/`OmniVoiceTTS` custom plugin **iskele**, henüz port edilmedi), `requirements.txt`, `.env.example`.
4. `README.md` + bu `HANDOFF.md`.

## 🎉 HAFIZA FAZ B ÇALIŞIYOR — Pİ-NATIVE (2026-07-10)
**KARAR: kendi memory sistemimiz Pi-native; Hermes YOK.** `pi-hermes-memory` extension'ı KALDIRILDI (`pi remove`) — Hermes-türeviydi + bizim sistemle paralel koşup karışıklık yapıyordu.
- **Kendi lokal extension'ımız:** `pi/extensions/mem/index.ts` — `memory_add` + `memory_search` custom tool'ları (pi `registerTool` API). Worker pi'yı `-e pi/extensions/mem/index.ts` ile **sadece kendi süreçlerinde** yükler (global DEĞİL; senin kişisel pi'na dokunmaz).
- **Kullanıcı-başı:** `MEM_USER` env → `memory/users/<user>/` + rol (adult/child/guest, `policy.json`). Depolama = repo'daki markdown dosyalar (otoriter) + `node:sqlite` FTS5 (`memory/.index/mem.db`, Türkçe diacritics-duyarsız). `memory/` gitignored + nested audit-git.
- **Session-finalize:** `ctx.add_shutdown_callback` → `PiBrain.finalize()` → pi oturum kapanınca 3-5 kalıcı not çıkarır (30sn best-effort). Canlı test: köpek isimleri kendiliğinden kaydedildi.
- **Canlı doğrulandı:** memory_add(private+family), memory_search (FTS getirme), correction/update, izolasyon, finalize. Hepsi sesli oturumda.
- **AÇIK KONU (D fazı):** pi rpc'de ara sıra `WebSocket closed 1000` + tek turda ~40sn gecikme. Ses için yüksek — kararlılık/gecikme incelenecek.
- Eski plan dokümanları (`docs/hafiza-v2-plan.md`, `hafiza-implementasyon-rehberi.md`) Hermes-çerçeveliydi; artık geçersiz/tarihsel — gerçek = bu bölüm + `pi/extensions/mem/`.

## 🎉 FAZ 3 + HAFIZA-A ÇALIŞIYOR (2026-07-10)
- **Speaker-ID (campplus, sherpa-onnx):** oto-enroll konuşarak (bilinmeyen ses → "adını söyler misin?" → onay → kaydet). Tanıma tutarlı.
- **KRİTİK DERS — enroll = recognize AYNI ses yolu:** CLI `--record` (Mac mic, ham) ile enroll edilince tanıma skorları düşük/oynak (0.1-0.6) çünkü oda sesi tarayıcı-WebRTC işlemeli. **Oda sesinden oto-enroll** (SpeakerState.last_embedding) → skorlar 0.45-0.67, tutarlı. Enrollment'ı HEP oda sesinden yap.
- **Sticky speaker-ID:** `SPEAKER_VAD_RMS`(0.01) sessizlik-kapısı + `SPEAKER_STICKY_MISSES`(5) → kısa sessizlik/dip'lerde konuşmacı korunur, pi swap thrash yok.
- **Hafıza Faz A:** boot enjeksiyonu (persona+profile+family, role-gated) + `MEM_USER` env + memory-skill v0. Test edildi: özel not→`notes/`, aile→`family.md`(açık istekle), profil kendini zenginleştirdi, izolasyon OK. `memory/` gitignored (public repo'ya girmez) + nested audit-git.
- **Kullanıcı:** `baba` (adult, policy.json). persona `pi/personas/baba.md`. Model pin `gpt-5.6-terra`.
- **SIRADAKİ:** Hafıza Faz B (`tools/mem` CLI + SQLite FTS5 arama + session-finalize + git-audit). Detay: `docs/hafiza-implementasyon-rehberi.md`.

## 🎉 FAZ 2 ÇALIŞIYOR (2026-07-10): sesli uçtan-uca
web → STT (Whisper wyoming) → pi beyni (Candan, warm rpc) → TTS (OmniVoice) → ses. Test edildi, normal hız.
- **Repo:** github.com/drascom/candan-lite (public, main). web/ absorbe (fork silindi).
- **Çalıştırma:** worker `cd worker && .venv/bin/python agent.py dev`; web `cd web && pnpm dev` (:3000).
- **OmniVoice gerçek çıktı = 24kHz** (audio_start bildiriyor; referans "48kHz" YANLIŞti → 2× hız bug'ıydı, düzeltildi).
- **GOTCHA — worker restart:** worker'ı yeniden başlatınca mevcut odaya OTOMATİK dispatch OLMAZ (sadece yeni odaya). Tarayıcı sekmesini TAM KAPAT → tekrar bağlan (oda sıfırdan oluşsun).
- Not: cloud turn-detector 401 → yerel mini modele düşüyor (zararsız; Faz 3'te düzeltilir).

## KARAR GÜNCELLEME (2026-07-10): beyin = pi CLI, warm RPC
`PIDEV_BASE_URL` + `openai.LLM(base_url=…)` planı **İPTAL**. Beyin `pi` CLI (Codex sub),
worker onu **kalıcı `--mode rpc` subprocess** olarak sürer. Detay: `docs/pi-brain-design.md`.
- Warm per-user süreç → cold-start oturum başına 1 kez (tur başına DEĞİL). Pre-warm @join.
- Local pi config: `candan-lite/pi/` (personas/skills/settings) + `sessions/<user>/` memory.
- Kimlik = speaker-ID (`hermes-livekit/voice/speaker*.py` port). Warm-havuz şimdilik YOK.
- Referans client: `~/work/candan assistant/mate-mac/` (macOS).

## SIRADAKİ (revize öncelik)
1. ✅ **`pi/` local scaffold** — AGENTS.md, personas/candan.md, skills/, settings.json.
2. ✅ **`worker/pi_brain.py`** — warm `pi --mode rpc` LLM adaptörü. Protokol runtime doğrulandı:
   `message_update.assistantMessageEvent.text_delta` stream; `agent_settled` tur bitişi; abort@barge-in.
   ⚠️ **Model:** global varsayılan `gpt-5.6-luna` Codex'te BOZUK ("Model not found"). Pin: `PI_MODEL=openai-codex/gpt-5.6-terra` (worker/.env). Alternatif: gpt-5.4/5.5/5.6-sol.
   Test: `python worker/pi_brain.py smoke` (get_state) ve `... prompt "merhaba"` (metin) → PASS.
3. **Faz 2 test:** metinle uçtan uca (web chat → worker → pi rpc → cevap) — worker'ı gerçek LiveKit odasında çalıştır (livekit-agents kurulumu + `python agent.py dev`). Bu RUNTIME testi KULLANICI yapar.
4. **STT/TTS port:**
   - `worker/whisper_stt.py` ← `../candan assistant/hermes-livekit/adapter.py` ~**1399-1543**.
   - `worker/omnivoice_tts.py` ← `hermes-livekit/voice/tts.py` + OmniVoice etiket (adapter ~207-221).
5. **Speaker-ID port:** `worker/speaker_id.py` ← `voice/speaker.py` + `speaker_store.py` → persona seçimi.
6. **Deploy:** worker+pi remote, systemd/compose.

## Anahtar bilgiler
- **LiveKit URL:** `wss://mate-livekit.drascom.uk` (→ oracle-stage :7880)
- **LiveKit key/secret:** `web/.env.local` ve `worker/.env` içinde (gitignored — public repo'ya girmez; web ve worker AYNI değeri kullanır)
- **Oda (dev):** `candan-lite-dev` (eski Hermes worker'ıyla çakışmasın diye ayrı)
- **STT/TTS:** `192.168.0.25:10300` (Whisper wyoming) / `192.168.0.25:8808` (OmniVoice)
- **SSH:** `ssh oracle-stage` (~/.ssh/config; passwordless sudo var)
- **Kaynak repo (referans, DOKUNMA):** `~/work/candan assistant/hermes-livekit/` (donduruldu, silinmedi)

## Kurallar (memory'den)
- `Mate-IOS/`'a dokunma. Commit'e AI imzası koyma. Git akışı: sadece main.
- Claude görsel/runtime test YAPMAZ — sadece build doğrular; app'i kullanıcı açar.
- Sunucuda git-takipli dosyaları elle düzenleme (plugin/monorepo); .25 kutusu serbest.

Memory: `candan-lite-pivot-plan.md` (index'te ⭐⭐ EN GÜNCEL).
