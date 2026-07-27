"""higgs_tts — livekit-agents TTS plugin over Higgs TTS 3 (4B).

`omnivoice_tts.py`'nin YERİNE geçer; onu SİLMEZ. Motor seçimi `agent.py`'de
`TTS_ENGINE` ile yapılır, geri dönüş tek satır (bkz. handoff notu).

İKİ YOL:
  • `POST /api/tts/stream` → chunked ham PCM. **VARSAYILAN.** Sunucu cümleyi
    üretirken 320 ms'lik bloklar hâlinde akıtır; ilk ses ~0.5 sn'de başlar.
  • `POST /api/tts` → tam WAV. GERİ DÖNÜŞ yolu (`HIGGS_STREAM=0`) ve bench.

⚡ NEDEN STREAMING (27 Tem, canlı şikâyet: "konuşmalara geç başlıyor"):
Ölçülen gecikme (kullanıcı sustu → Candan'ın sesi) kısa cevapta ~3 sn, uzun
cevapta 8-10 sn'ydi ve UZUNLUKLA ARTIYORDU. Beyin darboğaz DEĞİLDİ (llama-server
KV cache yeniden kullanımıyla ardışık çağrılar 0.67-0.79 sn). Darboğaz TTS'ti:
tam WAV ucu cümlenin TAMAMINI üretip öyle dönüyor, o süre kullanıcı için
sessizlik. Ölçüm (HTTP, 5 tekrar medyanı, ilk sese kadar):

    cümle    tam WAV      streaming    kazanç
    kısa      761 ms       467 ms      -0.29 sn
    orta     2343 ms       510 ms      -1.83 sn
    uzun     6207 ms       546 ms      -5.66 sn

Toplam RTF ikisinde de ~0.50 — yani ses aynı hızda üretiliyor, sadece BEKLETMİYORUZ.
Streaming'de ilk ses cümle uzunluğundan neredeyse BAĞIMSIZ (467-546 ms).

livekit zaten `streaming=False` ile CÜMLE BAŞINA `synthesize()` çağırıyor;
`streaming` bayrağı GİRDİ (metin) akışıyla ilgili, ÇIKTI akışıyla değil.
`AudioEmitter`'a parça parça `push()` etmek bu bayrakla ilgisiz ve
`omnivoice_tts._run_ws` yıllardır aynısını yapıyor — pipeline bunu destekliyor.

OMNIVOICE'TAN AYNEN TAŞINAN KAZANIMLAR (hepsi ölçülmüş):
  • `normalize_tr()` (trnorm) — Higgs'te de WER 0.058 → 0.028 (29 cümle, ASR
    geri-dönüş). Higgs ham metinde zaten iyi; kalan gerçek zayıflık `1.250.000`
    tipi binlik+milyon ve trnorm onu düzeltiyor.
  • `tts_cache` — kalıp/kısa cümle cache'i.
  • Kısa metin guard'ı: cümle sonu noktası → tek retry → sessizlik.
  • TTS hatası TURU ÖLDÜRMEZ: her yolda en az bir parça (gerekirse sessizlik)
    push edilir. Hiç frame push edilmezse livekit "no audio frames were pushed"
    APIError'ı atıp turu öldürüyor.

⚠️ CACHE ANAHTARI — MOTOR KİMLİĞİ ŞART. `tts_cache` anahtarı referans parmak izini
içeriyor; ama Higgs ve OmniVoice AYNI referans wav'ını (`/opt/omnivoice/default-ref.wav`)
ve aynı `ref_text`'i kullanıyor. Sadece referansa bakan bir anahtar iki motorda da
AYNI çıkar → Higgs'e geçince eski OmniVoice cache'i YANLIŞ SESLE çalardı.
Bu yüzden anahtara giren `ref` alanı `higgs-tts-3-4b:<parmak izi>` biçiminde,
motor kimliğiyle ÖNEKLİ (bkz. `ref_fingerprint`). Ayrıca geçişte
`worker/data/tts-cache/` bir kez TEMİZLENİR (handoff notundaki komut).

⚠️ ETİKET SÖZDİZİMİ — GEÇİŞİ BLOKE EDEN FARK. OmniVoice `[laughter]`, `[sigh]`,
`[surprise-oh]`, `[question-en]`, `[confirmation-en]` etiketlerini SESLENDİRİYORDU;
`pi/AGENTS.md` ve `pi/personas/candan.md` modele bunları hâlâ ürettiriyor (prompt
tarafına DOKUNULMADI — OmniVoice'a geri dönüş bozulmasın). Higgs'in resmi
`PROMPTING.md`'si ise net: **tanınmayan etiket ya çıktıyı bozar ya da HARFİ HARFİNE
OKUNUR.** Yani müdahalesiz Higgs "laughter", "sigh", "mood excited" diye konuşurdu.
Üstelik `trnorm` köşeli parantez içini BİLEREK koruyor (OmniVoice için doğruydu),
yani etiketler bozulmadan buraya ulaşıyor.

Çözüm `_to_higgs_markup()`: trnorm'dan SONRA çalışır, `HIGGS_TAG_MAP`/`MOOD_PRESETS`
ile Higgs'in kendi sözdizimine (`<|kategori:etiket|>`) çevirir ve **eşlemesi olmayan
her `[...]` kalıbını SİLER**. Garanti: sunucuya giden metinde köşeli parantez KALMAZ.
Higgs'in yerleşim kuralı da korunur — emotion/style/prosody CÜMLE BAŞINA taşınır,
`sfx` yerinde kalır (etiketin hemen ardından ses taklidi, arada boşluk YOK).

SIRA (kritik): mood çıkarma → trnorm → etiket dönüşümü → gönderim.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import time
import wave
from typing import Optional

import aiohttp

from livekit.agents import tts, utils
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions

import tts_cache
from log_utils import DedupeFilter
from trnorm import normalize_tr

logger = logging.getLogger("higgs_tts")
logger.addFilter(DedupeFilter())

# Cache anahtarına giren motor kimliği. DEĞİŞTİRME = tüm Higgs cache'ini geçersiz kılar.
ENGINE_ID = "higgs-tts-3-4b"

DEFAULT_SAMPLE_RATE = 24000
NUM_CHANNELS = 1

# Boş çıktı / hata durumunda emitter'a verilen sessizlik (omnivoice_tts ile aynı gerekçe:
# sıfır frame → livekit APIError → TUR ÖLÜR). Sessiz kalmak kabul, çökmek DEĞİL.
_SILENCE_MS = 120
_SHORT_TEXT_RETRIES = 1

# Sentez zaman aşımı. Ölçülen en yavaş cümle 4.1 s; 400 karakterlik emniyet supabı
# birkaç parçaya bölünebilir. 90 s cömert ama sonsuz değil — asılı kalan istek turu
# kilitlemesin.
_SYNTH_TIMEOUT_S = 90.0
# Streaming'de İKİ BLOK ARASI en uzun bekleme. Ölçülen en kötü ara 163 ms (blok=8);
# 20 s astronomik bir pay ama "sunucu bloke oldu" hâlini toplam 90 s beklemeden
# yakalar — tur erken kurtulsun.
_STREAM_STALL_S = 20.0
# `GET /api/default` (cache parmak izi) — kısa ve bağlayıcı olmayan.
_REF_TIMEOUT_S = 2.0
_REF_TTL_S = 300.0

# `[mood:excited]` / `[mood:sad]` KONTROL işareti — seslendirilmez, metinden SİLİNİR.
# omnivoice_tts ile AYNI sözleşme: pi_brain aynı işareti üretiyor, iki motor da anlamalı.
_MOOD_RE = re.compile(r"\s*\[mood:(excited|sad)\]\s*", re.IGNORECASE)
KNOWN_MOODS = ("excited", "sad")

_FINAL_PUNCT = ".!?…:;"

# ── OmniVoice etiketi → Higgs etiketi ────────────────────────────────────────
# TEK DÜZENLEME YERİ. Duygu işi ayrı bir görevde derinleştirilecek; katalog burada
# genişletilir, `_to_higgs_markup()` değişmez.
#
# Higgs sözdizimi `<|kategori:etiket|>`, iki yerleşim sınıfı var:
#   • CÜMLE BAŞI  : emotion (21), style (3), prosody'nin speed_*/pitch_*/expressive_*
#   • SATIR İÇİ   : sfx (tam yerinde) ve prosody'nin pause / long_pause
# `sfx` tuzağı: etiketten hemen SONRA ses taklidi gelmeli, ARADA BOŞLUK YOK
# (`<|sfx:laughter|>Haha, ...`).
#
# Boş dize ("") = "karşılığı yok, SİL". Sözlükte HİÇ olmayan anahtar da silinir —
# fark yalnız niyet beyanında: burada olan "bilerek atıldı", olmayan "tanınmadı".
# ⚠️ `excited` için AKLA İLK GELEN `<|emotion:elation|>` KULLANILMIYOR — ÖLÇÜLDÜ ve
# BOZUK çıktı. Aynı cümle (`"Bugün harika bir haber var."`), sunucuda 12'şer örnek,
# Whisper geri-dönüşüyle anlaşılırlık:
#     düz (etiketsiz)            12/12      <|emotion:sadness|>     12/12
#     <|emotion:enthusiasm|>     12/12      <|emotion:surprise|>    12/12
#     <|emotion:amusement|>      12/12      <|sfx:laughter|>        12/12
#     <|emotion:elation|>       5-7/12  ← cümle BAŞINI yiyor; 3 örnek tamamen boş,
#                                          2 örnek alakasız gevezelik ("Bye bye!")
# Katalogdaki temiz çıkan en yakın karşılık `enthusiasm` seçildi. Duygu işi ayrı
# görevde derinleştirilirken YENİ TOKEN AYNI ŞEKİLDE ÖLÇÜLSÜN — tokenizer etiketi
# tanıyor olması (hepsi tek özel token) çalıştığı anlamına GELMİYOR.
MOOD_PRESETS: dict[str, str] = {
    "excited": "<|emotion:enthusiasm|>",
    "sad": "<|emotion:sadness|>",
}

HIGGS_TAG_MAP: dict[str, str] = {
    # sesli taklit gerektirenler — SATIR İÇİ, taklit etikete bitişik
    "laughter": "<|sfx:laughter|>Haha, ",
    "sigh": "<|sfx:sigh|>Haah, ",
    # şaşkınlık aileleri → tek emotion karşılığı (CÜMLE BAŞINA taşınır)
    "surprise-ah": "<|emotion:surprise|>",
    "surprise-oh": "<|emotion:surprise|>",
    "surprise-wa": "<|emotion:surprise|>",
    "surprise-yo": "<|emotion:surprise|>",
    # karşılığı NET DEĞİL → sil (okunmaması yeter; uydurma eşleme YAPMIYORUZ)
    "question-en": "",
    "question-ah": "",
    "question-oh": "",
    "question-ei": "",
    "question-yi": "",
    "confirmation-en": "",
    "dissatisfaction-hnn": "",
}

# Metindeki HER `[...]` kalıbı. Eşlemesi olmayan da bu regex'e takılır ve SİLİNİR —
# "tanınmayan etiket asla seslendirilmez" garantisi buradan geliyor.
_BRACKET_RE = re.compile(r"\[[^\[\]]{0,60}\]")
# Yerleşimi CÜMLE BAŞI olan token sınıfı (kalanı satır içi kalır).
_PREFIX_TOKEN_RE = re.compile(
    r"^<\|(?:emotion|style):|^<\|prosody:(?:speed|pitch|expressive)_"
)
# Higgs kontrol token'ı — "söylenecek bir şey kaldı mı" sayımında sayılmaz.
_HIGGS_TOKEN_RE = re.compile(r"<\|[a-z_]+:[a-z_]+\|>")


def _extract_mood(text: str) -> tuple[Optional[str], str]:
    """Metinden `[mood:X]` KONTROL işaretini çıkar → (mood | None, temiz metin)."""
    if not text or "[" not in text:
        return None, text
    m = _MOOD_RE.search(text)
    if not m:
        return None, text
    cleaned = re.sub(r"\s{2,}", " ", _MOOD_RE.sub(" ", text)).strip()
    return m.group(1).lower(), cleaned


def _has_speakable(text: str) -> bool:
    """Seslendirilecek harf/rakam var mı? "...", "?!", " - " → yok."""
    return any(ch.isalnum() for ch in text)


def _to_higgs_markup(text: str, mood: Optional[str]) -> str:
    """OmniVoice `[etiket]`lerini Higgs sözdizimine çevir; TANINMAYANI SİL.

    GARANTİ: dönen metinde köşeli parantezli hiçbir kalıp KALMAZ. Higgs
    tanımadığı etiketi harfi harfine okuduğu için bu bir konfor değil, koruma.

    Yerleşim: emotion/style/prosody-cümle-başı token'ları metnin BAŞINA taşınır
    (kategori başına ilk gelen kazanır — `[mood:X]` her zaman ilk sırada, çünkü
    tur-kapsamlı ve daha belirleyici). `sfx` bulunduğu yerde kalır.

    trnorm'dan SONRA çağrılmalı: trnorm köşeli parantez içini bilerek koruyor
    (OmniVoice etiketleri seslendiriyordu), yani dönüşüm ondan önce yapılırsa
    ürettiğimiz `<|...|>` token'ları normalizasyona yakalanır.
    """
    prefixes: list[str] = []
    seen_categories: set[str] = set()

    def _add_prefix(token: str) -> None:
        category = token[2:token.index(":")]
        if category in seen_categories:   # istifleme evet, AYNI kategoriden iki tane hayır
            return
        seen_categories.add(category)
        prefixes.append(token)

    if mood in MOOD_PRESETS:
        _add_prefix(MOOD_PRESETS[mood])

    def _replace(match: re.Match) -> str:
        key = match.group(0)[1:-1].strip().lower()
        repl = HIGGS_TAG_MAP.get(key)
        if repl is None:
            # TANINMAYAN: Higgs bunu SESLİ OKUR → tek güvenli davranış silmek.
            logger.info("TTS: Higgs'in tanımadığı etiket silindi: %r", match.group(0))
            return " "
        if not repl:
            return " "
        if _PREFIX_TOKEN_RE.match(repl):
            _add_prefix(repl)
            return " "
        return repl        # satır içi (sfx / pause) — yerinde kalır

    body = _BRACKET_RE.sub(_replace, text)
    body = re.sub(r"\s{2,}", " ", body).strip()
    body = re.sub(r"\s+([,.!?;:…])", r"\1", body)   # silinen etiketin bıraktığı boşluk
    if body.endswith(","):                          # "…Haha," gibi asılı kalan taklit
        body = body[:-1] + "."
    return "".join(prefixes) + body


def _speakable_body(markup: str) -> str:
    """Higgs kontrol token'ları çıkarılmış hâl — "söylenecek bir şey var mı" için."""
    return _HIGGS_TOKEN_RE.sub(" ", markup)


def _ensure_final_punct(text: str) -> str:
    """Kısa metnin sonuna nokta ekle (omnivoice_tts'teki ölçülmüş EN AZ müdahale).

    Higgs'in boş çıktı davranışı BİLİNMİYOR (116 wav'ın hiçbiri boş çıkmamıştı,
    ama kısa tek kelimeler bench'te yoktu). Guard yine de duruyor: zararı yok,
    faydası kanıtlı bir sınıfta var.
    """
    stripped = text.rstrip()
    if not stripped or stripped[-1] in _FINAL_PUNCT:
        return text
    return stripped + "."


def _header_int(raw: Optional[str], default: int) -> int:
    """Sunucu başlığından tam sayı; bozuk/eksikse VARSAYILAN (başlık yüzünden tur ölmez)."""
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _silence_pcm() -> bytes:
    return b"\x00\x00" * int(DEFAULT_SAMPLE_RATE * NUM_CHANNELS * _SILENCE_MS / 1000)


# ── Referans parmak izi (cache anahtarı) ─────────────────────────────────────
_ref_cache: dict[str, tuple[float, Optional[str]]] = {}


def reset_ref_cache() -> None:
    """Parmak izi belleğini boşalt (test için)."""
    _ref_cache.clear()


def _fingerprint_from_payload(payload: object) -> str:
    """`GET /api/default` gövdesinden MOTOR KİMLİĞİ ÖNEKLİ parmak izi.

    Sunucu `ref_fingerprint` veriyorsa (referans KODLARININ sha256'sı) onu kullanırız —
    aynı yola başka bir wav yazılsa bile değişir. Yoksa gövdenin tamamı özetlenir.
    Önek `ENGINE_ID`: OmniVoice ile aynı referans wav'ını paylaştığımız için anahtarın
    motoru ayırt etmesi ŞART (bkz. modül başındaki uyarı).
    """
    fp: Optional[str] = None
    if isinstance(payload, dict):
        raw = payload.get("ref_fingerprint")
        if isinstance(raw, str) and raw:
            fp = raw
    if fp is None:
        material = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        fp = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"{ENGINE_ID}:{fp}"


async def ref_fingerprint(host: str, port: int) -> Optional[str]:
    """Higgs sunucusundaki referansın parmak izi. SALT-OKUMA (`GET /api/default`).

    None dönerse cache DEVRE DIŞI bırakılmalı: hangi referansla üretildiğini
    bilmediğimiz sesi çalmak, yeniden üretmekten daha kötü. Sonuç (başarısızlık dahil)
    TTL boyunca bellekte tutulur — her tur timeout beklemeyelim.
    """
    key = f"{host}:{port}"
    now = time.monotonic()
    hit = _ref_cache.get(key)
    if hit and now - hit[0] < _REF_TTL_S:
        return hit[1]

    try:
        timeout = aiohttp.ClientTimeout(total=_REF_TIMEOUT_S)
        async with (
            aiohttp.ClientSession(timeout=timeout) as sess,
            sess.get(f"http://{host}:{port}/api/default") as resp,
        ):
            resp.raise_for_status()
            payload = json.loads(await resp.text())
    except Exception as exc:  # noqa: BLE001 — cache bir optimizasyon, tur BOZULMAZ
        logger.warning("Higgs referansı okunamadı (%s) → ses cache'i devre dışı", exc)
        _ref_cache[key] = (now - _REF_TTL_S * 0.8, None)
        return None

    fp = _fingerprint_from_payload(payload)
    _ref_cache[key] = (now, fp)
    return fp


class HiggsTTS(tts.TTS):
    """Higgs TTS 3 plugin. `OmniVoiceTTS` ile AYNI yüzey (reset_mood dahil)."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        voice: Optional[str] = None,
        token: Optional[str] = None,
        stream: bool = True,
    ):
        super().__init__(
            # `streaming=False` GİRDİ akışı içindir (SynthesizeStream yok, livekit
            # cümle cümle çağırır). ÇIKTIYI parça parça push etmeye engel DEĞİL.
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=DEFAULT_SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
        )
        self._host = host
        self._port = port
        self._voice = voice
        self._token = token
        self._current_mood: Optional[str] = None
        # Chunked PCM ucu. Kapatınca tam-WAV yoluna dönülür (TEK SATIRLIK geri dönüş:
        # worker/.env'e `HIGGS_STREAM=0`). Sunucu ikisini de sunmaya devam ediyor.
        self._stream = stream

    def reset_mood(self) -> None:
        """Yeni tur başında nötr'e dön (agent.py agent_state 'thinking' hook'undan)."""
        self._current_mood = None

    def _http_url(self) -> str:
        return f"http://{self._host}:{self._port}/api/tts"

    def _stream_url(self) -> str:
        return f"http://{self._host}:{self._port}/api/tts/stream"

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> "HiggsChunkedStream":
        return HiggsChunkedStream(tts=self, input_text=text, conn_options=conn_options)


class HiggsChunkedStream(tts.ChunkedStream):
    """Bir synth turu. Higgs'te tek yol var: HTTP POST → tam WAV."""

    def __init__(
        self,
        *,
        tts: HiggsTTS,
        input_text: str,
        conn_options: APIConnectOptions,
    ):
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._higgs = tts
        # Emitter `initialize()` edildi mi? KENDİMİZ takip ediyoruz: `pushed_duration()`
        # asenkron güncellenen bir sayaç, hemen ardından okumak güvenilmez.
        self._emitter_ready = False

    # ── Emitter yardımcıları ─────────────────────────────────────────────────
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

    def _emit_pcm(
        self,
        output_emitter: tts.AudioEmitter,
        pcm: bytes,
        *,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        num_channels: int = NUM_CHANNELS,
    ) -> None:
        self._ensure_emitter(
            output_emitter, sample_rate=sample_rate, num_channels=num_channels
        )
        output_emitter.push(pcm)
        output_emitter.flush()

    def _push_pcm(
        self,
        output_emitter: tts.AudioEmitter,
        pcm: bytes,
        *,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        num_channels: int = NUM_CHANNELS,
    ) -> None:
        """Parça push et, flush ETME (streaming: flush cümlenin SONUNDA bir kez)."""
        self._ensure_emitter(
            output_emitter, sample_rate=sample_rate, num_channels=num_channels
        )
        output_emitter.push(pcm)

    def _emit_silence(self, output_emitter: tts.AudioEmitter) -> None:
        """Turu KURTARAN sessizlik — sessiz kalmak kabul, çökmek DEĞİL."""
        try:
            self._ensure_emitter(output_emitter)
            output_emitter.push(_silence_pcm())
            output_emitter.flush()
        except Exception:  # noqa: BLE001 — guard'ın kendisi ASLA patlamasın
            logger.warning("sessizlik guard'ı emitter'a yazamadı", exc_info=True)

    # ── Ana akış ─────────────────────────────────────────────────────────────
    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        # SIFIRLA: livekit `_main_task` her deneme için YENİ bir AudioEmitter kurup
        # `_run()`'ı tekrar çağırıyor. Bayrak devreden kalırsa ikinci denemede
        # `initialize()` atlanır ve `push()` "AudioEmitter isn't started" ile patlar.
        self._emitter_ready = False

        mood, text = _extract_mood(self._input_text)
        if mood is not None:
            self._higgs._current_mood = mood
        effective_mood = self._higgs._current_mood

        # Türkçe normalizasyon — SIRA KRİTİK: mood İŞARETİ ÇIKARILDIKTAN SONRA,
        # gönderimden ÖNCE. Ölçüm (29 cümle, ASR geri-dönüş): WER 0.058 → 0.028.
        text = normalize_tr(text)

        # Guard 1: söylenecek bir şey kalmadı ([mood:X]-only, "..." vb.).
        if not _has_speakable(text):
            logger.info("TTS: seslendirilecek metin yok (%r) → sessiz geçildi", text[:20])
            self._emit_silence(output_emitter)
            return

        mood_key = effective_mood if effective_mood in KNOWN_MOODS else None

        if tts_cache.is_cacheable(text):
            await self._run_short(output_emitter, text, mood_key)
            return

        sent = self._prepare(text, mood_key)
        if sent is None:
            logger.info("TTS: etiket dönüşümünden sonra metin kalmadı → sessiz geçildi")
            self._emit_silence(output_emitter)
            return

        pushed = 0
        try:
            async for pcm, sample_rate, num_channels in self._iter_pcm(sent, mood_key):
                if not pcm:
                    continue
                self._push_pcm(
                    output_emitter, pcm,
                    sample_rate=sample_rate, num_channels=num_channels,
                )
                pushed += len(pcm)
            if not pushed:
                raise RuntimeError("Higgs: ses üretilmedi (boş yanıt)")
            output_emitter.flush()
        except Exception as exc:  # noqa: BLE001 — TTS hatası turu ÖLDÜRMESİN
            logger.warning(
                "TTS başarısız (%s: %s) → cümlenin kalanı sessiz geçildi "
                "[%d karakter, %d bayt çalındı]",
                type(exc).__name__, exc, len(text), pushed,
            )
            if pushed:
                # YARIM ses zaten çıktı: sessizlik EKLEME (tur zaten kurtuldu),
                # sadece parçayı kapat ki livekit segmenti bitmiş saysın.
                try:
                    output_emitter.flush()
                except Exception:  # noqa: BLE001
                    logger.warning("yarım akış flush edilemedi", exc_info=True)
            else:
                self._emit_silence(output_emitter)

    async def _run_short(
        self, output_emitter: tts.AudioEmitter, text: str, mood: Optional[str]
    ) -> None:
        """Kısa/kalıp metin: cache'ten çal, yoksa üret + boşsa tek retry + cache'e yaz.

        Sıra: cache → sentez → (boş/hata) tek retry → sessizlik. Hiçbir adım turu öldürmez.
        """
        key: Optional[str] = None
        ref = await ref_fingerprint(self._higgs._host, self._higgs._port)
        if ref is not None:
            key = tts_cache.make_key(
                text, ref=ref, voice=self._higgs._voice, mood=mood
            )
            cached = tts_cache.load(key)
            if cached:
                logger.info("TTS cache HIT (%d bayt): %.40r", len(cached), text)
                self._emit_pcm(output_emitter, cached)
                return

        sent = self._prepare(text, mood)
        if sent is None:
            logger.info("TTS: etiket dönüşümünden sonra metin kalmadı → sessiz geçildi")
            self._emit_silence(output_emitter)
            return

        # RETRY'İN ŞARTI: HİÇ ses push edilmemiş olmak. Streaming'de parçalar
        # çıktıkça çalınıyor; yarısı çalınmış bir cümleyi baştan sentezlemek
        # kullanıcıya cümleyi İKİ KEZ dinletir. O yüzden ses başladıktan sonra
        # gelen hata retry ETMEZ, elde ne varsa onunla kapatılır.
        pushed = 0
        for attempt in range(_SHORT_TEXT_RETRIES + 1):
            parts: list[bytes] = []
            sample_rate, num_channels = DEFAULT_SAMPLE_RATE, NUM_CHANNELS
            try:
                async for pcm, sr, ch in self._iter_pcm(sent, mood):
                    if not pcm:
                        continue
                    sample_rate, num_channels = sr, ch
                    parts.append(pcm)
                    self._push_pcm(
                        output_emitter, pcm, sample_rate=sr, num_channels=ch
                    )
                    pushed += len(pcm)
            except Exception as exc:  # noqa: BLE001 — hata da retry'a değer
                logger.warning(
                    "TTS kısa metin hatası (%s: %s) deneme %d/%d: %.40r",
                    type(exc).__name__, exc, attempt + 1, _SHORT_TEXT_RETRIES + 1, text,
                )
                if pushed:
                    break              # yarım ses çıktı → tekrarlama
                continue

            if not parts:
                logger.warning(
                    "TTS boş çıktı, deneme %d/%d: %.40r",
                    attempt + 1, _SHORT_TEXT_RETRIES + 1, text,
                )
                continue

            # CACHE streaming'de de YAZILIR: parçalar birleştirilince tam PCM.
            # Bir sonraki turda cache HIT olur ve sentez HİÇ yapılmaz (streaming'e
            # de gerek kalmaz — anında çalar).
            if key is not None:
                tts_cache.store(
                    key, b"".join(parts),
                    sample_rate=sample_rate, channels=num_channels,
                )
            output_emitter.flush()
            return

        if pushed:
            # Yarım ses çalındı: tur kurtuldu, sessizlik EKLEME — sadece kapat.
            try:
                output_emitter.flush()
            except Exception:  # noqa: BLE001
                logger.warning("yarım akış flush edilemedi", exc_info=True)
            return

        logger.warning("TTS kısa metin üretilemedi → sessiz geçildi: %.40r", text)
        self._emit_silence(output_emitter)

    def _prepare(self, text: str, mood: Optional[str]) -> Optional[str]:
        """Gönderime hazır Higgs metni; söylenecek bir şey kalmadıysa None.

        `_to_higgs_markup` tanınmayan `[...]` kalıplarını sildiği için sonuç BOŞ
        kalabilir (örn. yalnız `[question-en]` içeren cümle). O durumda sunucuya
        boş metin gönderip 400 yemek yerine doğrudan sessizliğe düşülür.
        """
        markup = _to_higgs_markup(text, mood)
        if not _has_speakable(_speakable_body(markup)):
            return None
        return _ensure_final_punct(markup)

    async def _iter_pcm(self, text: str, mood: Optional[str]):
        """PCM parçalarını (pcm, sample_rate, kanal) olarak akıtır — TEK GİRİŞ NOKTASI.

        Streaming açıksa `/api/tts/stream`'i dinler; kapalıysa (`HIGGS_STREAM=0`)
        tam WAV'ı çekip TEK parça verir. Çağıranlar (`_run`, `_run_short`) iki
        durumu da aynı döngüyle işliyor, yani geri dönüş yolu ayrı kod değil.
        """
        if getattr(self._higgs, "_stream", True):
            async for part in self._stream_pcm(text, mood):
                yield part
            return
        yield await self._collect(text, mood)

    async def _stream_pcm(self, text: str, mood: Optional[str]):
        """`POST /api/tts/stream` → chunked ham PCM parçaları.

        SES BAŞLAMADAN gelen HTTP hatası (400/500/502/503) exception'a döner;
        çağıran sessizliğe düşer, tur ölmez. Ses BAŞLADIKTAN sonra sunucu
        yarıda kalırsa aiohttp gövdeyi eksik görüp hata atar — o da exception'a
        döner ama çağıran o ana kadarki sesi zaten çalmıştır.

        ⚠️ TEK BAYT HİZALAMA: s16le'de bir örnek 2 bayt, ama TCP parçaları
        keyfî sınırdan gelebilir. Tek sayılı kuyruk bir SONRAKİ parçaya devredilir;
        aksi hâlde emitter'a yarım örnek gider ve ses kayar (çıtırtı).
        """
        payload = {"text": text}
        if mood:
            payload["mood"] = mood
        if self._higgs._voice:
            payload["voice"] = self._higgs._voice

        timeout = aiohttp.ClientTimeout(
            total=_SYNTH_TIMEOUT_S, sock_read=_STREAM_STALL_S
        )
        async with (
            aiohttp.ClientSession(timeout=timeout) as sess,
            sess.post(self._higgs._stream_url(), json=payload) as resp,
        ):
            if resp.status != 200:
                detail = (await resp.text())[:200]
                raise RuntimeError(f"Higgs HTTP {resp.status}: {detail}")

            sample_rate = _header_int(
                resp.headers.get("X-Higgs-Sample-Rate"), DEFAULT_SAMPLE_RATE
            )
            num_channels = _header_int(
                resp.headers.get("X-Higgs-Channels"), NUM_CHANNELS
            )

            tail = b""
            async for raw in resp.content.iter_any():
                if not raw:
                    continue
                buf = tail + raw
                cut = len(buf) - (len(buf) % 2)
                tail = buf[cut:]
                if cut:
                    yield buf[:cut], sample_rate, num_channels
            if tail:
                # Yarım örnekle bitmek sunucu hatası olurdu; sessizce yutma.
                logger.warning("Higgs akışı tek bayt artıkla bitti (%d) → atıldı", len(tail))

    async def _collect(self, text: str, mood: Optional[str]) -> tuple[bytes, int, int]:
        """`POST /api/tts` → (s16le PCM, sample_rate, kanal). WAV zaten s16le."""
        wav_bytes = await self._post_tts(text, mood)
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
            sample_rate = wav.getframerate() or DEFAULT_SAMPLE_RATE
            num_channels = wav.getnchannels() or NUM_CHANNELS
            pcm = wav.readframes(wav.getnframes())
        return pcm, sample_rate, num_channels

    async def _post_tts(self, text: str, mood: Optional[str]) -> bytes:
        """Ham WAV baytları. HTTP hatası (400/500/502/503) exception'a döner —
        sunucu sessizce boş ses dönmüyor, biz de sessizce yutmuyoruz."""
        payload = {"text": text, "format": "wav"}
        if mood:
            payload["mood"] = mood
        if self._higgs._voice:
            payload["voice"] = self._higgs._voice

        timeout = aiohttp.ClientTimeout(total=_SYNTH_TIMEOUT_S)
        async with (
            aiohttp.ClientSession(timeout=timeout) as sess,
            sess.post(self._higgs._http_url(), json=payload) as resp,
        ):
            if resp.status != 200:
                detail = (await resp.text())[:200]
                raise RuntimeError(f"Higgs HTTP {resp.status}: {detail}")
            return await resp.read()
