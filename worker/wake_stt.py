"""wake_stt — AgentSession'dan BAĞIMSIZ paralel "wake dinleyici".

Ana AgentSession STT'si tüm cümlenin bitmesini bekler; bu modül PARALEL olarak
uzak participant mic track'ine (speaker_tap.py deseni) bir `rtc.AudioStream`
bağlar, silero VAD ile konuşma segmentini yakalar ve segmentin KISA erken
penceresini (WAKE_STT_WINDOW sn) wyoming Whisper'a verir. Transcript'te wake
word ("candan") varsa `on_wake()` çağrılır → brain.wake_now() → çan erken çalar.

ADIM 1 fizibilite (OmniVoice sesiyle offline): kısa-pencere Whisper 'candan'ı
~200-270ms roundtrip'te, 0 false-positive ile yakaladı; ≥1.0s pencere yeterli.

WAKE_STT_ENABLED kapalıysa agent.py bu modülü hiç kurmaz → davranış değişmez.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from livekit import rtc

from whisper_stt import _WhisperSession  # wyoming transcribe roundtrip'i yeniden kullan
# Wake eşleştirme TEK yerde (pi_brain) — kopya sapmasın. Fuzzy/izole tolerans dahil.
from pi_brain import wake_match, _wake_norm, _wake_variants
from log_utils import DedupeFilter

log = logging.getLogger("worker.wake_stt")
log.addFilter(DedupeFilter())  # tekrarlayan stream/hata loglarını seyreltir

TAP_RATE = 16000  # wyoming whisper 16k s16le ister; AudioStream doğrudan 16k verir
TAP_CHANNELS = 1
STT_WIDTH = 2  # s16le
MAX_SEG_SECONDS = 10.0  # tek segment için üst sınır (bellek/latency koruması)


class WakeSTT:
    """Her uzak mic track'i için silero-VAD kapılı kısa-pencere Whisper wake döngüsü.

    on_wake: wake word duyulunca çağrılan callback (idempotent olmalı; brain.wake_now
             zaten idempotent). Transcript metni argüman olarak verilir.
    vad:     livekit.plugins.silero VAD örneği (agent.py'deki ile aynı olabilir; ayrı
             stream açar, ana session'a dokunmaz).
    active:  opsiyonel; False dönerse o an transcribe ATLANIR (ör. sadece UYURKEN çalış —
             uyanıkken ana STT yeterli, çift-transcribe azalır). None → hep çalış.
    """

    def __init__(
        self,
        *,
        vad,
        stt_host: str,
        stt_port: int,
        language: str = "tr",
        wake_word: str = "candan",
        window: float = 1.5,
        on_wake: Callable[[str], None],
        active: Optional[Callable[[], bool]] = None,
    ):
        self._vad = vad
        self._host = stt_host
        self._port = stt_port
        self._lang = language
        self._wake_norm = _wake_norm(wake_word)
        self._wake_variants = _wake_variants(wake_word)
        self._window = max(0.5, float(window))
        self._on_wake = on_wake
        self._active = active
        self._tasks: dict[str, asyncio.Task] = {}
        # asyncio task'lara sadece ZAYIF referans tutar → tutmazsak GC, transcribe'ı
        # iş bitmeden toplayabilir ve wake word SESSİZCE kaçar. Güçlü referans havuzu.
        self._pending: set[asyncio.Task] = set()

    # --- speaker_tap.py ile aynı bağlanma deseni ---
    def attach(self, room: rtc.Room) -> None:
        room.on("track_subscribed", self._on_track_subscribed)
        for participant in list(room.remote_participants.values()):
            for pub in list(participant.track_publications.values()):
                track = getattr(pub, "track", None)
                if track is not None and pub.kind == rtc.TrackKind.KIND_AUDIO:
                    self._spawn(track, participant)

    def _on_track_subscribed(self, track, publication, participant) -> None:
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            self._spawn(track, participant)

    def _spawn(self, track, participant) -> None:
        key = f"{getattr(participant, 'identity', '?')}:{getattr(track, 'sid', id(track))}"
        if key in self._tasks and not self._tasks[key].done():
            return
        self._tasks[key] = asyncio.create_task(self._run_track(track, key))

    def _spawn_bg(self, coro) -> None:
        """Fire-and-forget task — GC toplamasın diye bitene kadar referansı tut."""
        t = asyncio.create_task(coro)
        self._pending.add(t)
        t.add_done_callback(self._pending.discard)

    def _is_active(self) -> bool:
        if self._active is None:
            return True
        try:
            return bool(self._active())
        except Exception:  # noqa: BLE001
            return True

    async def _run_track(self, track, key: str) -> None:
        """Mic frame'lerini AudioStream'den oku → VAD'a besle + konuşurken biriktir."""
        stream = rtc.AudioStream.from_track(
            track=track, sample_rate=TAP_RATE, num_channels=TAP_CHANNELS
        )
        vad_stream = self._vad.stream()
        seg = bytearray()  # aktif konuşma segmentinin s16le ham audio'su
        state = {"speaking": False, "next_win": 0, "busy": False}
        win_bytes = int(self._window * TAP_RATE) * STT_WIDTH
        max_bytes = int(MAX_SEG_SECONDS * TAP_RATE) * STT_WIDTH

        async def transcribe(payload: bytes) -> None:
            """Kısa pencere → wyoming Whisper → wake word varsa on_wake (tek seferde bir)."""
            if state["busy"] or not payload or not self._is_active():
                return
            state["busy"] = True
            try:
                sess = _WhisperSession(self._host, self._port, self._lang)
                try:
                    await sess.start(rate=TAP_RATE, width=STT_WIDTH, channels=TAP_CHANNELS)
                    await sess.feed(payload, TAP_RATE, STT_WIDTH, TAP_CHANNELS)
                    text = await sess.finish()
                except (ConnectionError, OSError) as e:
                    log.debug("wake_stt: whisper erişilemiyor: %s", e)
                    await sess.abort()
                    return
                if text and wake_match(text, self._wake_norm, self._wake_variants)[0]:
                    log.info("wake_stt: WAKE tespit → %r", text)
                    try:
                        self._on_wake(text)
                    except Exception:  # noqa: BLE001 — callback hatası akışı bozmasın
                        log.warning("wake_stt on_wake hata", exc_info=True)
            finally:
                state["busy"] = False

        async def pump_vad() -> None:
            """VAD olayları: konuşma başı/sonu → segment sınırlarını yönet."""
            try:
                async for ev in vad_stream:
                    et = getattr(ev, "type", None)
                    name = getattr(et, "name", str(et))
                    if name == "START_OF_SPEECH":
                        seg.clear()
                        state.update(speaking=True, next_win=1)
                    elif name == "END_OF_SPEECH":
                        state["speaking"] = False
                        if seg:  # segment sonu: tüm (kısa) segmenti son bir kez dene
                            self._spawn_bg(transcribe(bytes(seg)))
                        seg.clear()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.debug("wake_stt vad-pump bitti (%s): %s", key, e)

        pump = asyncio.create_task(pump_vad())
        log.info("wake_stt: track dinleniyor (%s), pencere=%.1fs", key, self._window)
        try:
            async for event in stream:
                frame = event.frame
                vad_stream.push_frame(frame)
                if not state["speaking"]:
                    continue
                seg.extend(bytes(frame.data))
                if len(seg) > max_bytes:  # aşırı uzun → baştan pencere kadar tut
                    del seg[:-win_bytes]
                # Erken pencere: konuşurken WINDOW dolar dolmaz transcribe et (cümle
                # bitmeden 'candan'ı yakala). Her katta bir kez (next_win artar).
                if len(seg) >= win_bytes * state["next_win"]:
                    state["next_win"] += 1
                    self._spawn_bg(transcribe(bytes(seg)))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.debug("wake_stt stream bitti (%s): %s", key, e)
        finally:
            pump.cancel()
            try:
                await vad_stream.aclose()
            except Exception:  # noqa: BLE001
                pass
            await stream.aclose()

    async def aclose(self) -> None:
        for t in self._tasks.values():
            t.cancel()
        self._tasks.clear()
        for t in list(self._pending):   # uçuşta kalan transcribe'lar da dursun
            t.cancel()
        self._pending.clear()
