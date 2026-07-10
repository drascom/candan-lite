# pi-brain tasarımı — warm per-user RPC (2026-07-10)

Karar: beyin = **pi CLI** (`@earendil-works/pi-coding-agent`, Codex subscription arka planda).
HTTP `/v1` sunucusu YOK → `PIDEV_BASE_URL`/`openai.LLM(base_url=…)` planı **iptal**.
Worker pi'yı **kalıcı `--mode rpc` subprocess** olarak sürer (stdin/stdout JSON-lines).

## Neden RPC (warm)
pi `--mode rpc` = "Headless operation, JSON stdin/stdout, embedding the agent in other
applications". `runRpcMode` → `Promise<never>` (kill'lenene dek yaşar).
Komut yüzeyi (ses için birebir):
- `{"type":"prompt","message":…}` → tur gönder, assistant event'leri stream (→ TTS)
- `{"type":"abort"}` → **barge-in** (kullanıcı kesince)
- `{"type":"steer"|"follow_up",…}` → tur-içi / agent-initiated enjeksiyon
- `{"type":"get_last_assistant_text"}`, `get_state`, `compact`, `set_model` …

## Cooldown çözümü
Cold-start (süreç + skill/persona yükleme + ilk model handshake) = **oturum başına bir kez**,
tur başına DEĞİL. Süreç tüm bağlantı boyunca warm kalır.
- **Pre-warm:** participant katılınca pi süreci başlat; karşılama konuşurken ısınır.
- Warm-havuz (N boşta süreç) şimdilik YOK — gecikme sorun olursa eklenir.

## Always-on / çift-yönlü
Client kapanana dek bağlı kalır. LiveKit oturumu + pi rpc süreci participant başına tüm
oturum boyunca ayakta. Agent proaktif konuşabilir: `session.say(...)` veya rpc `follow_up`.

## Kişi-başı özelleştirme (local, repo içinde)
```
candan-lite/
  pi/
    AGENTS.md            # ortak taban (proje bağlamı, temel davranış)
    personas/<user>.md   # kişiye özel kişilik overlay
    skills/              # ortak + kişiye özel skill dosyaları
    settings.json        # local pi ayarı (extension'lar)
  sessions/<user>/       # kişiye özel memory (--session-id)
```
Kullanıcı başına başlatma:
```
pi --mode rpc --approve \
   --append-system-prompt pi/personas/<user>.md \
   --skill pi/skills/ \
   --session-dir sessions --session-id <user>
```

## Referans clientlar (DOKUNMA, örnek al)
- `~/work/candan assistant/mate-mac/` — macOS client (SpeakerReceiver.swift vb. desenler).
- `~/work/candan assistant/Mate-IOS/` — iOS (DOKUNMA kuralı bunda kesin).

## Kullanıcı kimliği = speaker-ID (mevcut koddan port)
Kaynak (referans, DOKUNMA): `~/work/candan assistant/hermes-livekit/voice/`
- `speaker.py` + `speaker_store.py` → sesten kimlik → `<user>` → persona seçimi.
- Port hedefi: `worker/speaker_id.py`. İlk sürümde tek persona ('candan') ile de çalışır.

## Worker bileşenleri
- `worker/pi_brain.py` — livekit-agents LLM node; kalıcı `pi --mode rpc` subprocess sürer
  (spawn@session-start, prompt→stream, abort@barge-in, follow_up@proactive).
- `worker/whisper_stt.py` ← adapter.py ~1399-1543 (WhisperSession, wyoming Event).
- `worker/omnivoice_tts.py` ← voice/tts.py + OmniVoice etiket mantığı (adapter ~207-221).
- `worker/speaker_id.py` ← voice/speaker.py + speaker_store.py.
- `worker/agent.py` — AgentSession'ı bu 4 plugin'le bağlar.

## Revize yol haritası
1. `pi/` local scaffold (AGENTS.md, personas/candan.md, skills/, settings.json).
2. `worker/pi_brain.py` — warm rpc wrapper (önce tek persona, metinle).
3. Metinle uçtan uca test (web chat → worker → pi rpc → cevap).
4. STT/TTS port → sesli uçtan uca.
5. Speaker-ID port → persona seçimi + kişiye özel memory.
6. Deploy (worker+pi remote; systemd/compose).
