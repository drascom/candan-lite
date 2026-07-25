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

KISA METİN DAYANIKLILIĞI (ölçüldü, bkz. aşağıdaki "boş çıktı" notu) ve KALIP CACHE'İ
(`tts_cache`) `_run()` içinde: kısa/kalıp metinler TAMPONLANIR (stream edilmez) →
boş çıktı görülürse tek retry yapılabilir ve sonuç cache'lenebilir. Uzun metinler
eskiden olduğu gibi doğrudan stream edilir (gecikme artmasın).

BOŞ ÇIKTI — ölçüm (2026-07-25, lokal bench, omnipick klon referansı, n=12/metin):
OmniVoice kısa metinlerde ARA SIRA sıfır uzunlukta ses üretiyor. Karakter EŞİĞİ YOK;
iki AYRI rejim var:
  • tek kelime, noktalamasız ("Tamam", "Evet", "Peki", "Olur", "Hayır") → 16/60 BOŞ.
    Aynı kelimeler noktayla → 0/60. Tetikleyici uzunluk değil, cümle sonu
    noktalamasının OLMAMASI. Nokta sesi bozmuyor (ort. süre 0.71 → 0.70 sn).
  • çok kelimeli ~25 karakter ("Elbette, hemen bakıyorum") → 2/12 noktalamasız,
    4/12 noktalı. Bu rejim noktalamadan BAĞIMSIZ → retry şart.
Bu yüzden üç katman: `_ensure_final_punct` → tek retry (`_SHORT_TEXT_RETRIES`) →
sessizlik. Kalıp cache'i (`tts_cache`) ilk başarılı üretimden sonra bu sınıfı
tamamen atlatır.

`ZeroDivisionError` OmniVoice'tan GELMİYORDU — bench harness'ının RTF hesabıydı
(süre 0 → bölme), `experiments/tts-local-bench/common.py`'de düzeltilmişti.
Production'daki gerçek tehlike şu: hiç frame push edilmezse livekit
`ChunkedStream._main_task` "no audio frames were pushed" APIError'ı atar, retry'ları
tüketir ve TURU ÖLDÜRÜR. Bu yüzden guard her yolda en az bir parça (gerekirse
`_SILENCE_MS` kadar sessizlik) push eder: sessiz kalmak kabul, çökmek DEĞİL.
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

import tts_cache
from log_utils import DedupeFilter
from trnorm import normalize_tr

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

# Boş çıktı / hata durumunda emitter'a verilen sessizlik. Sıfır frame push etmek livekit'te
# "no audio frames were pushed" APIError'ı → retry → TUR ÖLÜR. Bir parça sessizlik turu
# kurtarır; kullanıcı sadece o cümleyi duymaz.
_SILENCE_MS = 120
# Kısa/kalıp metinlerde boş çıktı stokastik → tek retry gerçek şans (ölçüm: aynı metin
# ikinci denemede döndü). Uzun metinde retry YOK: orada zaten stream ediyoruz ve
# livekit'in kendi retry'ı devrede.
_SHORT_TEXT_RETRIES = 1


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


def _has_speakable(text: str) -> bool:
    """Seslendirilecek harf/rakam var mı? "...", "?!", " - " → yok.

    Sadece noktalamayı OmniVoice'a göndermek boşuna tur (ve boş çıktı riski); guard
    bunları doğrudan sessizliğe çevirir. Voiced etiketler ([laughter] vb.) HARF içerdiği
    için buradan geçer — onlar seslendirilmeli.
    """
    return any(ch.isalnum() for ch in text)


# Cümle sonu noktalaması. Kısa metinde bunun EKSİKLİĞİ boş çıktıyı tetikliyor (ölçüm:
# 'Tamam' 3/12 boş → 'Tamam.' 0/12; 'Evet' 1/12 → 'Evet.' 0/12).
_FINAL_PUNCT = ".!?…:;"


def _ensure_final_punct(text: str) -> str:
    """Kısa metnin sonuna nokta ekle — ölçülmüş EN AZ müdahale.

    Neden: OmniVoice'un boş çıktısı uzunlukla değil, cümle sonu noktalamasının
    OLMAMASIYLA ilişkili çıktı. Nokta eklemek boş çıktıyı gözlemlenen örneklerde
    sıfırladı ve üretilen sesi bozmadı (ortalama süre 0.71 sn → 0.70 sn, yani fark yok).
    Bu yüzden (b) seçeneği: metni sesi değiştirmeyecek en küçük şekilde uzat.

    Zaten noktalamayla bitiyorsa DOKUNMAZ. Cache anahtarı bu ekten ÖNCEKİ metinden
    üretilir ("Tamam" ile "Tamam." aynı sesi verdiği hâlde ayrı anahtar tutmanın anlamı
    yok değil — anahtar sözleşmesi "normalize_tr çıktısı" olarak kalsın).
    """
    stripped = text.rstrip()
    if not stripped or stripped[-1] in _FINAL_PUNCT:
        return text
    return stripped + "."


def _silence_pcm() -> bytes:
    """`_SILENCE_MS` kadar s16le sessizlik."""
    return b"\x00\x00" * int(DEFAULT_SAMPLE_RATE * NUM_CHANNELS * _SILENCE_MS / 1000)


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
        # Emitter `initialize()` edildi mi? KENDİMİZ takip ediyoruz: `pushed_duration()`
        # asenkron güncellenen bir sayaç (push() kanala yazar, süre `_main_task`'te işlenir),
        # yani hemen ardından okumak güvenilmez. Sessizlik guard'ı bu bayrağa bakar.
        self._emitter_ready = False

    # ── Emitter yardımcıları ─────────────────────────────────────────────────
    def _emit_pcm(
        self,
        output_emitter: tts.AudioEmitter,
        pcm: bytes,
        *,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        num_channels: int = NUM_CHANNELS,
    ) -> None:
        """Hazır s16le PCM'i tek seferde emitter'a ver (cache hit / tamponlanmış sentez)."""
        self._ensure_emitter(
            output_emitter, sample_rate=sample_rate, num_channels=num_channels
        )
        output_emitter.push(pcm)
        output_emitter.flush()

    def _ensure_emitter(
        self,
        output_emitter: tts.AudioEmitter,
        *,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        num_channels: int = NUM_CHANNELS,
    ) -> None:
        """Emitter'ı bir kez başlat (ikinci `initialize()` RuntimeError verir)."""
        if self._emitter_ready:
            return
        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=sample_rate,
            num_channels=num_channels,
            mime_type="audio/pcm",
        )
        self._emitter_ready = True

    def _emit_silence(self, output_emitter: tts.AudioEmitter) -> None:
        """Turu KURTARAN sessizlik — sessiz kalmak kabul, çökmek DEĞİL.

        Emitter başlatılmamışsa başlatır, sonra her hâlükârda `_SILENCE_MS` sessizlik
        push edip flush eder. Neden push şart: livekit `_main_task` hiç frame
        görmezse "no audio frames were pushed" APIError atar, retry'ları tüketir ve
        hatayı tur akışına fırlatır. Stream ortasında hata olduysa sona eklenen 120 ms
        sessizlik duyulmaz.
        """
        try:
            self._ensure_emitter(output_emitter)
            output_emitter.push(_silence_pcm())
            output_emitter.flush()
        except Exception:  # noqa: BLE001 — guard'ın kendisi ASLA patlamasın
            logger.warning("sessizlik guard'ı emitter'a yazamadı", exc_info=True)

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        # SIFIRLA: livekit `_main_task` her deneme için YENİ bir AudioEmitter kurup
        # `_run()`'ı tekrar çağırıyor. Bayrak devreden kalırsa ikinci denemede
        # `initialize()` atlanır ve `push()` "AudioEmitter isn't started" ile patlar.
        self._emitter_ready = False

        # KONTROL işaretini yakala + metinden SİL. İşaret bu cümlede varsa mood'u
        # tur için set et; yoksa turdan devreden mood (varsa) geçerli kalır.
        mood, text = _extract_mood(self._input_text)
        if mood is not None:
            self._omni._current_mood = mood
        effective_mood = self._omni._current_mood

        # Türkçe normalizasyon — SIRA KRİTİK: mood İŞARETİ ÇIKARILDIKTAN SONRA, gönderimden
        # ÖNCE. Böylece `[mood:excited]` normalizer'a hiç ulaşmaz.
        # Neden: OmniVoice kendi normalize_text'i Türkçede yalnız çıplak tam sayıları çeviriyor;
        # `%25`, binlik ayıraçlı `3.500`, kesme ekli `14:30'da` ham kalıyordu ve hizalama
        # bozulunca kelime düşüyordu ("başlıyor"). Ölçüm (ASR geri-dönüş, 14 cümle):
        # WER %35.1 → %6.3, atlanan kelime 61 → 9. Bkz. experiments/tts-local-bench/README.md
        # SESLENDİRİLEN etiketler ([laughter], [sigh], [question-*] ...) trnorm tarafından
        # korunur — köşeli parantez içi AYNEN geçer, _OMNI_TAG_RE listesinin tamamı güvende.
        # İki yol da (WS / HTTP) AYNI normalize metni kullanır, tutarsızlık olmasın.
        text = normalize_tr(text)

        # ── Guard 1: söylenecek bir şey kalmadı ──────────────────────────────────
        # `[mood:X]`-only cümle ya da tamamen noktalama ("...") → OmniVoice'a göndermenin
        # anlamı yok. Sessizlik push ETMEK zorundayız: hiç frame gitmezse livekit APIError
        # atıp turu öldürür (bkz. modül başındaki "BOŞ ÇIKTI" notu).
        if not _has_speakable(text):
            logger.info("TTS: seslendirilecek metin yok (%r) → sessiz geçildi", text[:20])
            self._emit_silence(output_emitter)
            return

        mood_key = effective_mood if effective_mood in MOOD_PRESETS else None

        # ── Kalıp/kısa metin yolu: cache + tamponlama + retry ────────────────────
        if tts_cache.is_cacheable(text):
            await self._run_short(output_emitter, text, mood_key)
            return

        # ── Uzun metin: eski davranış, doğrudan stream (gecikme artmasın) ────────
        try:
            if mood_key is not None:
                await self._run_http(output_emitter, text, mood_key)
            else:
                await self._run_ws(output_emitter, text)
        except Exception as exc:  # noqa: BLE001 — TTS hatası turu ÖLDÜRMESİN
            logger.warning(
                "TTS başarısız (%s: %s) → o cümle sessiz geçildi [%d karakter]",
                type(exc).__name__, exc, len(text),
            )
            self._emit_silence(output_emitter)

    async def _run_short(
        self, output_emitter: tts.AudioEmitter, text: str, mood: Optional[str]
    ) -> None:
        """Kısa/kalıp metin: cache'ten çal, yoksa TAMPONLA (stream etme) + boşsa retry.

        Neden tamponluyoruz: (a) cache'e yazmak için sesin tamamı elde olmalı,
        (b) emitter'a hiç dokunmadığımız sürece retry GÜVENLİ — bir kez `initialize()`
        edildikten sonra yeniden denemek mümkün değil. Kısa metinde tampon maliyeti
        ihmal edilebilir (~1 sn ses ≈ 48 KB).

        Sıra: cache → sentez → (boş çıktıysa) tek retry → sessizlik. Hiçbir adım
        turu öldürmez.
        """
        # Cache anahtarı pinned REFERANSA bağlı: ref okunamazsa cache tamamen kapalı
        # (yanlış sesle çalmaktansa yeniden üret). Bkz. tts_cache.ref_fingerprint.
        key: Optional[str] = None
        ref = await tts_cache.ref_fingerprint(self._omni._host, self._omni._port)
        if ref is not None:
            key = tts_cache.make_key(
                text, ref=ref, voice=self._omni._voice, mood=mood
            )
            cached = tts_cache.load(key)
            if cached:
                logger.info("TTS cache HIT (%d bayt): %.40r", len(cached), text)
                self._emit_pcm(output_emitter, cached)
                return

        # Guard (b): noktalaması eksik kısa metne nokta ekle. Boş çıktı riskini gözlenen
        # örneklerde sıfırladı; sesi değiştirmiyor. Cache anahtarı EKSİZ metinden üretildi.
        sent = _ensure_final_punct(text)
        if sent != text:
            logger.debug("TTS: kısa metne cümle sonu noktası eklendi: %r → %r", text, sent)

        for attempt in range(_SHORT_TEXT_RETRIES + 1):
            try:
                if mood is not None:
                    pcm, sample_rate, num_channels = await self._collect_http(sent, mood)
                else:
                    pcm, sample_rate, num_channels = await self._collect_ws(sent)
            except Exception as exc:  # noqa: BLE001 — hata da retry'a değer
                logger.warning(
                    "TTS kısa metin hatası (%s: %s) deneme %d/%d: %.40r",
                    type(exc).__name__, exc, attempt + 1, _SHORT_TEXT_RETRIES + 1, text,
                )
                continue

            if not pcm:
                # OmniVoice'un stokastik boş çıktısı — ölçüldü, tekrar denemek işe yarıyor.
                logger.warning(
                    "TTS boş çıktı, deneme %d/%d: %.40r",
                    attempt + 1, _SHORT_TEXT_RETRIES + 1, text,
                )
                continue

            if key is not None:
                tts_cache.store(
                    key, pcm, sample_rate=sample_rate, channels=num_channels
                )
            self._emit_pcm(
                output_emitter, pcm, sample_rate=sample_rate, num_channels=num_channels
            )
            return

        logger.warning("TTS kısa metin üretilemedi → sessiz geçildi: %.40r", text)
        self._emit_silence(output_emitter)

    async def _collect_ws(self, text: str) -> tuple[bytes, int, int]:
        """WS sentezini emitter'a DOKUNMADAN tampona al. (pcm_s16le, sample_rate, kanal)."""
        chunks: list[bytes] = []
        sample_rate = DEFAULT_SAMPLE_RATE
        num_channels = NUM_CHANNELS

        async with websockets.connect(
            self._omni._ws_url(), max_size=16 * 1024 * 1024
        ) as ws:
            await ws.send(json.dumps(self._speak_msg(text), ensure_ascii=False))
            async for raw in ws:
                if isinstance(raw, (bytes, bytearray)):
                    chunks.append(_f32le_to_s16le(bytes(raw)))
                    continue
                event = json.loads(raw)
                etype = event.get("type")
                if etype == "audio_start":
                    sample_rate = int(event.get("sample_rate", DEFAULT_SAMPLE_RATE))
                    num_channels = int(event.get("channels", NUM_CHANNELS))
                elif etype == "audio_end":
                    break
                elif etype == "error":
                    raise RuntimeError(f"OmniVoice: {event.get('message')}")

        return b"".join(chunks), sample_rate, num_channels

    async def _collect_http(self, text: str, mood: str) -> tuple[bytes, int, int]:
        """HTTP (mood) sentezini tampona al. WAV zaten s16le → dönüşüm yok."""
        wav_bytes = await self._post_tts(text, mood)
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
            sample_rate = wav.getframerate() or DEFAULT_SAMPLE_RATE
            num_channels = wav.getnchannels() or NUM_CHANNELS
            pcm = wav.readframes(wav.getnframes())  # s16le
        return pcm, sample_rate, num_channels

    def _speak_msg(self, text: str) -> dict:
        """WS `speak` mesajı. Metin HAM gider (voiced etiketler OmniVoice'e ulaşsın);
        `[mood:X]` KONTROL işareti zaten `_run()`'da silindi."""
        msg: dict = {"type": "speak", "id": "brain", "text": text}
        if self._omni._voice:
            msg["voice"] = self._omni._voice
        return msg

    async def _post_tts(self, text: str, mood: str) -> bytes:
        """`POST /api/tts` — mood preset'iyle klon sentez. Dönen ham WAV baytları."""
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
            return await resp.read()

    async def _run_ws(self, output_emitter: tts.AudioEmitter, text: str) -> None:
        """Nötr yol: WS ile streaming sentez (uzun metin; gecikme için tamponsuz).

        Ses üretilmezse RuntimeError atar → `_run()` yakalar ve sessizlik push eder.
        Sessizce dönmek OLMAZ: livekit hiç frame görmezse APIError'la turu öldürür.
        """
        pushed = 0

        async with websockets.connect(
            self._omni._ws_url(), max_size=16 * 1024 * 1024
        ) as ws:
            await ws.send(json.dumps(self._speak_msg(text), ensure_ascii=False))
            async for raw in ws:
                if isinstance(raw, (bytes, bytearray)):
                    self._ensure_emitter(output_emitter)
                    pcm = _f32le_to_s16le(bytes(raw))
                    output_emitter.push(pcm)
                    pushed += len(pcm)
                    continue

                event = json.loads(raw)
                etype = event.get("type")
                if etype == "audio_start":
                    self._ensure_emitter(
                        output_emitter,
                        sample_rate=int(event.get("sample_rate", DEFAULT_SAMPLE_RATE)),
                        num_channels=int(event.get("channels", NUM_CHANNELS)),
                    )
                elif etype == "audio_end":
                    break
                elif etype == "error":
                    raise RuntimeError(f"OmniVoice: {event.get('message')}")

        if not pushed:
            raise RuntimeError("OmniVoice: ses üretilmedi (boş çıktı)")
        output_emitter.flush()

    async def _run_http(
        self, output_emitter: tts.AudioEmitter, text: str, mood: str
    ) -> None:
        """Duygulu yol: HTTP /api/tts (pitch+speed preset). Ses kimliği (clone) korunur.

        Dönen WAV s16le olduğundan f32 dönüşümü GEREKMEZ; PCM doğrudan push edilir.
        """
        pcm, sample_rate, num_channels = await self._collect_http(text, mood)
        if not pcm:
            raise RuntimeError("OmniVoice: ses üretilmedi (boş WAV)")
        self._emit_pcm(
            output_emitter, pcm, sample_rate=sample_rate, num_channels=num_channels
        )
