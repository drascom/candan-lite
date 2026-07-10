"""candan-lite voice worker — livekit-agents AgentSession.

Ağır adapter.py'ın yerine ince worker: VAD/turn-detect/barge-in framework'ten;
sadece STT (Whisper wyoming) ve TTS (OmniVoice) custom plugin.
Beyin = pi CLI, warm `--mode rpc` alt-süreci (worker/pi_brain.py, docs/pi-brain-design.md).

Çalıştırma (dev): python agent.py dev
Oda: MATE_LIVEKIT_ROOM (candan-lite-dev)
"""
import asyncio
import logging
import os
from pathlib import Path
from dotenv import load_dotenv
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import silero

from pi_brain import PiBrain, WAKE_ENABLED   # warm pi --mode rpc beyni + wake gate
from whisper_stt import WhisperWyomingSTT    # Wyoming (faster-whisper) STT plugin
from omnivoice_tts import OmniVoiceTTS       # OmniVoice WS TTS plugin
from speaker_id import build_speaker_id, SpeakerStore  # Faz 3: speaker-ID (opsiyonel)
from speaker_tap import SpeakerState, SpeakerTap       # paralel speaker tap

# worker/.env (gitignored) — cwd'den bağımsız, dosya konumuna göre yükle.
load_dotenv(Path(__file__).resolve().parent / ".env")

STT_HOST = os.environ.get("STT_HOST", "192.168.0.25")
STT_PORT = int(os.environ.get("STT_PORT", "10300"))
TTS_HOST = os.environ.get("TTS_HOST", "192.168.0.25")
TTS_PORT = int(os.environ.get("TTS_PORT", "8808"))
LANG = os.environ.get("MATE_LANGUAGE", "tr")

# Beyin: pi CLI, warm `--mode rpc` alt-süreci (HTTP /v1 YOK). Persona env ile seçilir.
PI_PERSONA = os.environ.get("PI_DEFAULT_PERSONA", "candan")
SPEAKER_MIN_S = float(os.environ.get("SPEAKER_MIN_SECONDS", "1.0") or 1.0)
# Yapışkanlık: art arda kaç güvensiz pencereden sonra current unknown'a düşsün.
SPEAKER_STICKY_MISSES = int(float(os.environ.get("SPEAKER_STICKY_MISSES", "5") or 5))


async def entrypoint(ctx: JobContext):
    await ctx.connect()

    # --- Faz 3: speaker-ID (opsiyonel, additive) ---
    # SPEAKER_ID_ENABLED kapalı / model yok / sherpa-onnx yok ise sp=None kalır ve
    # davranış Faz 2 ile AYNI olur (tek persona candan, tek warm süreç).
    sp = build_speaker_id()
    speaker_state: SpeakerState | None = None
    tap: SpeakerTap | None = None
    store: SpeakerStore | None = None
    if sp is not None:
        try:
            store = SpeakerStore()
            sp.reload(await store.all_speaker_embeddings())  # enrolled kişileri yükle
            speaker_state = SpeakerState(sticky_misses=SPEAKER_STICKY_MISSES)
            tap = SpeakerTap(sp, speaker_state, min_seconds=SPEAKER_MIN_S)
        except Exception as e:  # noqa: BLE001 — speaker-ID hiç kurulamazsa Faz 2'ye düş
            import logging
            logging.getLogger("worker.agent").warning("speaker-ID kurulamadı: %r", e)
            speaker_state = None
            tap = None
            store = None

    # Beyin (warm pi). Hafıza Faz B: oturum kapanışında finalize() ile kalıcı
    # maddeleri kaydettir (best-effort, kapanışı bloklamaz).
    brain = PiBrain(
        persona=PI_PERSONA,
        speaker_state=speaker_state,
        speaker_id=sp if speaker_state is not None else None,
        speaker_store=store if speaker_state is not None else None,
    )

    async def _finalize_memory() -> None:
        try:
            await brain.finalize()
        except Exception:  # noqa: BLE001 — kapanış hiçbir koşulda bloklanmaz
            pass

    ctx.add_shutdown_callback(_finalize_memory)

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=WhisperWyomingSTT(host=STT_HOST, port=STT_PORT, language=LANG),
        # Faz 3.1: sesli oto-enrollment — bilinmeyen ses gelince PiBrain isim sorar,
        # onaylanınca sp/store ile kaydeder (speaker_state None ise devre dışı).
        llm=brain,
        tts=OmniVoiceTTS(host=TTS_HOST, port=TTS_PORT),
        # turn_detection: framework multilingual model (Faz 3) — şimdilik VAD tabanlı
    )

    await session.start(
        agent=Agent(instructions="Sen Candan'sın. Türkçe, kısa ve yardımcı konuş."),
        room=ctx.room,
    )
    # STT'den BAĞIMSIZ paralel speaker tap'i room'a bağla (mic track → embed/identify).
    if tap is not None:
        tap.attach(ctx.room)

    # Wake durumunu web'e sinyalle: local participant attribute `candan.awake`.
    # NOT: transcript'i worker'da toggle ETME — session.output.set_transcription_enabled
    # TranscriptSynchronizer'ı detach edip agent metnini bozuyor. Uyurken kullanıcı
    # metnini gizleme WEB tarafında `candan.awake` ile yapılır.
    def _apply_wake_state(awake: bool) -> None:
        """Uyku/uyanık durumunu web'e yayınla (attribute). Sync bağlamdan güvenli."""
        val = "true" if awake else "false"
        try:
            asyncio.create_task(
                ctx.room.local_participant.set_attributes({"candan.awake": val}))
        except RuntimeError:  # çalışan loop yok → atla
            pass

    if WAKE_ENABLED:
        # Geçişleri web'e bağla; başlangıç: UYKUDA → attribute "false" + transcript kapalı.
        brain.set_wake_change(_apply_wake_state)
        _apply_wake_state(False)

        # Çanı ERKEN çal: transcript kesinleşir kesinleşmez (PiBrain turu işlenmeden ÖNCE)
        # wake word varsa brain.wake_now() → on_change → candan.awake="true" → çan.
        # Böylece çan ~0.3-0.5s daha erken çalar. Idempotent; "candan" tek başınaysa
        # PiStream tarafı zaten SILENT döner (sözlü yanıt yok).
        @session.on("user_input_transcribed")
        def _on_transcript(ev) -> None:
            if not getattr(ev, "is_final", False):
                return
            try:
                brain.wake_now(getattr(ev, "transcript", "") or "")
            except Exception:  # noqa: BLE001 — sinyal hatası akışı bozmasın
                pass
    else:
        # Gate yok: hep uyanık → attribute "true" + transcript açık (eski davranış) +
        # katılınca kısaca selamla.
        _apply_wake_state(True)
        await session.generate_reply(instructions="Kullanıcıyı kısaca selamla.")


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
