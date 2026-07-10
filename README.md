# candan-lite

Candan asistanının **hafif** yeniden yapımı. Ağır Hermes+plugin yığını donduruldu;
beyin artık **pi.dev agent** (OpenAI-uyumlu `/chat`), ses boru hattı **livekit-agents**
framework'ü üstünde ince bir worker.

## Mimari
```
web/ (Next.js, LiveKit agent-starter) ──ses+metin──► LiveKit (oracle-stage :7880) ◄── worker/
                                                                                        ├─ STT → Whisper  (.25:10300, wyoming)
                                                                                        ├─ TTS → OmniVoice(.25:8808)
                                                                                        ├─ VAD/turn/barge-in  → framework
                                                                                        └─ LLM → pi.dev (OpenAI /v1)  ◄── brain/
```

## Klasörler
- `web/`   — istemci (livekit-web'den taşındı, direct-mint token, oda `candan-lite-dev`). HAZIR.
- `worker/`— livekit-agents worker (AgentSession). STT/TTS custom plugin. YAPILIYOR.
- `brain/` — pi.dev agent (OpenAI /chat) + tool + hafıza. şimdilik local PC, sonra remote.
- `deploy/`— systemd/compose (cutover'da).

## Durum (2026-07-10)
- [x] oracle-stage Hermes durduruldu+disable (livekit-server AÇIK kaldı)
- [x] livekit-web → `web/`, Hermes'ten koparıldı (direct-mint)
- [ ] worker STT/TTS port (adapter.py'dan: WhisperSession wyoming + OmniVoice bridge)
- [ ] pi.dev brain bağla (metinle uçtan uca)
- [ ] tool + hafıza
- [ ] deploy / cutover

## Faz kaynak referansları (candan/hermes-livekit'ten taşınacak)
- Wyoming STT: `hermes-livekit/adapter.py` ~satır 1399-1543 (`WhisperSession`, wyoming Event)
- OmniVoice TTS: `hermes-livekit/voice/tts.py` + adapter OmniVoice etiket mantığı (~satır 207-221)
- turn-detect: `hermes-livekit/voice/turn_detector.py`
- speaker-ID: `hermes-livekit/voice/speaker*.py`
- LiveKit config (use_external_ip:true): `hermes-livekit/setup_livekit.py`
