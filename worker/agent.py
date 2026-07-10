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

# worker/.env (gitignored) — cwd'den bağımsız, dosya konumuna göre yükle.
load_dotenv(Path(__file__).resolve().parent / ".env")

STT_HOST = os.environ.get("STT_HOST", "192.168.0.25")
STT_PORT = int(os.environ.get("STT_PORT", "10300"))
TTS_HOST = os.environ.get("TTS_HOST", "192.168.0.25")
TTS_PORT = int(os.environ.get("TTS_PORT", "8808"))
LANG = os.environ.get("MATE_LANGUAGE", "tr")

# Beyin: pi CLI, warm `--mode rpc` alt-süreci (HTTP /v1 YOK). Persona env ile seçilir.
PI_PERSONA = os.environ.get("PI_DEFAULT_PERSONA", "candan")


async def entrypoint(ctx: JobContext):
    await ctx.connect()

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=WhisperWyomingSTT(host=STT_HOST, port=STT_PORT, language=LANG),
        llm=PiBrain(persona=PI_PERSONA),
        tts=OmniVoiceTTS(host=TTS_HOST, port=TTS_PORT),
        # turn_detection: framework multilingual model (Faz 3) — şimdilik VAD tabanlı
    )

    await session.start(
        agent=Agent(instructions="Sen Candan'sın. Türkçe, kısa ve yardımcı konuş."),
        room=ctx.room,
    )
    await session.generate_reply(instructions="Kullanıcıyı kısaca selamla.")


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
