"""speaker_tap — AgentSession'dan BAĞIMSIZ paralel "speaker tap".

Uzak participant'ın mikrofon track'ine ayrı bir `rtc.AudioStream` bağlar,
~SPEAKER_MIN_SECONDS ses biriktirir, `SpeakerID.embed` + `identify` ile kişiyi
çözer ve paylaşılan `SpeakerState.current`'i (isim veya None) günceller. STT'ye
DOKUNMAZ — Faz 2 pipeline'ı aynen çalışır; bu additive bir dinleyicidir.

SPEAKER_ID_ENABLED kapalı / model yok ise agent.py bu modülü hiç kurmaz.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os

from livekit import rtc

from speaker_id import SpeakerID, pcm_to_f32

log = logging.getLogger("worker.speaker_tap")

TAP_RATE = 16000  # sherpa 16k dışını içeride resample eder; 16k besliyoruz
TAP_CHANNELS = 1


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, "") or default))
    except (TypeError, ValueError):
        return default


class SpeakerState:
    """Paylaşılan güncel-konuşmacı durumu. `current` = tanınan isim veya None.

    Yapışkan (hysteresis): güvenle tanınan kişi, aradaki güvensiz pencerelerde
    HEMEN düşmez; ancak art arda `sticky_misses` kadar güvensiz pencere gelince
    unknown'a iner. Konuşmacı yalnızca güvenli FARKLI bir kişi tanınınca değişir.
    """

    def __init__(self, sticky_misses: int = 5) -> None:
        self.current: str | None = None
        self.score: float = 0.0
        # Faz 3.1: son hesaplanan HAM embedding (normalize edilmemiş). Sesli
        # oto-enrollment onaylanınca bu ses örneği kişiye yazılır.
        self.last_embedding = None  # np.ndarray | None
        self.sticky_misses = max(1, int(sticky_misses))
        self._misses = 0  # art arda güvensiz (identify=None) pencere sayacı

    def observe(self, name: str | None, score: float) -> bool:
        """Yapışkan güncelleme. `name` = identify sonucu (None = güvensiz pencere,
        yalnızca KONUŞMA içeren pencereler için çağrılmalı — sessizlik değil).
        Döner: `current` değişti mi (bool)."""
        prev = self.current
        self.score = score
        if name is not None:
            # Güvenli tanıma: aynı kişi → koru; None/farklı kişi → o kişiye geç.
            self._misses = 0
            if name != self.current:
                self.current = name
        else:
            # Güvensiz pencere: sabra bağla, hemen sıfırlama.
            self._misses += 1
            if self.current is not None and self._misses >= self.sticky_misses:
                self.current = None
                self._misses = 0
        return self.current != prev


class SpeakerTap:
    """Room'daki her uzak mikrofon track'i için bir embed/identify döngüsü sürer."""

    def __init__(self, sp: SpeakerID, state: SpeakerState, min_seconds: float = 1.0):
        self._sp = sp
        self._state = state
        self._min_seconds = max(0.5, min_seconds)
        self._tasks: dict[str, asyncio.Task] = {}
        # Konuşma-kapısı: normalize [-1,1] RMS eşiği. Bunun altındaki (sessizlik/
        # kelime-arası) pencereler identify EDİLMEZ; current DEĞİŞMEZ.
        self._vad_rms = _f("SPEAKER_VAD_RMS", 0.01)

    def attach(self, room: rtc.Room) -> None:
        """Track subscribe olaylarını dinle; mevcut abonelikleri de yakala."""
        room.on("track_subscribed", self._on_track_subscribed)
        # AgentSession zaten abone olmuş olabilir → mevcut track'leri tara.
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
        self._tasks[key] = asyncio.create_task(self._consume(track, key))

    async def _consume(self, track, key: str) -> None:
        stream = rtc.AudioStream.from_track(
            track=track, sample_rate=TAP_RATE, num_channels=TAP_CHANNELS
        )
        need = int(self._min_seconds * TAP_RATE) * 2  # bayt (s16le mono)
        buf = bytearray()
        log.info("speaker-tap: track dinleniyor (%s)", key)
        try:
            async for event in stream:
                payload = bytes(event.frame.data)
                if not payload:
                    continue
                buf.extend(payload)
                if len(buf) < need:
                    continue
                chunk = bytes(buf)
                buf = bytearray()  # kayan pencere: her ~min_seconds bir örnek
                try:
                    samples = pcm_to_f32(chunk, width=2, channels=TAP_CHANNELS)
                    # Konuşma-kapısı: düşük-enerji (sessizlik) pencerelerini ATLA.
                    # identify çağırma, current'ı değiştirme → sessizlik "unknown"
                    # üretmez, yapışkan state bozulmaz.
                    rms = float(math.sqrt(float((samples * samples).mean()))) if samples.size else 0.0
                    if rms < self._vad_rms:
                        log.debug("speaker-tap: sessiz pencere atlandı (rms=%.4f)", rms)
                        continue
                    emb = await asyncio.to_thread(
                        self._sp.embed_samples, samples, TAP_RATE
                    )
                    # Enrollment için son ham embedding'i sakla (yalnızca KONUŞMA
                    # penceresi → sessizlik yanlış-pozitif enroll tetiklemez).
                    self._state.last_embedding = emb
                    name, score = self._sp.identify(emb)
                except Exception as e:  # noqa: BLE001
                    log.debug("speaker-tap embed/identify hata: %s", e)
                    continue
                # Yapışkan güncelleme: anlık unknown current'ı hemen düşürmez.
                if self._state.observe(name, score):
                    log.info(
                        "speaker-tap: konuşmacı → %s (skor=%.3f)",
                        self._state.current or "unknown", score,
                    )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.debug("speaker-tap stream bitti (%s): %s", key, e)
        finally:
            await stream.aclose()

    async def aclose(self) -> None:
        for t in self._tasks.values():
            t.cancel()
        self._tasks.clear()
