"""candan-lite voice worker — livekit-agents AgentSession.

Ağır adapter.py'ın yerine ince worker: VAD/turn-detect/barge-in framework'ten;
sadece STT (Whisper wyoming) ve TTS (OmniVoice) custom plugin. LLM = pi.dev (OpenAI /v1).

Çalıştırma (dev): python agent.py dev
Oda: MATE_LIVEKIT_ROOM (candan-lite-dev)
"""
import os
from dotenv import load_dotenv
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import openai, silero

from whisper_stt import WhisperWyomingSTT   # TODO: adapter.py ~1399-1543'ten port
from omnivoice_tts import OmniVoiceTTS       # TODO: voice/tts.py'den port

load_dotenv(".env.local")

STT_HOST = os.environ.get("STT_HOST", "192.168.0.25")
STT_PORT = int(os.environ.get("STT_PORT", "10300"))
TTS_HOST = os.environ.get("TTS_HOST", "192.168.0.25")
TTS_PORT = int(os.environ.get("TTS_PORT", "8808"))
LANG = os.environ.get("MATE_LANGUAGE", "tr")

# Beyin: pi.dev agent, OpenAI-uyumlu. Şimdilik local PC; sonra remote (sadece base_url değişir).
PIDEV_BASE_URL = os.environ.get("PIDEV_BASE_URL", "http://localhost:8100/v1")
PIDEV_MODEL = os.environ.get("PIDEV_MODEL", "candan")


async def entrypoint(ctx: JobContext):
    await ctx.connect()

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=WhisperWyomingSTT(host=STT_HOST, port=STT_PORT, language=LANG),
        llm=openai.LLM(base_url=PIDEV_BASE_URL, model=PIDEV_MODEL, api_key="local"),
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
