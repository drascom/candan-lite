"""omnivoice_tts — livekit-agents TTS plugin over OmniVoice (VoxCPM WS bridge).

voice/tts.py `_vox_stream` + OmniVoice etiket mantığının (adapter.py ~207-221)
portu. OmniVoice WS Bridge v0: `ws://TTS_HOST:TTS_PORT/ws`
  gönder: {"type":"speak","id":...,"text":...,"voice":...}
  al:     binary = pcm f32le chunk;  JSON audio_start{sample_rate,channels} /
          audio_end / error{message}

Etiketler: LLM metne [laughter]/[sigh]/… gömer; OmniVoice bunları seslendirir.
Bu yüzden metin OmniVoice'e HAM (etiketli) gider — strip yalnızca gösterim/
transkript içindir (burada değil). `_strip_omni_tags` referans için tutuldu.

f32le → s16le'ye çevrilip AudioEmitter'a (mime audio/pcm) verilir.
"""
from __future__ import annotations

import json
import logging
import re
from array import array
from typing import Optional

import websockets

from livekit.agents import tts, utils
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions

logger = logging.getLogger("omnivoice_tts")

# OmniVoice non-verbal etiketleri (adapter.py ~207-221). TTS metnine gömülür ve
# OmniVoice seslendirir; sadece transkript gösteriminde strip edilir.
_OMNI_TAG_RE = re.compile(
    r"\s*\[(?:laughter|sigh|confirmation-en"
    r"|question-(?:en|ah|oh|ei|yi)"
    r"|surprise-(?:ah|oh|wa|yo)"
    r"|dissatisfaction-hnn)\]",
    re.IGNORECASE,
)

DEFAULT_SAMPLE_RATE = 48000
NUM_CHANNELS = 1


def _strip_omni_tags(text: str) -> str:
    """OmniVoice [etiket]'lerini çıkar (gösterim için; TTS metni ham kalır)."""
    if not text or "[" not in text:
        return text
    return _OMNI_TAG_RE.sub("", text).strip()


def _f32le_to_s16le(payload: bytes) -> bytes:
    """VoxCPM f32le PCM → s16le (voice/tts.py to_s16le portu)."""
    floats = array("f")
    floats.frombytes(payload[: len(payload) - len(payload) % 4])
    return array(
        "h", (max(-32768, min(32767, int(f * 32767.0))) for f in floats)
    ).tobytes()


class OmniVoiceTTS(tts.TTS):
    """OmniVoice (VoxCPM WS) TTS plugin. openai/cartesia TTS yerine geçer."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        voice: Optional[str] = None,
        token: Optional[str] = None,
    ):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=DEFAULT_SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
        )
        self._host = host
        self._port = port
        self._voice = voice
        self._token = token

    def _ws_url(self) -> str:
        url = f"ws://{self._host}:{self._port}/ws"
        if self._token:
            url += f"?token={self._token}"
        return url

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> "OmniVoiceChunkedStream":
        return OmniVoiceChunkedStream(
            tts=self, input_text=text, conn_options=conn_options
        )


class OmniVoiceChunkedStream(tts.ChunkedStream):
    """Bir synth turu: OmniVoice WS'e speak gönder, PCM chunk'ları emit et."""

    def __init__(
        self,
        *,
        tts: OmniVoiceTTS,
        input_text: str,
        conn_options: APIConnectOptions,
    ):
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._omni = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        request_id = utils.shortuuid()
        # Metin HAM gider (etiketler OmniVoice'e ulaşsın).
        msg: dict = {"type": "speak", "id": "brain", "text": self._input_text}
        if self._omni._voice:
            msg["voice"] = self._omni._voice

        initialized = False

        async with websockets.connect(
            self._omni._ws_url(), max_size=16 * 1024 * 1024
        ) as ws:
            await ws.send(json.dumps(msg, ensure_ascii=False))
            async for raw in ws:
                if isinstance(raw, (bytes, bytearray)):
                    if not initialized:
                        output_emitter.initialize(
                            request_id=request_id,
                            sample_rate=DEFAULT_SAMPLE_RATE,
                            num_channels=NUM_CHANNELS,
                            mime_type="audio/pcm",
                        )
                        initialized = True
                    output_emitter.push(_f32le_to_s16le(bytes(raw)))
                    continue

                event = json.loads(raw)
                etype = event.get("type")
                if etype == "audio_start":
                    if not initialized:
                        output_emitter.initialize(
                            request_id=request_id,
                            sample_rate=int(event.get("sample_rate", DEFAULT_SAMPLE_RATE)),
                            num_channels=int(event.get("channels", NUM_CHANNELS)),
                            mime_type="audio/pcm",
                        )
                        initialized = True
                elif etype == "audio_end":
                    break
                elif etype == "error":
                    raise RuntimeError(f"OmniVoice: {event.get('message')}")

        output_emitter.flush()
