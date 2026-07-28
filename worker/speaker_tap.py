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
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from livekit import rtc

from speaker_id import SpeakerID, emb_to_bytes, pcm_to_f32
from log_utils import DedupeFilter

log = logging.getLogger("worker.speaker_tap")
log.addFilter(DedupeFilter())  # "sessiz pencere atlandı" vb. tekrarları seyreltir

TAP_RATE = 16000  # sherpa 16k dışını içeride resample eder; 16k besliyoruz
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
    """

    def __init__(self) -> None:
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
        self._rows: list[tuple] = []
        self._dim: int | None = None
        self._pending = 0  # henüz diske yazılmamış satır sayısı
        self._swept = False
        self._dropped = 0
        if self.enabled:
            log.info("gölge embedding kaydı: %s (TTL %.0f gün)", self.path, self._ttl_days)

    @property
    def pending(self) -> int:
        return self._pending

    def add(
        self,
        *,
        turn: int,
        embedding,
        decided: str | None,
        ranking: list[tuple[str, float]],
        window_seconds: float,
        rms: float,
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
            ))
            self._pending += 1
        except Exception as e:  # noqa: BLE001 — ölçüm ASLA akışı bozmasın
            log.debug("gölge embedding kaydı atlandı: %s", e)

    async def maybe_flush(self) -> None:
        """Bekleyen satır varsa dosyayı yeniden yaz (thread'de, olay döngüsü serbest)."""
        if not self.enabled or self._pending <= 0:
            return
        rows = list(self._rows)  # anlık görüntü: `add` paralel ekleyebilir
        self._pending = 0
        try:
            await asyncio.to_thread(self._write, rows)
        except Exception as e:  # noqa: BLE001
            log.debug("gölge embedding yazımı başarısız: %s", e)

    def _write(self, rows: list[tuple]) -> None:
        """Thread içinde çalışır. Atomik: geçici dosya + `os.replace`."""
        if not rows:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        if not self._swept:
            self._swept = True
            self._sweep()
        tmp = self.dir / f".{self.session_id}.tmp.npz"
        with open(tmp, "wb") as f:
            np.savez(
                f,
                ts=np.array([r[0] for r in rows], dtype=np.float64),
                turn=np.array([r[1] for r in rows], dtype=np.int32),
                emb=np.stack([r[2] for r in rows]),
                best_name=np.array([r[3] for r in rows]),
                best_score=np.array([r[4] for r in rows], dtype=np.float32),
                second_name=np.array([r[5] for r in rows]),
                second_score=np.array([r[6] for r in rows], dtype=np.float32),
                window_seconds=np.array([r[7] for r in rows], dtype=np.float32),
                rms=np.array([r[8] for r in rows], dtype=np.float32),
                decided=np.array([r[9] for r in rows]),
            )
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
    ) -> None:
        self.current: str | None = None
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
        # yabancı bir sesin tek penceresi AS-norm eşiğini geçip doğrudan Ayhan olması
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
        self._last_confirmed_name: str | None = None
        self._last_confirmed_at = 0.0
        self._turn_active = False
        self._turn_started_at = 0.0
        # Her yeni kullanıcı dönüşü bir üretim numarası alır. SpeakerTap bu sayıyı
        # izleyerek önceki konuşmacı/Candan sesiyle dolu ses tamponunu anında atar.
        # Böylece kayan pencere hiçbir zaman yeni STT dönüşünden ÖNCEKİ sesin
        # embedding'ini üretmez.
        self._turn_generation = 0
        # (zaman, isim, skor, embedding) — embedding onay döngüsü için taşınır
        # (bkz. `last_turn_candidate_windows`); yoksa None.
        self._turn_observations: list[tuple[float, str | None, float, object | None]] = []
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
        self.score = 0.0
        self._candidate = None
        self._candidate_hits = 0
        self._misses = 0

    @property
    def turn_active(self) -> bool:
        """Whether a user turn is currently collecting speaker evidence."""
        return self._turn_active

    @property
    def turn_generation(self) -> int:
        """Monotonic token used by the audio tap to discard pre-turn audio."""
        return self._turn_generation

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
            best_run: list[tuple[float, str | None, float]] = []
            run: list[tuple[float, str | None, float]] = []
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
        self, run: list[tuple[float, str | None, float, object | None]]
    ) -> list[tuple[float, str | None, float, object | None]]:
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
        accepted: list[tuple[float, str | None, float, object | None]],
        finished_at: float,
    ) -> tuple[str | None, float, float, int]:
        """Turun kabul edilmiş pencerelerinden "sormaya değer mi" adayını çıkar.

        Seçim SKOR AĞIRLIKLI: skorlar AS-norm ölçeğinde negatif olabildiği için ham
        skor ağırlık olarak kullanılamaz (negatif ağırlık çoğunluğu ters çevirirdi);
        turun en düşük skoruna göre kaydırılmış pozitif ağırlık (w = s - s_min + 1)
        kullanılır — monoton, tüm skorlar eşitse saf pencere sayımına indirgenir.

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
        for _at, name, score, _emb in accepted:
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
            (item[2], item[3])
            for item in sorted(fresh, key=lambda x: x[2], reverse=True)
            if item[3] is not None
        ]
        return cand, ratio, avg_score, len(fresh)

    def observe(
        self,
        name: str | None,
        score: float,
        *,
        capture_ok: bool = True,
        now: float | None = None,
        embedding: object | None = None,
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
        if self._turn_active and capture_ok:
            observed_at = time.monotonic() if now is None else float(now)
            if observed_at >= self._turn_started_at:
                self._turn_observations.append((observed_at, name, score, embedding))
        return False


class SpeakerTap:
    """Room'daki her uzak mikrofon track'i için bir embed/identify döngüsü sürer."""

    def __init__(self, sp: SpeakerID, state: SpeakerState, min_seconds: float = 1.0,
                 store=None, capture_gate: Callable[[], tuple[bool, str | None]] | None = None):
        self._sp = sp
        self._state = state
        # PiBrain'in wake bayraklarına bakan isteğe bağlı kalite kapısı. Bu sınıf
        # LiveKit'ten bağımsız kalır; bağlanmazsa eski güvenli varsayılan (kabul) geçer.
        self._capture_gate = capture_gate
        self._min_seconds = max(0.5, min_seconds)
        # Embedding modelleri tek kelimelik 1 sn pencerelerde kararsız kalabiliyor.
        # Her `min_seconds`'ta bir, güncel dönüşe ait son 1.5 sn'i değerlendiririz.
        # İlk pencere `min_seconds`'ta çıkar (bkz. `_consume`), sonrakiler kayan
        # 1.5 sn'dir → ~2 sn'lik doğal bir cümlede iki ayrı kanıt penceresi oluşur;
        # tek kısa söz hâlâ tek pencereyle kimlik alamaz.
        self._window_seconds = max(
            self._min_seconds,
            _f("SPEAKER_WINDOW_SECONDS", 1.5),
        )
        self._tasks: dict[str, asyncio.Task] = {}
        # Konuşma-kapısı: normalize [-1,1] RMS eşiği. Bunun altındaki (sessizlik/
        # kelime-arası) pencereler identify EDİLMEZ; current DEĞİŞMEZ.
        self._vad_rms = _f("SPEAKER_VAD_RMS", 0.01)
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
        self._emb_log = SessionEmbLog()
        # Turlar arası akış hiç oluşmazsa (arka arkaya konuşma) tamponu boşaltacak
        # emniyet valfi. 1 pencere/sn ile bu ~8 dk kesintisiz tek tur demektir.
        self._emb_log_max_pending = _i("SPEAKER_EMB_LOG_FLUSH_EVERY", 512)

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
        # İLK pencere `min_seconds` dolar dolmaz üretilir; `window_seconds` (1.5 sn)
        # dolmasını beklemek dönüş başına GECİKME demekti: ilk kanıt 1.5 sn'de,
        # ikincisi 2.5 sn'de çıkıyordu → 2 onay için 2.5 sn konuşma gerekiyordu.
        # Artık kanıtlar 1.0 / 2.0 / 3.0 sn'de çıkar (2 onay = 2.0 sn). Pencere
        # İÇERİĞİ yine kayan 1.5 sn'ye kadar büyür; yalnız ilk pencere
        # `min_seconds` uzunluğundadır — bu zaten yapılandırmadaki "en kısa kabul
        # edilebilir gömme süresi". Eşik/marj ve iki-onay kuralı aynen geçerli.
        first_bytes = min(window_bytes, hop_bytes)
        buf = bytearray()
        bytes_since_window = 0
        seen_turn_generation = self._state.turn_generation
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
                except Exception as e:  # noqa: BLE001
                    log.debug("speaker-tap embed/identify hata: %s", e)
                    continue
                # GÖLGE KAYIT — salt gözlem. Reddedilen (name=None) pencereler de
                # yazılır; hızlı yolun ürettiği vektör TEKRAR EMBED EDİLMEDEN
                # kullanılır ve bu satır diske dokunmaz (yalnız RAM tamponu).
                self._emb_log.add(
                    turn=self._state.turn_generation,
                    embedding=emb,
                    decided=name,
                    ranking=ranking,
                    window_seconds=len(chunk) / 2.0 / TAP_RATE,
                    rms=rms,
                )
                if self._emb_log.pending >= self._emb_log_max_pending:
                    await self._emb_log.maybe_flush()
                # Artımlı öğrenme (default kapalı): yüksek güvenli tanımada centroid'i besle.
                if name is not None and score >= self._learn_min:
                    await self._maybe_learn(name, emb)
                # Kimlik yalnız aktif VAD dönüşüne yazılır; agent echo penceresi
                # veya dönüş dışındaki eski gözlem sonraki transkripte taşınamaz.
                self._state.observe(
                    name,
                    score,
                    capture_ok=self._state.last_embedding_capture_ok is not False,
                    embedding=emb,
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
        await self._emb_log.maybe_flush()  # oturumun son pencereleri kaybolmasın
