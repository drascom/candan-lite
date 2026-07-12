"""whisper_stt — livekit-agents STT plugin over Wyoming (faster-whisper).

adapter.py ~1399-1543 + voice/services.py WhisperSession mantığının portu.
Wyoming Event protokolü: transcribe → audio-start → audio-chunk* → audio-stop
→ transcript. Whisper streaming DEĞİL (utterance-batch) → STTCapabilities
streaming=False; AgentSession bunu VAD (silero) ile StreamAdapter'a sarar.

Dil = MATE_LANGUAGE (WhisperWyomingSTT(language=...)).
"""
from __future__ import annotations

import logging
from typing import Optional

from livekit import rtc
from livekit.agents import stt
from livekit.agents.types import (
    DEFAULT_API_CONNECT_OPTIONS,
    NOT_GIVEN,
    APIConnectOptions,
    NotGivenOr,
)
from livekit.agents.utils import AudioBuffer

from wyoming.event import Event, async_read_event, async_write_event
import asyncio

from log_utils import DedupeFilter

logger = logging.getLogger("whisper_stt")
logger.addFilter(DedupeFilter())

STT_WIDTH = 2  # bytes/sample: s16le


class _WhisperSession:
    """Tek utterance: bağlantı aç, chunk'ları akıt, transcript al.

    voice/services.py WhisperSession'ın birebir portu (harici repoya bağımlılık yok).
    """

    def __init__(self, host: str, port: int, language: str = ""):
        self.host = host
        self.port = port
        self.language = language
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None

    async def start(self, rate: int, width: int, channels: int) -> None:
        self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
        data = {"language": self.language} if self.language else {}
        await async_write_event(Event(type="transcribe", data=data), self._writer)
        await async_write_event(
            Event(
                type="audio-start",
                data={"rate": rate, "width": width, "channels": channels},
            ),
            self._writer,
        )

    async def feed(self, payload: bytes, rate: int, width: int, channels: int) -> None:
        await async_write_event(
            Event(
                type="audio-chunk",
                data={"rate": rate, "width": width, "channels": channels},
                payload=payload,
            ),
            self._writer,
        )

    async def finish(self, timeout: float = 30.0) -> str:
        await async_write_event(Event(type="audio-stop"), self._writer)
        try:
            while True:
                event = await asyncio.wait_for(async_read_event(self._reader), timeout)
                if event is None:
                    return ""
                if event.type == "transcript":
                    return (event.data or {}).get("text", "") or ""
        finally:
            if self._writer:
                self._writer.close()

    async def abort(self) -> None:
        if self._writer:
            self._writer.close()


class WhisperWyomingSTT(stt.STT):
    """Wyoming (faster-whisper) STT plugin. openai/deepgram STT yerine geçer."""

    def __init__(self, *, host: str, port: int, language: str = ""):
        super().__init__(
            capabilities=stt.STTCapabilities(streaming=False, interim_results=False)
        )
        self._host = host
        self._port = port
        self._language = language

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> stt.SpeechEvent:
        lang = language if isinstance(language, str) and language else self._language

        # StreamAdapter'ın verdiği frame'leri tek blob'a birleştir.
        frame = rtc.combine_audio_frames(buffer)
        payload = bytes(frame.data)
        rate = frame.sample_rate
        channels = frame.num_channels

        session = _WhisperSession(self._host, self._port, lang)
        text = ""
        try:
            await session.start(rate=rate, width=STT_WIDTH, channels=channels)
            if payload:
                await session.feed(payload, rate, STT_WIDTH, channels)
            text = await session.finish()
        except (ConnectionError, OSError) as e:
            logger.warning("whisper_stt: STT erişilemiyor (%s:%s): %s",
                           self._host, self._port, e)
            await session.abort()

        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[stt.SpeechData(language=lang or "", text=text)],
        )
