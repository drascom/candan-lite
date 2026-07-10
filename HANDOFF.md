# candan-lite — HANDOFF (2026-07-10)

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
