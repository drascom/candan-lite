"""candan-lite voice worker — livekit-agents AgentSession.

Ağır adapter.py'ın yerine ince worker: VAD/turn-detect/barge-in framework'ten;
sadece STT (Whisper wyoming) ve TTS (OmniVoice) custom plugin.
Beyin = pi CLI, warm `--mode rpc` alt-süreci (worker/pi_brain.py, docs/pi-brain-design.md).

Çalıştırma (dev): python agent.py dev
Oda: MATE_LIVEKIT_ROOM (candan-lite-dev)
"""
import asyncio
import logging
import os
from pathlib import Path
from dotenv import load_dotenv
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import silero

from pi_brain import PiBrain, WAKE_ENABLED   # warm pi --mode rpc beyni + wake gate
from whisper_stt import WhisperWyomingSTT    # Wyoming (faster-whisper) STT plugin
from moss_stt import MossSTT                 # MOSS-Transcribe-Diarize STT (alternatif)
from omnivoice_tts import OmniVoiceTTS       # OmniVoice WS TTS plugin
from speaker_id import build_speaker_id, SpeakerStore  # Faz 3: speaker-ID (opsiyonel)
from speaker_tap import SpeakerState, SpeakerTap       # paralel speaker tap
from wake_stt import WakeSTT                            # paralel erken-wake dinleyici (opsiyonel)
from reminders import (                                 # proaktif ajan (hatırlatma/olay)
    HEARTBEAT_SECONDS, Deliverer, EventStore,
)

# worker/.env (gitignored) — cwd'den bağımsız, dosya konumuna göre yükle.
load_dotenv(Path(__file__).resolve().parent / ".env")

STT_HOST = os.environ.get("STT_HOST", "192.168.0.25")
STT_PORT = int(os.environ.get("STT_PORT", "10300"))
TTS_HOST = os.environ.get("TTS_HOST", "192.168.0.25")
TTS_PORT = int(os.environ.get("TTS_PORT", "8808"))
LANG = os.environ.get("MATE_LANGUAGE", "tr")

# STT backend seçimi: wyoming (varsayılan, mevcut Wyoming/faster-whisper davranışı
# birebir) | moss (MOSS-Transcribe-Diarize HTTP servisi, .25:8909). Kapalı/tanımsızsa
# wyoming. MOSS_STT_URL sadece backend=moss iken kullanılır.
STT_BACKEND = os.environ.get("STT_BACKEND", "wyoming").strip().lower()
MOSS_STT_URL = os.environ.get("MOSS_STT_URL", "http://192.168.0.25:8909")


def _build_stt():
    """STT_BACKEND'e göre STT plugin'i seç (varsayılan wyoming = mevcut davranış)."""
    log = logging.getLogger("worker.agent")
    if STT_BACKEND == "moss":
        log.info("STT backend = moss (%s), dil=%s", MOSS_STT_URL, LANG)
        return MossSTT(url=MOSS_STT_URL, language=LANG)
    log.info("STT backend = wyoming (%s:%s), dil=%s", STT_HOST, STT_PORT, LANG)
    return WhisperWyomingSTT(host=STT_HOST, port=STT_PORT, language=LANG)

# Beyin: pi CLI, warm `--mode rpc` alt-süreci (HTTP /v1 YOK). Persona env ile seçilir.
PI_PERSONA = os.environ.get("PI_DEFAULT_PERSONA", "candan")
SPEAKER_MIN_S = float(os.environ.get("SPEAKER_MIN_SECONDS", "1.0") or 1.0)
# Yapışkanlık: art arda kaç güvensiz pencereden sonra current unknown'a düşsün.
SPEAKER_STICKY_MISSES = int(float(os.environ.get("SPEAKER_STICKY_MISSES", "5") or 5))

# Paralel erken-wake dinleyici (opsiyonel, additive). Kapalıyken davranış AYNI.
def _envflag(name: str, default: bool = False) -> bool:
    return (os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on"))

WAKE_STT_ENABLED = _envflag("WAKE_STT_ENABLED", False)
WAKE_STT_WINDOW = float(os.environ.get("WAKE_STT_WINDOW", "1.5") or 1.5)
# Segmentin yalnız İLK bu kadar saniyesi wake taramasına girer (wake kelimesi cümle
# başında söylenir; gerisini Whisper'a vermek saf GPU israfı).
WAKE_STT_MAX_SECONDS = float(os.environ.get("WAKE_STT_MAX_SECONDS", "4.0") or 4.0)
WAKE_WORD = os.environ.get("WAKE_WORD", "candan")

# Uyurken kullanıcı transkriptini web UI'a YAYINLAMA (DEFAULT açık). Ses/STT/wake
# boru hattı AYNEN çalışır (wake eşleşmesi + konu bildirimi transcript'i görür); sadece
# UI'a giden "kullanıcı ne dedi" yayını uykuda susturulur. false → eski davranış
# (uyurken de UI'a yazılır; gizleme yalnız web tarafında `candan.awake` ile yapılır).
SLEEP_TRANSCRIPTS_HIDDEN = _envflag("SLEEP_TRANSCRIPTS_HIDDEN", True)

# Log gürültüsü: livekit-agents 'dev' modu varsayılan olarak DEBUG basar
# (worker.py _default_log_level dev_default="DEBUG") → speaker-tap/wake_stt gibi
# her pencerede/chunk'ta basılan debug loglar sürekli akar. Varsayılanı INFO'ya
# çekiyoruz; ham/eski (DEBUG + dedupe kapalı) davranış WORKER_VERBOSE_LOGS=true
# ile geri gelir (bkz. log_utils.DedupeFilter, aynı bayrağı okur).
WORKER_VERBOSE_LOGS = _envflag("WORKER_VERBOSE_LOGS", False)
WORKER_LOG_LEVEL = "DEBUG" if WORKER_VERBOSE_LOGS else os.environ.get("WORKER_LOG_LEVEL", "INFO")

# Explicit agent dispatch. agent_name VERİLMEZSE LiveKit otomatik dispatch yapar; ama
# hem otomatik dispatch hem token'a gömülü dispatch YALNIZCA oda İLK OLUŞTURULURKEN
# işlenir; oda zaten varsa yok sayılır. Oda adımız sabit (candan-lite-dev) olduğu için,
# oda yaşarken worker restart edilince agent odaya bir daha giremiyordu ("registered
# worker" yazar, iş gelmez) — yarış koşulu buydu. agent_name verince worker SADECE
# açıkça çağrılınca iş alır; web token route'u VAR OLAN oda için `AgentDispatchClient.
# createDispatch` ile bu ADI açıkça dispatch eder (web/app/api/token/route.ts:
# ensureAgentDispatch, web/lib/agent-name.ts — aynı ad!).
AGENT_NAME = os.environ.get("LIVEKIT_AGENT_NAME") or os.environ.get("AGENT_NAME") or "candan"


def _install_sleep_transcript_gate(session, brain) -> None:
    """Uyurken KULLANICI transkriptinin web UI'a yayınını bastır (ses/STT/wake AYNEN sürer).

    Mekanizma (livekit-agents 1.6.5, voice/room_io): RoomIO, `user_input_transcribed`
    olaylarını arka plan task'ında (`_forward_user_transcript`) tek bir yayın noktasından
    — `_user_tr_output.capture_text()` — odaya (web transkript stream'i) basar. Bu tek
    noktayı brain UYKUDAYKEN no-op'a çeviriyoruz.

    Neden BOZMAZ:
    - Session olayları (agent.py'deki wake_now / konu bildirimi / proaktif reply_seen)
      AYNI emit'ten BAĞIMSIZ, doğrudan `session.on(...)` ile beslenir → susturma onları
      görmez. Wake eşleşmesi + konu bildirimi çalışmaya devam eder.
    - capture_text yayınlanmazsa alt-çıkışlar `_capturing=False` kalır → `flush()` da
      no-op olur (yarım/partial metin sızmaz).
    - AGENT metnini (TranscriptSynchronizer / `session.output.transcription`) HİÇ ELLEMEZ;
      bu ayrı bir nesne. Eski uyarı (agent output toggle'ı sync'i bozar) hâlâ geçerli,
      biz USER çıkışına dokunuyoruz.

    Wake cümlesi GÖRÜNÜR kalır: EventEmitter.emit SENKRON ve kayıt sırasıyla çağırır;
    agent.py'nin `_on_transcript`'i (wake_now → awake=True) `session.start`'tan SONRA
    kaydedildiği için RoomIO handler'ından SONRA ama capture_text'i işleyen arka-plan
    task'ından ÖNCE çalışır → wake cümlesi işlendiğinde awake=True olur, yayınlanır."""
    log = logging.getLogger("worker.agent")
    try:
        rio = session.room_io
    except Exception:  # noqa: BLE001 — room'suz start (olmamalı) → gate kurma
        log.warning("uyku-transkript gate: room_io yok, atlanıyor")
        return
    out = getattr(rio, "_user_tr_output", None)
    if out is None or not callable(getattr(out, "capture_text", None)):
        log.warning("uyku-transkript gate kurulamadı: _user_tr_output yok/uyumsuz")
        return
    orig_capture = out.capture_text
    wake = getattr(brain, "_wake", None)

    async def _gated_capture(text: str) -> None:
        # Uyurken (awake False) kullanıcı metnini web'e yayınlama. wake yoksa (beklenmez)
        # eski davranış = yayınla.
        if wake is not None and not getattr(wake, "awake", True):
            return
        await orig_capture(text)

    out.capture_text = _gated_capture  # instance-attr bound method'u gölgeler
    log.info("uyku-transkript gate aktif (SLEEP_TRANSCRIPTS_HIDDEN=true)")


async def entrypoint(ctx: JobContext):
    await ctx.connect()

    # --- Faz 3: speaker-ID (opsiyonel, additive) ---
    # SPEAKER_ID_ENABLED kapalı / model yok / sherpa-onnx yok ise sp=None kalır ve
    # davranış Faz 2 ile AYNI olur (tek persona candan, tek warm süreç).
    sp = build_speaker_id()
    speaker_state: SpeakerState | None = None
    tap: SpeakerTap | None = None
    store: SpeakerStore | None = None
    if sp is not None:
        try:
            store = SpeakerStore()
            sp.reload(await store.all_speaker_embeddings())  # enrolled kişileri yükle
            speaker_state = SpeakerState(sticky_misses=SPEAKER_STICKY_MISSES)
            tap = SpeakerTap(sp, speaker_state, min_seconds=SPEAKER_MIN_S, store=store)
        except Exception as e:  # noqa: BLE001 — speaker-ID hiç kurulamazsa Faz 2'ye düş
            logging.getLogger("worker.agent").warning("speaker-ID kurulamadı: %r", e)
            speaker_state = None
            tap = None
            store = None

    # Beyin (warm pi). Hafıza Faz B: oturum kapanışında finalize() ile kalıcı
    # maddeleri kaydettir (best-effort, kapanışı bloklamaz).
    brain = PiBrain(
        persona=PI_PERSONA,
        speaker_state=speaker_state,
        speaker_id=sp if speaker_state is not None else None,
        speaker_store=store if speaker_state is not None else None,
    )

    async def _finalize_memory() -> None:
        try:
            await brain.finalize()
        except Exception:  # noqa: BLE001 — kapanış hiçbir koşulda bloklanmaz
            pass

    ctx.add_shutdown_callback(_finalize_memory)

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=_build_stt(),  # STT_BACKEND: wyoming (varsayılan) | moss
        # Faz 3.1: sesli oto-enrollment — bilinmeyen ses gelince PiBrain isim sorar,
        # onaylanınca sp/store ile kaydeder (speaker_state None ise devre dışı).
        llm=brain,
        tts=OmniVoiceTTS(host=TTS_HOST, port=TTS_PORT),
        # turn_detection: framework multilingual model (Faz 3) — şimdilik VAD tabanlı
    )

    await session.start(
        agent=Agent(instructions="Sen Candan'sın. Türkçe, kısa ve yardımcı konuş."),
        room=ctx.room,
    )
    # Uyurken kullanıcı transkriptini web UI'a YAYINLAMA (default açık). Ses/STT/wake
    # boru hattı AYNEN çalışır; sadece RoomIO'nun user-transkript yayını uykuda susar.
    # WAKE kapalıysa hep uyanık → gate zaten no-op olurdu; kurma.
    if WAKE_ENABLED and SLEEP_TRANSCRIPTS_HIDDEN:
        _install_sleep_transcript_gate(session, brain)

    # STT'den BAĞIMSIZ paralel speaker tap'i room'a bağla (mic track → embed/identify).
    if tap is not None:
        tap.attach(ctx.room)

    # Paralel erken-wake dinleyici (opsiyonel, default KAPALI). Açıksa mic track'e
    # ayrı bir VAD+Whisper penceresi bağlar; "candan" duyulunca brain.wake_now() →
    # çan HEMEN çalar (ana STT tüm cümleyi beklemeden). Ana wake/iki-adım akışını
    # BOZMAZ (wake_now idempotent). Verimlilik: sadece UYURKEN transcribe eder.
    wake_stt: WakeSTT | None = None
    if WAKE_STT_ENABLED:
        wake_stt = WakeSTT(
            vad=silero.VAD.load(),  # ana session'dan ayrı, bağımsız stream
            stt_host=STT_HOST,
            stt_port=STT_PORT,
            language=LANG,
            wake_word=WAKE_WORD,
            window=WAKE_STT_WINDOW,
            max_seconds=WAKE_STT_MAX_SECONDS,
            on_wake=lambda text: brain.wake_now(text),  # idempotent → çift çan yok
            # Sadece uyurken çalış: uyanıkken ana STT yeterli, çift-transcribe azalır.
            active=lambda: not getattr(getattr(brain, "_wake", None), "awake", True),
        )
        wake_stt.attach(ctx.room)
        ctx.add_shutdown_callback(wake_stt.aclose)

    # Wake durumunu web'e sinyalle: local participant attribute `candan.awake`.
    # NOT: AGENT metnini worker'da toggle ETME — session.output.set_transcription_enabled
    # TranscriptSynchronizer'ı detach edip agent metnini bozuyor. USER (kullanıcı)
    # transkripti uyurken _install_sleep_transcript_gate ile RoomIO'nun ayrı user-çıkışında
    # (agent output DEĞİL) susturulur; web tarafı ayrıca `candan.awake` ile de gizler.
    # asyncio, task'lara sadece ZAYIF referans tutar: create_task'ın dönüşünü
    # tutmazsak GC task'ı iş bitmeden toplayabilir → attribute sessizce gitmez
    # (çan çalmaz). Güçlü referansı burada tutup bitince bırakıyoruz.
    bg_tasks: set[asyncio.Task] = set()

    def _apply_wake_state(awake: bool) -> None:
        """Uyku/uyanık durumunu web'e yayınla (attribute). Sync bağlamdan güvenli."""
        val = "true" if awake else "false"
        try:
            t = asyncio.create_task(
                ctx.room.local_participant.set_attributes({"candan.awake": val}))
        except RuntimeError:  # çalışan loop yok → atla
            return
        bg_tasks.add(t)
        t.add_done_callback(bg_tasks.discard)

    if WAKE_ENABLED:
        # Geçişleri web'e bağla; başlangıç: UYKUDA → attribute "false" + transcript kapalı.
        brain.set_wake_change(_apply_wake_state)
        _apply_wake_state(False)

        # Çanı ERKEN çal: transcript kesinleşir kesinleşmez (PiBrain turu işlenmeden ÖNCE)
        # wake word varsa brain.wake_now() → on_change → candan.awake="true" → çan.
        # Böylece çan ~0.3-0.5s daha erken çalar. Idempotent; "candan" tek başınaysa
        # PiStream tarafı zaten SILENT döner (sözlü yanıt yok).
        @session.on("user_input_transcribed")
        def _on_transcript(ev) -> None:
            # Her transcript (partial dahil) = kullanıcı konuşuyor → uyku sayacını
            # tazele. Uzun sözde pencere DOLMASIN (uyandırmaz, sadece sayacı iter).
            try:
                brain.wake_touch()
            except Exception:  # noqa: BLE001
                pass
            if not getattr(ev, "is_final", False):
                return
            try:
                brain.wake_now(getattr(ev, "transcript", "") or "")
            except Exception:  # noqa: BLE001 — sinyal hatası akışı bozmasın
                pass

        # Uyku sayacı KULLANICININ SON KONUŞMASINDAN sonra başlamalı: kullanıcı
        # konuşurken (VAD) ve asistan cevap verirken (thinking/speaking) sayaç DURUR;
        # ikisinden hangisi SONRA biterse WAKE_WINDOW_SECONDS oradan sayılır.
        @session.on("user_state_changed")
        def _on_user_state(ev) -> None:
            try:
                brain.wake_user_speaking(getattr(ev, "new_state", "") == "speaking")
            except Exception:  # noqa: BLE001
                pass

        @session.on("agent_state_changed")
        def _on_agent_state(ev) -> None:
            try:
                brain.wake_agent_busy(
                    getattr(ev, "new_state", "") in ("thinking", "speaking"))
            except Exception:  # noqa: BLE001
                pass
    else:
        # Gate yok: hep uyanık → attribute "true" + transcript açık (eski davranış) +
        # katılınca kısaca selamla.
        _apply_wake_state(True)
        await session.generate_reply(instructions="Kullanıcıyı kısaca selamla.")

    # ── Proaktif ajan: vakti gelen hatırlatmaları KENDİ BAŞLATARAK ilet ──────
    # Veriyi pi extension (family-memory) yazar → memory/events.db; sesi worker verir
    # (AgentSession sadece burada). Sözleşme = paylaşılan SQLite dosyası.
    reply_seen = asyncio.Event()   # proaktif seslenmeye kullanıcı yanıt verdi mi

    @session.on("user_input_transcribed")
    def _on_any_transcript(_ev) -> None:
        reply_seen.set()           # herhangi bir söz = onay ("efendim", "ne var", ...)

    @session.on("user_state_changed")
    def _on_user_speaking(ev) -> None:
        if getattr(ev, "new_state", "") == "speaking":
            reply_seen.set()       # VAD daha hızlı: konuşmaya başlaması bile onaydır

    class _LiveKitIO:
        """Deliverer'ın dış dünyası (reminders.ProactiveIO). Uyku/kesme/varlık kuralları
        BURADA bağlanır: pi_brain'in wake bayrakları + odadaki katılımcılar."""

        def present(self) -> bool:
            return bool(ctx.room.remote_participants)   # kullanıcı odada yoksa SESLENME

        def busy(self) -> bool:
            return brain.busy()                         # konuşuyor/cevaplıyor → ERTELE

        def display_name(self, user: str) -> str:
            return brain.display_name(user)

        def set_busy(self, v: bool) -> None:
            brain.wake_agent_busy(v)                    # uykudayken de seslen; sayaç dursun

        def hold(self, v: bool) -> None:
            brain.proactive_hold(v)                     # onay sözü pi'ya gitmesin

        def wake(self) -> bool:
            # Seslendiğimiz AN pencereyi aç: konuşmayı BİZ başlattık, cevabı ('efendim')
            # duymak için kullanıcının ayrıca "candan" demesi GEREKMEZ. (brain.wake_now()
            # burada YANLIŞTI: o metinde wake word arar → boş metinle sessiz no-op.)
            return brain.proactive_wake()

        def sleep(self) -> None:
            brain.proactive_sleep()                     # cevap yok → uyandırmayı geri al

        async def say(self, text: str) -> None:
            await session.say(text)                     # SpeechHandle → playout'u bekler

        async def wait_reply(self, timeout: float) -> bool:
            reply_seen.clear()
            try:
                await asyncio.wait_for(reply_seen.wait(), timeout)
                return True
            except asyncio.TimeoutError:
                return False

    # NOT: adı `store` DEĞİL — yukarıdaki `store` SpeakerStore'dur (satır 77). Aynı
    # fonksiyonda tek adı iki farklı tipe bağlamak, `logging` tuzağının kardeşidir:
    # bugün zararsız (SpeakerStore kullanımları bu satırın üstünde bitiyor), yarın
    # araya kod girince sessizce yanlış nesneye gider.
    event_store = EventStore()
    deliverer = Deliverer(event_store, _LiveKitIO())
    log = logging.getLogger("worker.proactive")

    async def _heartbeat() -> None:
        """Periyodik tick: vakti gelen olayları ilet + (sessizken) konsolidasyon."""
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_SECONDS)
                try:
                    user = brain.current_user()          # guest/unknown → '' → iş yok
                    if not user:
                        continue
                    await deliverer.tick(user)
                    await brain.consolidate_if_needed()  # busy/uyanıkken kendi atlar
                except Exception:  # noqa: BLE001 — tek tur hatası döngüyü öldürmesin
                    log.warning("heartbeat tick hatası", exc_info=True)
        except asyncio.CancelledError:
            pass

    hb = asyncio.create_task(_heartbeat())

    async def _stop_hb() -> None:
        hb.cancel()
        event_store.close()

    ctx.add_shutdown_callback(_stop_hb)


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name=AGENT_NAME,  # explicit dispatch — web token'ı bu adı çağırır
            log_level=WORKER_LOG_LEVEL,
        )
    )
