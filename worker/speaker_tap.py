"""speaker_tap — AgentSession'dan BAĞIMSIZ paralel "speaker tap".

Uzak participant'ın mikrofon track'ine ayrı bir `rtc.AudioStream` bağlar,
~SPEAKER_MIN_SECONDS ses biriktirir, `SpeakerID.embed_samples` + `identify` ile kişiyi
çözer ve paylaşılan `SpeakerState.current`'i (isim veya None) günceller. STT'ye
DOKUNMAZ; bu, LiveKit Agent'ın paralel kimlik dinleyicisidir.

SPEAKER_ID_ENABLED kapalı / ReDimNet2 yüklenemezse agent.py bu modülü kurmaz.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from livekit import rtc

import prosody
from speaker_id import SpeakerID, emb_to_bytes, pcm_to_f32
from log_utils import DedupeFilter

log = logging.getLogger("worker.speaker_tap")
log.addFilter(DedupeFilter())  # "sessiz pencere atlandı" vb. tekrarları seyreltir

TAP_RATE = 16000  # ReDimNet2 üretim ve deney ön işlemesiyle aynı örnekleme hızı
TAP_CHANNELS = 1


@dataclass(frozen=True)
class TurnSpeakerDecision:
    """One final, turn-scoped identity decision consumed by transcript and PiBrain.

    `candidate*` alanları KİMLİK DEĞİLDİR. Karar `name=None` (Bilinmeyen) olsa bile
    turun kabul edilmiş pencerelerinden bir "sormaya değer mi" adayı hesaplanır.
    Persona swap'i, `_identity_note()` ve hafıza kimliği YALNIZ `name`e bakar —
    aday oraya ASLA sızmaz (bkz. pi_brain._confirm_identity_line).
    """

    name: str | None
    score: float
    reason: str
    accepted: int
    total: int
    candidate: str | None = None
    candidate_ratio: float = 0.0
    candidate_score: float = 0.0
    candidate_windows: int = 0


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


def _b(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class SessionEmbLog:
    """Gölge kaydedici: pencere embedding'lerini oturum başına `.npz`'ye yazar.

    NEDEN: pencere embedding'leri şimdiye dek hiçbir yere yazılmıyordu — RAM'de
    duruyor ve her `begin_turn()`'de siliniyordu. Reddedilen pencerelerin skorları
    yalnız `log.debug`'a gidiyor, varsayılan seviye INFO olduğu için diske hiç
    düşmüyordu. Sonuç: "komşu pencereyle karşılaştırma" gibi fikirler GEÇMİŞ
    veride hiç denenemiyordu. Asıl değerli veri REDDEDİLEN pencerelerdir; bu
    yüzden onlar da yazılır.

    SALT GÖZLEM: karar mantığına dokunmaz, hiçbir değer döndürmez, hızlı yolun
    davranışını değiştirmez. `add()` yalnız RAM tamponuna yazar (mikrosaniye);
    diske yazım turlar ARASINDA (`turn_active` False iken) ayrı bir thread'de
    yapılır, yani gerçek zamanlı yol bloklanmaz.

    ⚠️ BİYOMETRİK: embedding biyometrik veridir, depo PUBLIC. Dosyalar
    `worker/data/` altına yazılır (`.gitignore`'da), log'a/transkripte ASLA
    embedding basılmaz ve TTL süresi dolan dosyalar açılışta silinir.

    ŞEMA 3: akış alanlarına model kimliği eklendi. `t_rel` (pencerenin tur başına ofseti),
    `track_id`, `capture_ok`, tur kararı (`turn_final_name/reason`) ve `prosody.py`
    öznitelikleri. Bunların hiçbiri ham ses SAKLANMADAN sonradan üretilemez;
    eklenmezse bugüne kadar toplanan veri akış analizi için ölüdür. Eski dosyalar
    bu alanları içermez → okuma tarafı `schema_version`'a bakmalıdır.
    """

    SCHEMA_VERSION = 3

    def __init__(self, model_id: str) -> None:
        # DİKKAT (DEVIR §7, `c9d0d27`): env modül seviyesinde okunursa `.env`
        # ETKİSİZ kalır. Burası çağrı anında (SpeakerTap kurulurken, load_dotenv
        # SONRASI) okunur; hiçbiri varsayılan argümana bağlı değildir.
        self.enabled = _b("SPEAKER_EMB_LOG_ENABLED", True)
        self._ttl_days = _f("SPEAKER_EMB_LOG_TTL_DAYS", 7.0)
        self._max_windows = _i("SPEAKER_EMB_LOG_MAX_WINDOWS", 20000)
        here = Path(__file__).resolve().parent
        raw_dir = (os.getenv("SPEAKER_EMB_LOG_DIR") or "").strip()
        base = Path(raw_dir) if raw_dir else Path("data") / "session-emb"
        self.dir = base if base.is_absolute() else here / base
        self.session_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
        self.path = self.dir / f"{self.session_id}.npz"
        self.model_id = str(model_id)
        self._rows: list[tuple] = []
        # tur numarası → (karar adı, gerekçe). Pencereler tur BİTMEDEN yazıldığı
        # için karar sonradan eşlenir (bkz. `_write`). Sözlük tur başına tek satır.
        self._turn_decisions: dict[int, tuple[str, str]] = {}
        self._dim: int | None = None
        self._pending = 0  # henüz diske yazılmamış satır sayısı
        self._swept = False
        self._dropped = 0
        if self.enabled:
            log.info("gölge embedding kaydı: %s (TTL %.0f gün)", self.path, self._ttl_days)

    @property
    def pending(self) -> int:
        return self._pending

    def note_turn_decision(self, turn: int, decision) -> None:
        """Bir turun NİHAİ kararını eşle. Salt gözlem; kararı okur, üretmez.

        `resolve_turn()` sonrası çağrılır. Bu iki alan olmadan "%70.5 Bilinmeyen"
        metriği toplanan veriden offline ÜRETİLEMEZ — pencere satırlarında turun
        nasıl bittiği yazmıyordu.
        """
        if not self.enabled:
            return
        try:
            row = (str(getattr(decision, "name", None) or ""), str(getattr(decision, "reason", "")))
            if self._turn_decisions.get(int(turn)) != row:
                self._turn_decisions[int(turn)] = row
                self._pending += 1  # dosya yeniden yazılsın
        except Exception as e:  # noqa: BLE001 — ölçüm ASLA akışı bozmasın
            log.debug("gölge tur kararı atlandı: %s", e)

    def add(
        self,
        *,
        turn: int,
        embedding,
        decided: str | None,
        ranking: list[tuple[str, float]],
        window_seconds: float,
        rms: float,
        t_rel: float = float("nan"),
        track_id: int = -1,
        capture_ok: int = -1,
        features: dict | None = None,
    ) -> None:
        """Bir pencereyi tampona ekle. ASLA yükselmez, ASLA disk'e dokunmaz."""
        if not self.enabled:
            return
        try:
            arr = np.asarray(embedding)
            if arr.ndim == 0 or arr.size == 0:
                return  # None / skaler: gömme değil
            vec = arr.astype(np.float16).reshape(-1)
            if self._dim is None:
                self._dim = int(vec.size)
            elif int(vec.size) != self._dim:
                return  # model değişmiş: karışık boyutlu matris yazma
            if len(self._rows) >= self._max_windows:
                self._dropped += 1
                return
            best_name, best_score = ranking[0] if ranking else ("", 0.0)
            second_name, second_score = ranking[1] if len(ranking) > 1 else ("", 0.0)
            self._rows.append((
                time.time(), int(turn), vec,
                str(best_name), float(best_score),
                str(second_name), float(second_score),
                float(window_seconds), float(rms), str(decided or ""),
                float(t_rel), int(track_id), int(capture_ok),
                features if isinstance(features, dict) else {},
            ))
            self._pending += 1
        except Exception as e:  # noqa: BLE001 — ölçüm ASLA akışı bozmasın
            log.debug("gölge embedding kaydı atlandı: %s", e)

    async def maybe_flush(self) -> None:
        """Bekleyen satır varsa dosyayı yeniden yaz (thread'de, olay döngüsü serbest)."""
        if not self.enabled or self._pending <= 0:
            return
        rows = list(self._rows)  # anlık görüntü: `add` paralel ekleyebilir
        decisions = dict(self._turn_decisions)
        self._pending = 0
        try:
            await asyncio.to_thread(self._write, rows, decisions)
        except Exception as e:  # noqa: BLE001
            log.debug("gölge embedding yazımı başarısız: %s", e)

    def _write(self, rows: list[tuple], decisions: dict[int, tuple[str, str]] | None = None) -> None:
        """Thread içinde çalışır. Atomik: geçici dosya + `os.replace`."""
        if not rows:
            return
        decisions = decisions or {}
        self.dir.mkdir(parents=True, exist_ok=True)
        if not self._swept:
            self._swept = True
            self._sweep()
        empty: dict = {}
        cols = {
            "schema_version": np.array(self.SCHEMA_VERSION, dtype=np.int16),
            "model_id": np.array(self.model_id),
            "ts": np.array([r[0] for r in rows], dtype=np.float64),
            "turn": np.array([r[1] for r in rows], dtype=np.int32),
            "emb": np.stack([r[2] for r in rows]),
            "best_name": np.array([r[3] for r in rows]),
            "best_score": np.array([r[4] for r in rows], dtype=np.float32),
            "second_name": np.array([r[5] for r in rows]),
            "second_score": np.array([r[6] for r in rows], dtype=np.float32),
            "window_seconds": np.array([r[7] for r in rows], dtype=np.float32),
            "rms": np.array([r[8] for r in rows], dtype=np.float32),
            "decided": np.array([r[9] for r in rows]),
            # AKIŞ: `t_rel` olmadan pencereler tur içinde sırasız bir yığın olur.
            "t_rel": np.array([r[10] for r in rows], dtype=np.float32),
            "track_id": np.array([r[11] for r in rows], dtype=np.int16),
            "capture_ok": np.array([r[12] for r in rows], dtype=np.int8),
            "turn_final_name": np.array([decisions.get(r[1], ("", ""))[0] for r in rows]),
            "turn_final_reason": np.array([decisions.get(r[1], ("", ""))[1] for r in rows]),
        }
        for name in prosody.SCALAR_FIELDS:
            cols[name] = np.array(
                [float((r[13] or empty).get(name, np.nan)) for r in rows], dtype=np.float16
            )
        for name, size in prosody.VECTOR_FIELDS.items():
            cols[name] = np.stack([
                np.asarray((r[13] or empty).get(name, np.full(size, np.nan)), dtype=np.float16)
                for r in rows
            ])
        tmp = self.dir / f".{self.session_id}.tmp.npz"
        with open(tmp, "wb") as f:
            np.savez(f, **cols)
        os.replace(tmp, self.path)

    def _sweep(self) -> None:
        """TTL süresi dolmuş oturum dosyalarını sil — biyometrik veri birikmesin."""
        if self._ttl_days <= 0:
            return
        cutoff = time.time() - self._ttl_days * 86400.0
        removed = 0
        for p in self.dir.glob("*.npz"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
                    removed += 1
            except OSError:
                continue
        if removed:
            log.info("gölge embedding: %d eski oturum dosyası silindi (TTL)", removed)


class SpeakerState:
    """Turn-scoped speaker identity state.

    `current` is cleared when a turn opens (first VAD speech start after the
    previous turn was resolved) and is set only by `resolve_turn()`. Continuous
    tap observations outside the active turn can never become evidence for a
    later transcript. This prevents the previous sticky identity from being
    injected into a new user's prompt.
    """

    def __init__(
        self,
        sticky_misses: int = 5,
        confirm_hits: int = 2,
        turn_confirm_hits: int | None = None,
        turn_max_seconds: float = 8.0,
        continuity_seconds: float | None = None,
        fast_single_enabled: bool | None = None,
        fast_single_score: float | None = None,
        fast_single_margin: float | None = None,
    ) -> None:
        self.current: str | None = None
        # Konuşma bağlamı, biyometrik karar değildir. Önceki Candan cevabı bir
        # soru/eylem sonucuysa ve hemen arkasından ses penceresi üretmeyecek kadar
        # kısa bir yanıt gelirse UI/hitap akışı için önceki ad burada taşınabilir.
        # Persona, hafıza, yetki ve kalıcı yazma yolları YALNIZ `current`i okur.
        self.contextual_current: str | None = None
        self.contextual_reason: str | None = None
        self.score: float = 0.0
        # Faz 3.1: son hesaplanan HAM embedding (normalize edilmemiş). Sesli
        # oto-enrollment onaylanınca bu ses örneği kişiye yazılır.
        self.last_embedding = None  # np.ndarray | None
        # Her embedding'in ÜRETİLDİĞİ andaki enrollment kalite kararı. Toplayıcı
        # embedding'i birkaç yüz ms sonra yoklar; o arada agent "thinking" durumuna
        # geçerse canlı bayrağa bakmak, kullanıcının az önceki sesini yanlışlıkla
        # Candan yankısı sayıp elemek olur. Bu yüzden karar embedding ile birlikte
        # atomik gibi taşınır (None = gate bağlı değil / varsayılan kabul).
        self.last_embedding_capture_ok: bool | None = None
        self.last_embedding_capture_reason: str | None = None
        self.sticky_misses = max(1, int(sticky_misses))
        self._misses = 0  # art arda güvensiz (identify=None) pencere sayacı
        # KRİTİK: unknown → kayıtlı kişi geçişi tek pencereye güvenmez. Canlıda
        # yabancı bir sesin tek penceresi model eşiğini geçip doğrudan Ayhan olması
        # hem yanlış selama hem de yanlış hafıza/persona bağlamına yol açıyordu.
        # Aynı isim bu kadar ARDIŞIK güvenli pencerede görülmeden `current` değişmez.
        self.confirm_hits = max(2, int(confirm_hits))
        self._candidate: str | None = None
        self._candidate_hits = 0
        self.turn_confirm_hits = max(
            2,
            int(turn_confirm_hits if turn_confirm_hits is not None else confirm_hits),
        )
        self.turn_max_seconds = max(1.0, float(turn_max_seconds))
        # Önceki dönüşün kimliğini KÖRÜ KÖRÜNE devralmayız. Ancak bu süre içinde
        # aynı kişi için yeni dönüşte EN AZ BİR güvenli (normal strict identify'dan
        # geçmiş) pencere varsa, iki pencere aramayız. Kısa doğal devam cümleleri
        # böylece Bilinmeyen'e düşmez; yabancı/sessiz ses ise ad alamaz.
        if continuity_seconds is None:
            continuity_seconds = _f("SPEAKER_CONTINUITY_SECONDS", 12.0)
        self.continuity_seconds = max(0.0, float(continuity_seconds))
        # Kısa konuşmada yalnız bir pencere yetişebiliyor. ReDimNet2 o pencereyi
        # güçlü biçimde ayırmışsa ikinci pencereyi zorlamak doğru sonucu gereksizce
        # Bilinmeyen yapıyor. Genel identify eşiği DEĞİŞMEZ; bu yol kalibre skor
        # tabanı + daha sıkı marj ister ve aynı turda başka isim varsa çalışmaz.
        if fast_single_enabled is None:
            fast_single_enabled = _b("SPEAKER_FAST_SINGLE_ENABLED", True)
        if fast_single_score is None:
            fast_single_score = _f("SPEAKER_FAST_SINGLE_SCORE", 0.568)
        if fast_single_margin is None:
            fast_single_margin = _f("SPEAKER_FAST_SINGLE_MARGIN", 0.25)
        self.fast_single_enabled = bool(fast_single_enabled)
        self.fast_single_score = float(fast_single_score)
        self.fast_single_margin = max(0.0, float(fast_single_margin))
        self._last_confirmed_name: str | None = None
        self._last_confirmed_at = 0.0
        self._turn_active = False
        self._turn_started_at = 0.0
        # Her yeni kullanıcı dönüşü bir üretim numarası alır. SpeakerTap bu sayıyı
        # izleyerek önceki konuşmacı/Candan sesiyle dolu ses tamponunu anında atar.
        # Böylece kayan pencere hiçbir zaman yeni STT dönüşünden ÖNCEKİ sesin
        # embedding'ini üretmez.
        self._turn_generation = 0
        # Embed/identify işi CPU thread'inde sürerken final STT gelebilir. Final
        # karar bu sayaç sıfırlanmadan kapanırsa, birkaç yüz ms sonra gelen doğru
        # pencere eski üretime ait diye çöpe gider ve UI "Bilinmeyen" yazar.
        # Üretim başına sayaç + Event, final yolu için sınırlı bir bekleme kapısıdır.
        self._pending_observations: dict[int, int] = {}
        self._pending_events: dict[int, asyncio.Event] = {}
        # (zaman, isim, skor, marj, embedding) — embedding onay döngüsü için taşınır
        # (bkz. `last_turn_candidate_windows`); yoksa None.
        self._turn_observations: list[
            tuple[float, str | None, float, float, object | None]
        ] = []
        self.last_turn_decision = TurnSpeakerDecision(
            name=None,
            score=0.0,
            reason="henüz dönüş yok",
            accepted=0,
            total=0,
        )
        # Son dönüşün ADAY pencereleri: (skor, embedding), skora göre azalan.
        # Kullanıcı "evet" derse yalnız buradan örnek yazılır — dönüş dışı hiçbir
        # pencere profile giremez.
        self.last_turn_candidate_windows: list[tuple[float, object]] = []
        # İfade corpus'u yalnız açık enrollment akışında etkinleştirilir. Tap zaten
        # uzak mikrofonu dinlediği için WAV ve embedding aynı temiz pencereden gelir.
        self._expression_label: str | None = None
        self._expression_chunks: list[bytes] = []
        self._expression_embeddings: list = []

    def begin_expression_capture(self, label: str) -> None:
        self._expression_label = label
        self._expression_chunks = []
        self._expression_embeddings = []

    def add_expression_window(self, pcm: bytes, embedding) -> None:
        if self._expression_label is not None:
            self._expression_chunks.append(pcm)
            self._expression_embeddings.append(embedding)

    def finish_expression_capture(self) -> tuple[str | None, list[bytes], list]:
        label = self._expression_label
        chunks, embs = self._expression_chunks, self._expression_embeddings
        self._expression_label = None
        self._expression_chunks, self._expression_embeddings = [], []
        return label, chunks, embs

    def discard_expression_capture(self) -> None:
        self._expression_label = None
        self._expression_chunks, self._expression_embeddings = [], []

    def begin_turn(self, now: float | None = None) -> None:
        """Open a new evidence window and invalidate every previous identity.

        KRİTİK (canlı ölçüm 26 Tem): bu metod LiveKit `user_state_changed ->
        speaking` olayına bağlı, yani VAD'in KONUŞMA BAŞLANGICI'na. Tek bir
        kullanıcı dönüşü (tek final transkript) içinde VAD birden çok kez
        speaking↔listening yapar — Türkçe'de cümle içi doğal duraklar 0.55 sn'lik
        VAD sessizlik eşiğini kolayca aşıyor. Eskiden her yeni `speaking`
        tamponu SIFIRLIYORDU: dönüşün ilk 4-6 iyi penceresi çöpe gidiyor, karar
        yalnız SON konuşma parçasından veriliyordu. Son parça çoğu kez ilk
        pencerenin oluşması için gereken süreden kısa → `kabul=0/0`.
        (Canlı kanıt: 20:57:11-14 arası 4 pencerede Ayhan tanındı, 20:57:14'teki
        karar yine de `kabul=0/0` çıktı.)

        Bu yüzden dönüş sınırı artık VAD parçası DEĞİL, dönüşün kendisi:
        yeni dönüş yalnız `resolve_turn()` sonrası ilk `speaking` ile açılır.
        Aktif dönüş içindeki tekrar tetiklemeler NO-OP'tur — kanıt birikmeye
        devam eder. Güvenlik kuralı bozulmaz: önceki dönüşün kimliği hâlâ
        taşınmaz (`resolve_turn` dönüşü kapatır) ve aynı dönüşte iki farklı
        kişi görünürse karar yine `Bilinmeyen` olur — hatta artık dönüşün
        TAMAMI görüldüğü için çelişki DAHA iyi yakalanır.
        """
        if self._turn_active:
            return
        self._turn_generation += 1
        self._turn_active = True
        self._turn_started_at = time.monotonic() if now is None else float(now)
        self._turn_observations = []
        self.last_turn_candidate_windows = []
        self.current = None
        self.contextual_current = None
        self.contextual_reason = None
        self.score = 0.0
        self._candidate = None
        self._candidate_hits = 0
        self._misses = 0

    @property
    def turn_active(self) -> bool:
        """Whether a user turn is currently collecting speaker evidence."""
        return self._turn_active

    @property
    def turn_started_at(self) -> float:
        """Aktif turun `time.monotonic()` başlangıcı — gölge kaydın `t_rel`'i için.

        Salt okuma; karar mantığı bu değeri yalnız `resolve_turn()` içinde kullanır.
        """
        return self._turn_started_at

    @property
    def turn_generation(self) -> int:
        """Monotonic token used by the audio tap to discard pre-turn audio."""
        return self._turn_generation

    def begin_observation(self, generation: int) -> bool:
        """Bu dönüşe ait bir embed/identify işini uçuşta olarak işaretle."""
        if not self._turn_active or generation != self._turn_generation:
            return False
        self._pending_observations[generation] = (
            self._pending_observations.get(generation, 0) + 1
        )
        event = self._pending_events.setdefault(generation, asyncio.Event())
        event.clear()
        return True

    def finish_observation(self, generation: int) -> None:
        """Uçuşta pencereyi kapat ve bekleyen final kararını uyandır."""
        left = max(0, self._pending_observations.get(generation, 0) - 1)
        if left:
            self._pending_observations[generation] = left
            return
        self._pending_observations.pop(generation, None)
        self._pending_events.setdefault(generation, asyncio.Event()).set()

    async def wait_for_pending(
        self, generation: int, timeout: float = 0.4
    ) -> tuple[bool, float]:
        """Uçuşta kimlik işi bitene dek sınırlı bekle.

        Dönen ilk değer bütün işlerin yetişip yetişmediğidir; ikinci değer gerçek
        bekleme süresidir. Timeout fail-open'dır: speaker modeli konuşmayı durdurmaz.
        """
        started = time.monotonic()
        if self._pending_observations.get(generation, 0) <= 0:
            return True, 0.0
        event = self._pending_events.setdefault(generation, asyncio.Event())
        try:
            await asyncio.wait_for(event.wait(), timeout=max(0.0, timeout))
            ready = self._pending_observations.get(generation, 0) <= 0
        except asyncio.TimeoutError:
            ready = False
        return ready, time.monotonic() - started

    async def resolve_turn_when_ready(
        self, timeout: float = 0.4
    ) -> tuple[TurnSpeakerDecision, bool, float]:
        """Final STT ile yarışan son pencereye kısa süre tanı, sonra kararı kapat."""
        generation = self._turn_generation
        ready, waited = await self.wait_for_pending(generation, timeout)
        return self.resolve_turn(), ready, waited

    @property
    def response_name(self) -> str | None:
        """UI/hitap adı; bağlamsal ad biyometrik `current`in yerini almaz."""
        return self.current or self.contextual_current

    def assume_contextual(self, name: str, reason: str) -> bool:
        """Kısa yanıtı yalnız konuşma akışında önceki kişiye bağla.

        Gerçek ses kararı varsa hiçbir şey yapmaz. `_last_confirmed_*`, `current`
        ve skor değişmediği için bu çağrı kimlik doğrulaması/yetki üretmez.
        """
        clean = (name or "").strip()
        if self.current is not None or not clean:
            return False
        self.contextual_current = clean
        self.contextual_reason = (reason or "").strip() or "kısa cevap bağlamı"
        return True

    def resolve_turn(self, now: float | None = None) -> TurnSpeakerDecision:
        """Resolve only observations collected since the latest `begin_turn()`.

        A name is accepted only when one identity has a consecutive confirmation
        run and no other accepted identity appeared in the same turn. Ambiguous
        windows break a run; conflicting names make the whole turn unknown.
        """
        if not self._turn_active:
            return self.last_turn_decision

        finished_at = time.monotonic() if now is None else float(now)
        observations = [
            item
            for item in self._turn_observations
            if self._turn_started_at <= item[0] <= finished_at
        ]
        accepted = [item for item in observations if item[1] is not None]
        names = {item[1] for item in accepted}
        decision_name: str | None = None
        decision_score = 0.0
        reason = "bu dönüşte güvenli ses penceresi yok"

        if len(names) > 1:
            reason = "aynı dönüşte çelişen kimlik pencereleri"
        elif accepted:
            only_name = accepted[0][1]
            best_run: list[tuple[float, str | None, float, float, object | None]] = []
            run: list[tuple[float, str | None, float, float, object | None]] = []
            for item in observations:
                if item[1] == only_name:
                    run.append(item)
                    if len(run) > len(best_run):
                        best_run = list(run)
                else:
                    run = []
            # TAVAN = KAYAN PENCERE (28 Tem canlı bulgusu). Eskiden onay grubunun
            # ilk-son aralığı `turn_max_seconds`'ı aşarsa TÜM kanıt çöpe gidiyordu:
            # 16:25:05'te 15 pencerenin HEPSİ Ayhan (ort. 5.69) olduğu hâlde karar
            # `Bilinmeyen` çıktı, çünkü run 15 sn > 8 sn. Ölçüm bunu desen olarak
            # doğruladı: ≥7 pencereli turlarda başarı %84'ten %43'e düşüyordu —
            # yani UZUN konuşmak tanınma şansını azaltıyordu.
            # Tavanın ASIL amacı korunuyor: "ardışık onay" sayılan pencereler
            # birbirine zaman olarak YAKIN olmalı (aradaki uzun sessizlikte yeni
            # pencere üretilmediği için t=0 ve t=20'deki iki gözlem "ardışık"
            # görünebilir; o iki gözlem tek bir tur kanıtı sayılmamalı).
            # Yeni kural: grubu atmak yerine SON `turn_max_seconds`'lık dilime
            # kırp. Kırpılmış grup her zaman eskisinin bir ALT KÜMESİ, dolayısıyla
            # eşik ASLA gevşemez; yalnız içinde nitelikli, sıkı bir alt grup
            # barındırdığı hâlde tümden atılan turlar kurtulur.
            best_run = self._within_ceiling(best_run)
            if len(best_run) < self.turn_confirm_hits:
                elapsed = finished_at - self._last_confirmed_at
                if (
                    len(best_run) == 1
                    and only_name == self._last_confirmed_name
                    and 0.0 <= elapsed <= self.continuity_seconds
                    and finished_at - best_run[0][0] <= self.turn_max_seconds
                ):
                    decision_name = only_name
                    decision_score = best_run[0][2]
                    reason = "son doğrulamayla uyumlu tek güncel pencere"
                elif (
                    self.fast_single_enabled
                    and len(best_run) == 1
                    and best_run[0][2] >= self.fast_single_score
                    and best_run[0][3] >= self.fast_single_margin
                    and finished_at - best_run[0][0] <= self.turn_max_seconds
                ):
                    decision_name = only_name
                    decision_score = best_run[0][2]
                    reason = (
                        "yüksek güvenli tek güncel pencere "
                        f"(skor={best_run[0][2]:.3f}, marj={best_run[0][3]:.3f})"
                    )
                else:
                    reason = f"yetersiz ardışık onay ({len(best_run)}/{self.turn_confirm_hits})"
            elif finished_at - best_run[-1][0] > self.turn_max_seconds:
                # Dönüş artık VAD parçasına göre değil final transkripte göre
                # kapanıyor; teorik olarak açık kalmış çok uzun bir dönüşte kanıt
                # bayatlayabilir. Onay grubunun SON penceresi transkript anına bu
                # kadar yakın olmalı — "güncel pencere" sözü ölçülebilir kalsın.
                reason = "onay penceresi transkript anına göre bayat"
            else:
                decision_name = only_name
                decision_score = min(item[2] for item in best_run)
                reason = f"{len(best_run)} ardışık güncel pencere"

        cand, ratio, cand_score, cand_windows = self._candidate_of(accepted, finished_at)
        self._turn_active = False
        self.current = decision_name
        self.score = decision_score
        if decision_name is not None:
            self._last_confirmed_name = decision_name
            self._last_confirmed_at = finished_at
        self.last_turn_decision = TurnSpeakerDecision(
            name=decision_name,
            score=decision_score,
            reason=reason,
            accepted=len(accepted),
            total=len(observations),
            candidate=cand,
            candidate_ratio=ratio,
            candidate_score=cand_score,
            candidate_windows=cand_windows,
        )
        return self.last_turn_decision

    def _within_ceiling(
        self, run: list[tuple[float, str | None, float, float, object | None]]
    ) -> list[tuple[float, str | None, float, float, object | None]]:
        """Onay grubunun yalnız SON `turn_max_seconds` saniyelik dilimini döndür.

        Amaç: "ardışık onay" pencerelerinin zamanda da bitişik olmasını zorlamak.
        Grubu tümden ELEMEZ — en yeni uçtan kırpar; sonuç daima girdinin alt
        kümesidir, bu yüzden karar eşiği gevşemez (bkz. `resolve_turn`).
        """
        if not run:
            return run
        newest = run[-1][0]
        return [item for item in run if newest - item[0] <= self.turn_max_seconds]

    def _candidate_of(
        self,
        accepted: list[tuple[float, str | None, float, float, object | None]],
        finished_at: float,
    ) -> tuple[str | None, float, float, int]:
        """Turun kabul edilmiş pencerelerinden "sormaya değer mi" adayını çıkar.

        Seçim SKOR AĞIRLIKLI: kosinüs skorları negatif olabileceği için ham skor
        ağırlık olarak kullanılamaz (negatif ağırlık çoğunluğu ters çevirirdi);
        turun en düşük skoruna göre kaydırılmış pozitif ağırlık kullanılır.

        `oran` ise BİLEREK sayım tabanlıdır: eşik ("kabul edilen pencerelerin ≥ %60'ı
        aynı adayı gösteriyor") ölçülebilir ve log'dan doğrulanabilir kalsın.
        `pencere_sayısı` yalnız GÜNCEL pencereleri sayar (transkript anına
        `turn_max_seconds` içinde) — bayat kanıtla soru sorulmasın.

        Yan etki: `last_turn_candidate_windows` doldurulur (skora göre azalan).
        """
        self.last_turn_candidate_windows = []
        if not accepted:
            return None, 0.0, 0.0, 0
        s_min = min(item[2] for item in accepted)
        weights: dict[str, float] = {}
        counts: dict[str, int] = {}
        for _at, name, score, _margin, _emb in accepted:
            key = str(name)
            weights[key] = weights.get(key, 0.0) + (score - s_min + 1.0)
            counts[key] = counts.get(key, 0) + 1
        cand = max(weights, key=lambda k: (weights[k], counts[k]))
        windows = [item for item in accepted if item[1] == cand]
        fresh = [
            item for item in windows
            if finished_at - item[0] <= self.turn_max_seconds
        ]
        ratio = len(windows) / len(accepted)
        avg_score = sum(item[2] for item in windows) / len(windows)
        self.last_turn_candidate_windows = [
            (item[2], item[4])
            for item in sorted(fresh, key=lambda x: x[2], reverse=True)
            if item[4] is not None
        ]
        return cand, ratio, avg_score, len(fresh)

    def observe(
        self,
        name: str | None,
        score: float,
        *,
        margin: float = 0.0,
        capture_ok: bool = True,
        now: float | None = None,
        embedding: object | None = None,
        generation: int | None = None,
    ) -> bool:
        """Record one identify result only when it belongs to an active user turn.

        `capture_ok=False` marks agent speech/echo and is excluded. The boolean
        return is retained for compatibility; identity changes happen only in
        `resolve_turn()`, therefore this method always returns False.

        `embedding` yalnız onay döngüsü içindir: kullanıcı "evet" derse profile
        YALNIZ bu dönüşün pencereleri yazılabilsin (bkz. resolve_turn). Pencere
        süresi tap tarafında >= SPEAKER_MIN_SECONDS garantidir.
        """
        self.score = score
        if generation is not None and generation != self._turn_generation:
            return False
        if self._turn_active and capture_ok:
            observed_at = time.monotonic() if now is None else float(now)
            if observed_at >= self._turn_started_at:
                self._turn_observations.append(
                    (observed_at, name, score, float(margin), embedding)
                )
        return False


class SpeakerTap:
    """Room'daki her uzak mikrofon track'i için bir embed/identify döngüsü sürer."""

    def __init__(self, sp: SpeakerID, state: SpeakerState, min_seconds: float = 1.5,
                 store=None, capture_gate: Callable[[], tuple[bool, str | None]] | None = None):
        self._sp = sp
        self._state = state
        # PiBrain'in wake bayraklarına bakan isteğe bağlı kalite kapısı. Bu sınıf
        # LiveKit'ten bağımsız kalır; bağlanmazsa eski güvenli varsayılan (kabul) geçer.
        self._capture_gate = capture_gate
        self._min_seconds = max(0.5, min_seconds)
        # Deneyle aynı 3 sn pencere / 1.5 sn adım kullanılır. İlk 1.5 sn'lik faydalı
        # kuyruk model tarafında sıfırla tamamlanır; ikinci kanıt 3.0 sn'de gelir.
        self._window_seconds = max(
            self._min_seconds,
            _f("SPEAKER_WINDOW_SECONDS", 3.0),
        )
        self._tasks: dict[str, asyncio.Task] = {}
        # Konuşma-kapısı: normalize [-1,1] RMS eşiği. Bunun altındaki (sessizlik/
        # kelime-arası) pencereler identify EDİLMEZ; current DEĞİŞMEZ.
        self._vad_rms = _f("SPEAKER_VAD_RMS", 0.008)
        # Artımlı öğrenme (opsiyonel, default KAPALI): YÜKSEK güvenle tanınan
        # pencerelerden ara sıra örnek ekleyip centroid'i güçlendir. Az örnekli
        # centroid'in başka gün/mikrofonda eşiğin altına düşmesine karşı.
        self._store = store if _b("SPEAKER_LEARN_ENABLED", False) else None
        self._learn_min = _f("SPEAKER_LEARN_MIN_SCORE", 0.60)
        self._learn_max_add = _i("SPEAKER_LEARN_MAX_PER_SESSION", 2)
        self._learn_cooldown = _f("SPEAKER_LEARN_COOLDOWN_S", 60.0)
        # Kişi başına KALICI tavan. Oturum sayacı tek başına yetmez: LiveKit her oda
        # oturumunda yeni bir job süreci açar → `_learned` sıfırlanır, tavan hiç dolmaz.
        # Canlı DB'de tam olarak bu oldu: ~55 oturum × 2 = 109 auto-learn örnek.
        self._learn_max_total = _i("SPEAKER_LEARN_MAX_TOTAL", 20)
        if self._store is not None and self._learn_max_total <= 0:
            self._store = None  # tavan 0 = auto-learn kapalı
        self._learned = 0
        self._last_learn = 0.0
        # Gölge embedding kaydedici (salt gözlem, varsayılan AÇIK, .env ile kapatılır).
        self._emb_log = SessionEmbLog(sp.model_id)
        # Turlar arası akış hiç oluşmazsa (arka arkaya konuşma) tamponu boşaltacak
        # emniyet valfi. 1 pencere/sn ile bu ~8 dk kesintisiz tek tur demektir.
        self._emb_log_max_pending = _i("SPEAKER_EMB_LOG_FLUSH_EVERY", 512)
        # Prozodi hesabı ayrı kolla kapatılabilir (varsayılan AÇIK). Ölçüldü:
        # Prozodi yalnız gözlem içindir ve gömme kararına girmez.
        self._prosody_enabled = _b("SPEAKER_EMB_LOG_PROSODY", True)
        # Çok mikrofonlu odada pencereleri ayırmak için track başına küçük sayı.
        # Sonradan TÜRETİLEMEZ: npz'de track kimliği hiç yoktu.
        self._track_ids: dict[str, int] = {}

    async def _maybe_learn(self, name: str, emb) -> None:
        """Güvenli tanımada örnek ekle (kapalıysa / kota dolduysa no-op).

        İki ayrı kota: `_learned` oturum-içi hız sınırı (bir oturum centroid'i tek
        başına domine etmesin), `_learn_max_total` ise DB'ye dayalı kalıcı tavan.
        Tavanı store uygular (insert+budama atomik) — burada sayıp orada eklemek
        eşzamanlı job'larda yarış olurdu.
        """
        if self._store is None or self._learned >= self._learn_max_add:
            return
        now = time.monotonic()
        if self._last_learn and (now - self._last_learn) < self._learn_cooldown:
            return
        sid = self._sp.id_for(name)
        if sid is None:
            return
        self._last_learn = now
        self._learned += 1
        try:
            _, dropped = await self._store.add_auto_learn_sample(
                sid, emb_to_bytes(emb), self._sp.dim, self._sp.model_id,
                self._learn_max_total,
            )
            self._sp.reload(await self._store.all_speaker_embeddings())
            log.info(
                "speaker-tap: %r için örnek eklendi (auto-learn, tavan=%d, atılan=%d)",
                name, self._learn_max_total, dropped,
            )
        except Exception as e:  # noqa: BLE001 — öğrenme asla akışı bozmasın
            log.debug("auto-learn hata: %s", e)

    def attach(self, room: rtc.Room) -> None:
        """Track subscribe olaylarını dinle; mevcut abonelikleri de yakala."""
        room.on("track_subscribed", self._on_track_subscribed)
        # AgentSession zaten abone olmuş olabilir → mevcut track'leri tara.
        for participant in list(room.remote_participants.values()):
            for pub in list(participant.track_publications.values()):
                track = getattr(pub, "track", None)
                if track is not None and pub.kind == rtc.TrackKind.KIND_AUDIO:
                    self._spawn(track, participant)

    def set_capture_gate(self, gate: Callable[[], tuple[bool, str | None]] | None) -> None:
        """Enrollment kalite kapısını sonradan bağla.

        Agent, SpeakerTap'i PiBrain'den önce kurar; bu küçük setter iki bileşeni
        döngüsel bağımlılık yaratmadan birleştirir.
        """
        self._capture_gate = gate

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
        hop_bytes = int(self._min_seconds * TAP_RATE) * 2  # bayt (s16le mono)
        window_bytes = int(self._window_seconds * TAP_RATE) * 2
        # İlk kanıt `min_seconds` dolunca, sonraki kanıtlar aynı adımla çıkar.
        # ReDimNet2 kısa ilk kuyruğu kendi 3 sn penceresine sıfırla tamamlar.
        first_bytes = min(window_bytes, hop_bytes)
        buf = bytearray()
        bytes_since_window = 0
        seen_turn_generation = self._state.turn_generation
        track_id = self._track_ids.setdefault(key, len(self._track_ids))
        noted_turn = -1
        log.info("speaker-tap: track dinleniyor (%s)", key)
        try:
            async for event in stream:
                payload = bytes(event.frame.data)
                if not payload:
                    continue
                turn_generation = self._state.turn_generation
                if turn_generation != seen_turn_generation:
                    # Yeni kullanıcı dönüşü: önceki agent sesi / eski konuşmacı
                    # pencereye karışmasın.
                    seen_turn_generation = turn_generation
                    buf = bytearray()
                    bytes_since_window = 0
                if not self._state.turn_active:
                    # TURLAR ARASI = yazım için doğru an: kanıt toplanmıyor,
                    # gecikmesi konuşmaya yansıyacak hiçbir iş yok.
                    # Tur burada KAPALI olduğuna göre `resolve_turn()` çalışmış ve
                    # `last_turn_decision` bu üretim numarasına aittir — kararı
                    # pencerelere eşlemek için tek ihtiyacımız olan an bu.
                    if turn_generation != noted_turn:
                        noted_turn = turn_generation
                        self._emb_log.note_turn_decision(
                            turn_generation, self._state.last_turn_decision
                        )
                    await self._emb_log.maybe_flush()
                    continue
                buf.extend(payload)
                if len(buf) > window_bytes:
                    del buf[:-window_bytes]
                bytes_since_window += len(payload)
                if len(buf) < first_bytes or bytes_since_window < hop_bytes:
                    continue
                chunk = bytes(buf)  # kayan pencere: her ~min_seconds bir örnek
                bytes_since_window = 0
                try:
                    samples = pcm_to_f32(chunk, width=2, channels=TAP_CHANNELS)
                    # Konuşma-kapısı: düşük-enerji (sessizlik) pencerelerini ATLA.
                    # identify çağırma, current'ı değiştirme → sessizlik "unknown"
                    # üretmez, yapışkan state bozulmaz.
                    rms = float(math.sqrt(float((samples * samples).mean()))) if samples.size else 0.0
                    if rms < self._vad_rms:
                        log.debug("speaker-tap: sessiz pencere atlandı (rms=%.4f)", rms)
                        continue
                    # AKIŞ: pencerenin turun başlangıcına göre ofseti. EMBED'DEN
                    # ÖNCE alınır — embed'in 20-40 ms'i ofsete karışmasın.
                    window_at = time.monotonic()
                    observation_generation = self._state.turn_generation
                    tracked = self._state.begin_observation(observation_generation)
                    if not tracked:
                        continue
                    try:
                        emb = await asyncio.to_thread(
                            self._sp.embed_samples, samples, TAP_RATE
                        )
                    # Enrollment için son ham embedding'i sakla (yalnızca KONUŞMA
                    # penceresi → sessizlik yanlış-pozitif enroll tetiklemez).
                        self._state.last_embedding = emb
                        if self._capture_gate is None:
                            self._state.last_embedding_capture_ok = None
                            self._state.last_embedding_capture_reason = None
                        else:
                            try:
                                ok, reason = self._capture_gate()
                            except Exception as e:  # noqa: BLE001 — kalite bilgisi akışı bozmasın
                                log.debug("speaker-tap capture gate hata: %s", e)
                                ok, reason = True, None
                            self._state.last_embedding_capture_ok = bool(ok)
                            self._state.last_embedding_capture_reason = reason
                        self._state.add_expression_window(chunk, emb)
                        name, score = self._sp.identify(emb)
                    # Sıralama identify'nin HEMEN ardından, arada await olmadan
                    # okunur — başka bir coroutine araya giremez.
                        ranking = list(getattr(self._sp, "last_ranking_top2", None) or [])
                        margin = (
                            float(ranking[0][1] - ranking[1][1])
                            if len(ranking) >= 2
                            else 0.0
                        )
                        # Karar için gerekli gözlem auto-learn/prosody'den ÖNCE
                        # yazılır; final STT yalnız bu noktayı bekler.
                        self._state.observe(
                            name,
                            score,
                            margin=margin,
                            capture_ok=self._state.last_embedding_capture_ok is not False,
                            embedding=emb,
                            generation=observation_generation,
                        )
                    finally:
                        self._state.finish_observation(observation_generation)
                except Exception as e:  # noqa: BLE001
                    log.debug("speaker-tap embed/identify hata: %s", e)
                    continue
                # Artımlı öğrenme (default kapalı): yüksek güvenli tanımada centroid'i besle.
                if name is not None and score >= self._learn_min:
                    await self._maybe_learn(name, emb)
                # `observe`'un GÖRDÜĞÜ değer (arada await yok) — gölge kayda aynısı.
                capture_ok_flag = self._state.last_embedding_capture_ok
                # GÖLGE KAYIT — salt gözlem, KARARDAN SONRA. Reddedilen (name=None)
                # pencereler de yazılır; hızlı yolun ürettiği vektör TEKRAR EMBED
                # EDİLMEDEN kullanılır ve bu satır diske dokunmaz (yalnız RAM).
                # Prozodi `to_thread`'de: olay döngüsü ~1 ms bile bloklanmasın ve
                # `observe()`'un zaman damgası bu hesaptan ETKİLENMESİN.
                features = None
                if self._prosody_enabled and self._emb_log.enabled:
                    features = await asyncio.to_thread(
                        prosody.window_features, samples, TAP_RATE
                    )
                self._emb_log.add(
                    turn=self._state.turn_generation,
                    embedding=emb,
                    decided=name,
                    ranking=ranking,
                    window_seconds=len(chunk) / 2.0 / TAP_RATE,
                    rms=rms,
                    t_rel=window_at - self._state.turn_started_at,
                    track_id=track_id,
                    capture_ok=-1 if capture_ok_flag is None else int(bool(capture_ok_flag)),
                    features=features,
                )
                if self._emb_log.pending >= self._emb_log_max_pending:
                    await self._emb_log.maybe_flush()
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
        # Son turun kararı turlar-arası akış hiç gelmeden kapanışa denk gelebilir.
        # Tur HÂLÂ açıksa karar yok demektir — yarım karar yazmayız.
        if not self._state.turn_active:
            self._emb_log.note_turn_decision(
                self._state.turn_generation, self._state.last_turn_decision
            )
        await self._emb_log.maybe_flush()  # oturumun son pencereleri kaybolmasın
