"""tts_cache — kalıp/kısa cümleler için ses cache'i (ses klonlama maliyetini sıfırlar).

NEDEN: sabit ses için klonlama zorunlu ve bedeli ~3.4× (4 sn referansla ~1.7×) —
bkz. `handoff/2026-07-25-tts-arastirma-ve-server-adimlari.md` §2. Sık tekrar eden kısa
yanıtlarda ("Tamam.", "Peki, gerek yok.", enrollment/sıfırlama satırları) bu bedeli her
seferinde ödemenin anlamı yok: aynı metin + aynı ses + aynı referans → aynı ses dosyası.

GENEL LRU DEĞİL — bilinçli olarak sadece iki şey cache'lenir:
  • `CACHED_PHRASES` içindeki kalıp satırlar (pi_brain.py'deki scripted sabitler),
  • `CACHE_MAX_CHARS`'tan kısa metinler (LLM'in "Tamam."/"Evet." tipi yanıtları —
    zaten kısa-metin guard'ının koruduğu sınıf, bkz. higgs_tts._run_short).
Uzun/serbest cümleler cache'e HİÇ girmez (disk şişmesin, isabet oranı sıfıra yakın).

ANAHTAR (bkz. make_key):
    sha256( ref_fingerprint | voice | mood | normalize_tr(metin) )
`mood` anahtarda ŞART: MOOD_PRESETS `speed`'i değiştiriyor (1.18 / 0.85) ve bu ölçülen
TEK çalışan kısım — mood'suz anahtar yanlış tempoda ses çalar.
`ref_fingerprint` anahtarda ŞART: `.25`'teki pinned referans (`default-ref.wav`)
değişebilir (§1.B onay bekliyor); ref değişince eski cache YANLIŞ SESLE çalar.
Referans okunamazsa cache TAMAMEN devre dışı kalır — yanlış ses çalmaktansa yeniden üret.

FORMAT: dosyalar ham PCM, 24 kHz mono s16le (`.pcm`). AudioEmitter'a dönüşümsüz
push edilir. Başka örnekleme hızı/kanal sayısı gelirse o çıktı cache'lenmez.

PREWARM (elle, otomatik ÇALIŞMAZ):
    cd worker && HIGGS_TTS_HOST=192.168.0.25 HIGGS_TTS_PORT=8809 python3 -m tts_cache --prewarm
    python3 -m tts_cache --list      # cache içeriği
    python3 -m tts_cache --clear     # cache'i boşalt
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

from trnorm import normalize_tr

logger = logging.getLogger("tts_cache")

# ── Ayarlar ───────────────────────────────────────────────────────────────────
CACHE_DIR = Path(__file__).resolve().parent / "data" / "tts-cache"
# Bu uzunluğun altındaki her metin cache'lenir. 48: "Tamam, devam ediyoruz." (22) ve
# cümleye bölünmüş kalıp parçaları rahat sığar; serbest sohbet cümleleri sığmaz.
CACHE_MAX_CHARS = 48
CACHE_MAX_FILES = 300          # ~300 × 1 sn × 48 KB ≈ 15 MB — fazlası eskiden silinir
CACHE_MAX_BYTES = 32 * 1024 * 1024
# Cache'lenen ham PCM'in tek geçerli formatı (emitter'a dönüşümsüz gitsin).
CACHE_SAMPLE_RATE = 24000
CACHE_CHANNELS = 1
# Pinned referans parmak izi bu kadar saniye bellekte tutulur (her turda HTTP atmayalım).
REF_FINGERPRINT_TTL = 300.0
REF_FETCH_TIMEOUT = 2.0

# ── Kalıp cümleler ────────────────────────────────────────────────────────────
# pi_brain.py'deki scripted/sabit yanıtlar: kullanıcı metnine göre DEĞİŞMEYEN, gerçekten
# tekrar eden satırlar. İsim içeren f-string'ler (f"Memnun oldum {name}!") BİLEREK yok —
# onlar tekrar etmez. Tek yer, elle düzenlenir (MOOD_PRESETS gibi).
# NOT: livekit streaming=False TTS'i CÜMLE BAŞINA synthesize() eder → aşağıdaki çok
# cümleli satırlar `_sentence_pieces()` ile parçalarına da açılır (ikisi de cache'lenir).
CACHED_PHRASES: tuple[str, ...] = (
    # sıfırlama (RESET_ACK / RESET_FAIL / RESET_CONFIRM_ASK / RESET_CONFIRM_NO)
    "Tamam, yeni sohbet başlattım. Seni dinliyorum.",
    "Şu anda sohbeti sıfırlayamadım, sonra tekrar deneyelim.",
    "Yeni sohbet başlatmamı mı istiyorsun?",
    "Tamam, devam ediyoruz.",
    # enrollment / ses kaydı
    "Teşekkür ederim. Ses profilini kaydettim.",
    "Boş ver, sonra kaydederiz.",
    "Peki, gerek yok.",
    "Seni kaydedebilmem için adını tek kelime olarak söyler misin?",
    "Adını anlayamadım. Adını tek kelime olarak söyler misin?",
    "Adını anlayamadım, seni kaydedemedim. İstersen sonra 'beni kaydet' de.",
    "Adını tam anlayamadım, sonra tekrar deneriz.",
    "Adını tam anlayamadım. Adını tek kelime olarak söyler misin?",
    "Ses kaydı şu anda kapalı, seni kaydedemem.",
    "Sesini tam alamadım. Biraz daha konuşur musun, tekrar deneyelim?",
    "Şu anda seni kaydedemedim, sonra tekrar deneyelim.",
    "Bu bölümü atlamayalım.",
    # rol yükseltme (_role_command)
    "Bunu ancak evin yetişkini yapabilir.",
    "Şu anda yapamadım, sonra tekrar deneyelim.",
    # ifade corpus'u istemleri (SPEAKER_EXPRESSION_PROMPTS — her enrollment'ta AYNEN tekrar)
    "Şimdi neşeli bir tonda şu cümleyi söyle: Bugün güzel bir şey oldu, çok mutluyum.",
    "Şimdi üzgün bir tonda şu cümleyi söyle: Bugün kendimi biraz üzgün hissediyorum.",
    "Şimdi meraklı bir tonda şu cümleyi söyle: Acaba şimdi ne olacak, gerçekten merak ediyorum.",
    "Şimdi kızgın bir tonda şu cümleyi söyle: Buna gerçekten kızdım, böyle olmasını istemezdim.",
    "Şimdi aceleci bir tonda şu cümleyi söyle: Hemen çıkmam gerekiyor, biraz acelem var.",
    "Şimdi istersen kendini kısaca anlat veya birkaç cümle serbestçe konuş. "
    "Bitirdiğinde sadece kaydım tamam de.",
    # kimlik guard'ı (_identity_guard_reply) — isimsiz, sabit dal
    "Bu kısa cümlede sesini yeniden doğrulayacak kadar örnek alamadım. "
    "Adını tahmin etmeyeceğim; birkaç saniye normal konuşursan tekrar karşılaştırırım.",
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _sentence_pieces(phrase: str) -> list[str]:
    """Kalıbı cümlelerine böl. livekit cümle başına synthesize() ettiği için parçalar da
    cache'lenebilir olmalı; tek cümlelik kalıpta parça = kalıbın kendisi."""
    return [p for p in (s.strip() for s in _SENTENCE_SPLIT_RE.split(phrase)) if p]


def _build_phrase_set() -> frozenset[str]:
    """Kalıpları (ve cümle parçalarını) NORMALİZE edilmiş hâlleriyle sakla — anahtar da
    `normalize_tr()` sonrası metinden üretiliyor, ikisi aynı uzayda olmalı."""
    out: set[str] = set()
    for phrase in CACHED_PHRASES:
        for piece in [phrase, *_sentence_pieces(phrase)]:
            norm = normalize_tr(piece).strip()
            if norm:
                out.add(norm)
    return frozenset(out)


PHRASE_SET = _build_phrase_set()


def is_cacheable(text: str) -> bool:
    """`text` (normalize_tr SONRASI) cache'lenmeli mi? Kalıp listesi VEYA kısa metin."""
    t = (text or "").strip()
    if not t:
        return False
    return len(t) <= CACHE_MAX_CHARS or t in PHRASE_SET


# ── Pinned referans parmak izi ────────────────────────────────────────────────
_ref_cache: dict[str, tuple[float, Optional[str]]] = {}


def _fingerprint_from_payload(payload: object) -> Optional[str]:
    """`GET /api/default` gövdesinden parmak izi. `ref_audio` + `ref_text` varsa onlar
    (anlamlı ve kararlı); yoksa gövdenin tamamı (kararlı JSON sıralamasıyla).

    ⚠️ SINIR: `ref_audio` bir YOL (`/opt/candan-lite/assets/voice/default-ref.wav`).
    Aynı yola BAŞKA bir
    wav yazılıp `ref_text` aynı bırakılırsa parmak izi değişmez → cache eski sesle çalar.
    Pratikte §1.B'nin iki seçeneği de `ref_text`'i değiştiriyor, o yüzden yeterli; yine de
    referansı elle değiştirdikten sonra `python3 -m tts_cache --clear` çalıştırın.
    """
    if isinstance(payload, dict):
        ref_audio = payload.get("ref_audio")
        ref_text = payload.get("ref_text")
        if ref_audio is not None or ref_text is not None:
            material = f"{ref_audio}\x00{ref_text}"
        else:
            material = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    else:
        material = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


async def ref_fingerprint(host: str, port: int) -> Optional[str]:
    """`.25`'teki pinned referansın parmak izi. SALT-OKUMA (`GET /api/default`).

    None dönerse cache DEVRE DIŞI bırakılmalı: hangi referansla üretildiğini bilmediğimiz
    sesi çalmak, yeniden üretmekten daha kötü. Sonuç TTL boyunca bellekte tutulur;
    başarısızlık da (kısa süre) tutulur ki her tur timeout beklemeyelim.
    """
    key = f"{host}:{port}"
    now = time.monotonic()
    hit = _ref_cache.get(key)
    if hit and now - hit[0] < REF_FINGERPRINT_TTL:
        return hit[1]

    fp: Optional[str] = None
    try:
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=REF_FETCH_TIMEOUT)
        async with (
            aiohttp.ClientSession(timeout=timeout) as sess,
            sess.get(f"http://{host}:{port}/api/default") as resp,
        ):
            resp.raise_for_status()
            payload = json.loads(await resp.text())
        fp = _fingerprint_from_payload(payload)
    except Exception as exc:  # noqa: BLE001 — cache bir optimizasyon, tur BOZULMAZ
        logger.warning("pinned referans okunamadı (%s) → ses cache'i devre dışı", exc)
        # Başarısızlığı da TTL'in beşte biri kadar tut: sürekli timeout beklemeyelim.
        _ref_cache[key] = (now - REF_FINGERPRINT_TTL * 0.8, None)
        return None

    _ref_cache[key] = (now, fp)
    return fp


def reset_ref_cache() -> None:
    """Parmak izi belleğini boşalt (test ve prewarm için)."""
    _ref_cache.clear()


# ── Anahtar + disk ────────────────────────────────────────────────────────────
def make_key(text: str, *, ref: str, voice: Optional[str], mood: Optional[str]) -> str:
    """Cache anahtarı. `text` normalize_tr SONRASI olmalı (çağıran öyle verir)."""
    material = "\x00".join((ref, voice or "", mood or "", text))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _path_for(key: str) -> Path:
    return CACHE_DIR / f"{key}.pcm"


def load(key: str) -> Optional[bytes]:
    """Cache'ten ham PCM oku. Yok / okunamıyor / bozuk (tek örnek bile değil) → None."""
    path = _path_for(key)
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) < 2 or len(data) % 2:
        logger.warning("bozuk cache dosyası atıldı: %s", path.name)
        try:
            path.unlink()
        except OSError:
            pass
        return None
    try:  # LRU tahliyesi mtime'a bakıyor → okunan dosya "taze" olsun
        os.utime(path)
    except OSError:
        pass
    return data


def store(key: str, pcm: bytes, *, sample_rate: int, channels: int) -> bool:
    """Ham PCM'i cache'e yaz. Beklenen format dışındaysa yazma (dönüşüm YAPMIYORUZ).

    Yazım atomik (geçici dosya + rename) — yarı yazılmış dosya okunmasın.
    """
    if sample_rate != CACHE_SAMPLE_RATE or channels != CACHE_CHANNELS:
        logger.debug(
            "cache atlandı: format %d Hz/%d kanal (beklenen %d Hz/mono)",
            sample_rate, channels, CACHE_SAMPLE_RATE,
        )
        return False
    if not pcm or len(pcm) % 2:
        return False
    path = _path_for(key)
    tmp = path.with_suffix(".pcm.tmp")
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(pcm)
        tmp.replace(path)
    except OSError as exc:
        logger.warning("cache yazılamadı (%s)", exc)
        try:
            tmp.unlink()
        except OSError:
            pass
        return False
    _evict()
    return True


def _evict() -> None:
    """Dosya sayısı/boyut sınırını aş → en ESKİ dokunulanları sil. Best-effort."""
    try:
        files = sorted(CACHE_DIR.glob("*.pcm"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return
    total = 0
    sizes: list[tuple[Path, int]] = []
    for p in files:
        try:
            size = p.stat().st_size
        except OSError:
            continue
        sizes.append((p, size))
        total += size
    i = 0
    while i < len(sizes) and (len(sizes) - i > CACHE_MAX_FILES or total > CACHE_MAX_BYTES):
        path, size = sizes[i]
        try:
            path.unlink()
            total -= size
        except OSError:
            pass
        i += 1


def clear() -> int:
    """Cache'i boşalt; silinen dosya sayısını döner."""
    n = 0
    try:
        entries = list(CACHE_DIR.glob("*.pcm")) + list(CACHE_DIR.glob("*.pcm.tmp"))
    except OSError:
        return 0
    for p in entries:
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    return n


def stats() -> dict:
    """(dosya sayısı, toplam bayt, toplam ses saniyesi)."""
    try:
        files = list(CACHE_DIR.glob("*.pcm"))
    except OSError:
        files = []
    total = 0
    for p in files:
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return {
        "dir": str(CACHE_DIR),
        "files": len(files),
        "bytes": total,
        "audio_s": round(total / (CACHE_SAMPLE_RATE * CACHE_CHANNELS * 2), 2),
    }


# ── Prewarm (ELLE çalıştırılır) ───────────────────────────────────────────────
async def prewarm(host: str, port: int, *, voice: Optional[str] = None,
                  moods: tuple[Optional[str], ...] = (None,)) -> int:
    """Kalıp listesini (ve cümle parçalarını) üret + cache'e yaz. Otomatik ÇAĞRILMAZ.

    `higgs_tts` üzerinden gider → canlıdaki ile BİREBİR aynı yol/normalizasyon.
    Yalnız sentez isteği atar (`POST /api/tts[/stream]`); referansa/ayara DOKUNMAZ.
    Döner: yazılan dosya sayısı.

    ⚠️ PARMAK İZİ `higgs_tts.ref_fingerprint` OLMALI — bu modülünki DEĞİL. Canlı yol
    (higgs_tts._run_short) anahtarı motor kimliğiyle ÖNEKLİ üretir; burada bu modülün
    kendi parmak izini kullanmak ULAŞILMAYAN anahtarlar yazar (cache hep ıskalar).
    """
    import higgs_tts  # döngüsel importu çalışma anına ertele

    tts = higgs_tts.HiggsTTS(host=host, port=port, voice=voice)
    ref = await higgs_tts.ref_fingerprint(host, port)
    if ref is None:
        print("HATA: pinned referans okunamadı → prewarm iptal (cache anahtarı üretilemez)")
        return 0

    targets = sorted(PHRASE_SET)
    written = 0
    for mood in moods:
        for i, text in enumerate(targets, 1):
            key = make_key(text, ref=ref, voice=voice, mood=mood)
            if load(key) is not None:
                print(f"[{i:3d}/{len(targets)}] atlandı (var): {text[:50]!r}")
                continue
            marked = f"[mood:{mood}] {text}" if mood else text
            try:
                stream = tts.synthesize(marked)
                await stream.collect()
                await stream.aclose()
            except Exception as exc:  # noqa: BLE001 — biri patlarsa kalanlar üretilsin
                print(f"[{i:3d}/{len(targets)}] HATA {type(exc).__name__}: {exc}")
                continue
            ok = load(key) is not None
            written += int(ok)
            print(f"[{i:3d}/{len(targets)}] {'yazıldı' if ok else 'YAZILMADI'}: {text[:50]!r}")
    return written


def _main(argv: list[str]) -> int:
    if "--list" in argv:
        s = stats()
        print(json.dumps(s, ensure_ascii=False, indent=2))
        for p in sorted(CACHE_DIR.glob("*.pcm")) if CACHE_DIR.exists() else []:
            print(f"  {p.name}  {p.stat().st_size} B")
        return 0
    if "--clear" in argv:
        print(f"silinen: {clear()}")
        return 0
    if "--phrases" in argv:
        for t in sorted(PHRASE_SET):
            print(f"{len(t):3d}  {t}")
        print(f"\ntoplam {len(PHRASE_SET)} kalıp/parça")
        return 0
    if "--prewarm" in argv:
        logging.basicConfig(level=logging.INFO)
        host = os.environ.get("HIGGS_TTS_HOST") or os.environ.get("TTS_HOST", "127.0.0.1")
        port = int(os.environ.get("HIGGS_TTS_PORT", "8809"))
        moods: tuple[Optional[str], ...] = (None,)
        if "--moods" in argv:
            from higgs_tts import MOOD_PRESETS
            moods = (None, *MOOD_PRESETS)
        print(f"prewarm → {host}:{port} (mood: {[m or 'nötr' for m in moods]})")
        n = asyncio.run(prewarm(host, port, moods=moods))
        print(f"\nyazılan: {n}  ·  {json.dumps(stats(), ensure_ascii=False)}")
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
