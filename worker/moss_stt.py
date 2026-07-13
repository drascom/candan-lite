"""moss_stt — livekit-agents STT plugin over MOSS-Transcribe-Diarize (HTTP).

whisper_stt.py'ın ALTERNATİFİ (env STT_BACKEND=moss ile seçilir). Aynı sözleşme:
streaming=False → AgentSession bunu silero VAD ile StreamAdapter'a sarar; her
utterance için _recognize_impl bir kez çağrılır.

Akış: AudioBuffer → tek s16le WAV blob → HTTP POST {url}/transcribe?language=<lang>
→ {"segments":[{"speaker","start","end","text"}], "raw": "..."}. Transcript =
segment text'lerinin birleşimi. Konuşmacı/segment detayı SpeechData'ya sığmaz →
şimdilik debug'a yazılır + `last_segments` alanında tutulur (isim eşleme AYRI faz).

Dil = MATE_LANGUAGE (agent.py'dan geçer), whisper_stt ile aynı kaynak.
HTTP = aiohttp (livekit-agents zaten bağımlı; yeni paket yok).
"""
from __future__ import annotations

import io
import logging
import wave

import aiohttp
from livekit import rtc
from livekit.agents import APIConnectionError, APITimeoutError, stt
from livekit.agents.types import (
    DEFAULT_API_CONNECT_OPTIONS,
    NOT_GIVEN,
    APIConnectOptions,
    NotGivenOr,
)
from livekit.agents.utils import AudioBuffer

from log_utils import DedupeFilter

logger = logging.getLogger("moss_stt")
logger.addFilter(DedupeFilter())

STT_WIDTH = 2  # bytes/sample: s16le (whisper_stt ile aynı)


def _pcm_to_wav(payload: bytes, rate: int, channels: int) -> bytes:
    """s16le PCM → in-memory WAV (stdlib wave; ek bağımlılık yok)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(max(1, channels))
        wf.setsampwidth(STT_WIDTH)
        wf.setframerate(rate)
        wf.writeframes(payload)
    return buf.getvalue()


class MossSTT(stt.STT):
    """MOSS-Transcribe-Diarize STT plugin. WhisperWyomingSTT yerine geçer."""

    def __init__(self, *, url: str, language: str = "", timeout: float = 30.0):
        super().__init__(
            capabilities=stt.STTCapabilities(streaming=False, interim_results=False)
        )
        self._url = url.rstrip("/")
        self._language = language
        self._timeout = timeout
        # Son transcribe'ın segment listesi (speaker/start/end/text). SpeechData'ya
        # sığmayan konuşmacı detayı burada erişilebilir kalır (gelecek: isim eşleme).
        self.last_segments: list[dict] = []

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> stt.SpeechEvent:
        lang = language if isinstance(language, str) and language else self._language

        # StreamAdapter'ın verdiği frame'leri tek blob'a birleştir (whisper_stt ile aynı).
        frame = rtc.combine_audio_frames(buffer)
        payload = bytes(frame.data)
        rate = frame.sample_rate
        channels = frame.num_channels

        text = ""
        self.last_segments = []
        if payload:
            wav = _pcm_to_wav(payload, rate, channels)
            endpoint = f"{self._url}/transcribe"
            params = {"language": lang} if lang else {}
            try:
                timeout = aiohttp.ClientTimeout(total=self._timeout)
                async with (
                    aiohttp.ClientSession(timeout=timeout) as sess,
                    sess.post(
                        endpoint,
                        params=params,
                        data=wav,
                        headers={"Content-Type": "audio/wav"},
                    ) as resp,
                ):
                    resp.raise_for_status()
                    result = await resp.json()
            except aiohttp.ServerTimeoutError as e:
                logger.warning("moss_stt: MOSS timeout (%s): %s", endpoint, e)
                raise APITimeoutError() from e
            except (aiohttp.ClientError, OSError) as e:
                logger.warning("moss_stt: MOSS erişilemiyor (%s): %s", endpoint, e)
                raise APIConnectionError(
                    f"MOSS STT erişilemiyor ({endpoint}): {e}"
                ) from e

            segments = (result or {}).get("segments") or []
            self.last_segments = segments
            text = " ".join(
                (s.get("text") or "").strip() for s in segments
            ).strip()
            # Konuşmacı/segment detayını (şimdilik) debug'a düşür; ileride isim eşleme.
            if segments:
                logger.debug(
                    "moss_stt: %d segment, konuşmacılar=%s",
                    len(segments),
                    sorted({s.get("speaker") for s in segments if s.get("speaker")}),
                )

        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[stt.SpeechData(language=lang or "", text=text)],
        )
