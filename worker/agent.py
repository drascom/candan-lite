"""candan-lite voice worker — livekit-agents AgentSession.

Ağır adapter.py'ın yerine ince worker: VAD/turn-detect/barge-in framework'ten;
sadece STT (Whisper wyoming) ve TTS (OmniVoice) custom plugin.
Beyin = pi CLI, warm `--mode rpc` alt-süreci (worker/pi_brain.py, docs/pi-brain-design.md).

Çalıştırma (dev): python agent.py dev
Oda: MATE_LIVEKIT_ROOM (candan-lite-dev)
"""
import os
from pathlib import Path
from dotenv import load_dotenv
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import silero

from pi_brain import PiBrain                 # warm pi --mode rpc beyni
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

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=WhisperWyomingSTT(host=STT_HOST, port=STT_PORT, language=LANG),
        # Faz 3.1: sesli oto-enrollment — bilinmeyen ses gelince PiBrain isim sorar,
        # onaylanınca sp/store ile kaydeder (speaker_state None ise devre dışı).
        llm=PiBrain(
            persona=PI_PERSONA,
            speaker_state=speaker_state,
            speaker_id=sp if speaker_state is not None else None,
            speaker_store=store if speaker_state is not None else None,
        ),
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
    await session.generate_reply(instructions="Kullanıcıyı kısaca selamla.")


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
