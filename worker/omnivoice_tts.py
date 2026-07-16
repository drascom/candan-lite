"""omnivoice_tts — livekit-agents TTS plugin over OmniVoice.

İki yol:
  • NÖTR (varsayılan): WS `ws://TTS_HOST:TTS_PORT/ws`
      gönder: {"type":"speak","id":...,"text":...,"voice":...}
      al:     JSON audio_start{sample_rate,channels,format} (ÖNCE gelir) →
              binary pcm f32le chunk'lar → JSON audio_end / error{message}
      Ölçülen çıktı: pcm_f32le, 24 kHz, mono. Hızlı, streaming.
  • DUYGULU: metinde `[mood:excited]` / `[mood:sad]` KONTROL işareti görülünce
      o tur HTTP `POST /api/tts` ile sentezlenir (pitch+speed preset'i, mode=clone,
      use_pinned). Dönen WAV (RIFF, 24 kHz mono s16le) parse edilip emit edilir.
      Ses kimliği (klon) korunur; OmniVoice emotion desteklemez — pitch/speed ile
      yaklaşırız.

Mood kalıcılığı: livekit-agents streaming=False TTS'i cümle-başına synthesize()
çağırır. Plugin "current mood" durumu tutar; turun ilk cümlesinde işaret görülünce
set edilir, kalan cümlelerde uygulanır. YENİ tur başında agent.py (agent_state
"thinking") `reset_mood()` çağırır → tur nötr başlar.

Etiketler: LLM metne [laughter]/[sigh]/… gömer; OmniVoice bunları SESLENDİRİR →
metne HAM (etiketli) gider. `[mood:...]` FARKLI: KONTROL işareti, seslendirilmez,
metinden SİLİNİR. Mood regex'i yalnızca `[mood:...]` yakalar; voiced tag'lere DOKUNMAZ.

f32le → s16le'ye çevrilip AudioEmitter'a (mime audio/pcm) verilir (WS yolu).
WAV zaten s16le olduğundan HTTP yolunda dönüşüm gerekmez.
"""
from __future__ import annotations

import io
import json
import logging
import re
import wave
from array import array
from typing import Optional

import aiohttp
import websockets

from livekit.agents import tts, utils
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions

from log_utils import DedupeFilter

logger = logging.getLogger("omnivoice_tts")
logger.addFilter(DedupeFilter())

# OmniVoice non-verbal etiketleri (adapter.py ~207-221). TTS metnine gömülür ve
# OmniVoice seslendirir; sadece transkript gösteriminde strip edilir.
_OMNI_TAG_RE = re.compile(
    r"\s*\[(?:laughter|sigh|confirmation-en"
    r"|question-(?:en|ah|oh|ei|yi)"
    r"|surprise-(?:ah|oh|wa|yo)"
    r"|dissatisfaction-hnn)\]",
    re.IGNORECASE,
)

# ── Duygu preset'leri ────────────────────────────────────────────────────────
# OmniVoice emotion desteklemez; sadece pitch+speed ile yaklaşıyoruz. Ses kimliği
# (clone/use_pinned) korunur. Orkestratör sesle ince ayar yapacak → tek yerde tut.
MOOD_PRESETS: dict[str, dict] = {
    "excited": {"instruct": "high pitch", "speed": 1.18},
    "sad":     {"instruct": "low pitch",  "speed": 0.85},
}

# `[mood:excited]` / `[mood:sad]` KONTROL işareti. Yalnızca bunu yakalar; voiced
# tag'lere ([laughter] vb.) DOKUNMAZ. Büyük/küçük harf duyarsız.
_MOOD_RE = re.compile(r"\s*\[mood:(excited|sad)\]\s*", re.IGNORECASE)

# OmniVoice gerçek çıktı hızı (audio_start ile ölçüldü). 48000 dersek 2× hızlı çalar.
DEFAULT_SAMPLE_RATE = 24000
NUM_CHANNELS = 1


def _extract_mood(text: str) -> tuple[Optional[str], str]:
    """Metinden `[mood:X]` KONTROL işaretini çıkar.

    Döner: (mood | None, işaret çıkarılmış metin). Voiced tag'ler ([laughter] vb.)
    _MOOD_RE ile eşleşmediğinden AYNEN korunur.
    """
    if not text or "[" not in text:
        return None, text
    m = _MOOD_RE.search(text)
    if not m:
        return None, text
    mood = m.group(1).lower()
    cleaned = _MOOD_RE.sub(" ", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return mood, cleaned


def _strip_omni_tags(text: str) -> str:
    """OmniVoice [etiket]'lerini çıkar (gösterim için; TTS metni ham kalır)."""
    if not text or "[" not in text:
        return text
    return _OMNI_TAG_RE.sub("", text).strip()


def _f32le_to_s16le(payload: bytes) -> bytes:
    """OmniVoice f32le PCM → s16le."""
    floats = array("f")
    floats.frombytes(payload[: len(payload) - len(payload) % 4])
    return array(
        "h", (max(-32768, min(32767, int(f * 32767.0))) for f in floats)
    ).tobytes()


class OmniVoiceTTS(tts.TTS):
    """OmniVoice TTS plugin. openai/cartesia TTS yerine geçer.

    Mood-farkındalıklı: `_current_mood` tur boyunca yaşar. agent.py yeni tur
    başında `reset_mood()` çağırır.
    """

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
        # Tur-kapsamlı duygu durumu. İlk cümlede [mood:X] ile set, tur boyu uygulanır,
        # yeni turda reset_mood() ile None'a döner.
        self._current_mood: Optional[str] = None

    def reset_mood(self) -> None:
        """Yeni tur başında nötr'e dön (agent.py agent_state 'thinking' hook'undan)."""
        self._current_mood = None

    def _ws_url(self) -> str:
        url = f"ws://{self._host}:{self._port}/ws"
        if self._token:
            url += f"?token={self._token}"
        return url

    def _http_url(self) -> str:
        return f"http://{self._host}:{self._port}/api/tts"

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
    """Bir synth turu (livekit cümle-başına çağırabilir): mood varsa HTTP, yoksa WS."""

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
        # KONTROL işaretini yakala + metinden SİL. İşaret bu cümlede varsa mood'u
        # tur için set et; yoksa turdan devreden mood (varsa) geçerli kalır.
        mood, text = _extract_mood(self._input_text)
        if mood is not None:
            self._omni._current_mood = mood
        effective_mood = self._omni._current_mood

        if effective_mood in MOOD_PRESETS:
            await self._run_http(output_emitter, text, effective_mood)
        else:
            await self._run_ws(output_emitter, text)

    async def _run_ws(self, output_emitter: tts.AudioEmitter, text: str) -> None:
        """Nötr yol: WS ile streaming sentez (mevcut davranış, değişmedi)."""
        request_id = utils.shortuuid()
        # Metin HAM gider (voiced etiketler OmniVoice'e ulaşsın). [mood:X] zaten silindi.
        msg: dict = {"type": "speak", "id": "brain", "text": text}
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

    async def _run_http(
        self, output_emitter: tts.AudioEmitter, text: str, mood: str
    ) -> None:
        """Duygulu yol: HTTP /api/tts (pitch+speed preset). Ses kimliği (clone) korunur.

        Dönen WAV s16le olduğundan f32 dönüşümü GEREKMEZ; PCM doğrudan push edilir.
        """
        request_id = utils.shortuuid()
        preset = MOOD_PRESETS[mood]

        form = aiohttp.FormData()
        form.add_field("text", text)
        form.add_field("language", "Turkish")
        form.add_field("mode", "clone")
        form.add_field("use_pinned", "true")
        form.add_field("instruct", preset["instruct"])
        form.add_field("speed", str(preset["speed"]))

        async with (
            aiohttp.ClientSession() as sess,
            sess.post(self._omni._http_url(), data=form) as resp,
        ):
            resp.raise_for_status()
            wav_bytes = await resp.read()

        with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
            sample_rate = wav.getframerate()
            num_channels = wav.getnchannels()
            pcm = wav.readframes(wav.getnframes())  # s16le

        output_emitter.initialize(
            request_id=request_id,
            sample_rate=sample_rate or DEFAULT_SAMPLE_RATE,
            num_channels=num_channels or NUM_CHANNELS,
            mime_type="audio/pcm",
        )
        output_emitter.push(pcm)
        output_emitter.flush()
