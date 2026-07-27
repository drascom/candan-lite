"""barge — sözü kesildiğinde: YENİ KOMUT mu, sadece SOHBET mi?

CANLI VAKA (27 Tem 18:37): Candan duygu örneklerini sayarken kullanıcı araya girip
"Beni duydun mu?" dedi. Cevap "Harika bir haber"de kesildi; Candan da *"Evet, seni
duydum. Az önceki örnekleri dinledin sanırım."* diye YANLIŞ varsaydı — kullanıcı
hiçbir örneği duymamıştı. İki ayrı kusur: (a) kesilen cevap ölüyordu, (b) model
kesilen metnin DUYULDUĞUNU sanıyordu.

KULLANICININ İKİ KARARI (tasarımı bunlar belirler, değiştirilmez):

  1. **Sohbet sayılırsa KALDIĞI CÜMLENİN BAŞINDAN devam.** Yarım kalan cümle baştan
     söylenir. Küçük bir tekrar olur ama kopukluk olmaz — insan da böyle yapar.
     Metin YENİDEN ÜRETİLMEZ: elde olan metin kullanılır (modele gidilmez → 0 token,
     0 ek gecikme, içerik değişmez).
  2. **Şüphede YENİ KOMUT say, sussun.** Yanlış kararın telafisi kolay ("devam et"
     demek yeter); tersinde kullanıcının sözünü İKİNCİ kez kesmesi gerekirdi.

SIRA — ucuzdan pahalıya (`truth_check`'in MALİYET KAPISI deseninin aynısı; kesme
anı gecikmeye duyarlı, tipik tur sınıflandırıcıya HİÇ gitmemeli):

  0. Kesme YOKSA hiçbir şey çalışmaz. Tipik turda bu dosyadan tek satır bile
     yürütülmez (`Pending` yoksa `decide` çağrılmaz).
  1. **Geri-bildirim sözlüğü** — tam metin eşleşmesi ("hı hı", "tamam", "aynen",
     kahkaha, isim seslenişi "Candan?"). Deterministik, bedava.
  2. **Yeni-istek işaretleri** — soru işareti, soru eki/zamiri, emir kipi eylem
     fiilleri ("yap/söyle/aç/kapat/anlat/dur/bekle"). Deterministik, bedava.
  3. **Çok kısa ifade** (≤2 kelime, yukarıdakilere takılmadıysa) → sohbet.
  4. Kalan belirsiz durum → `truth_check` katman-3'ün AYNI ucu/deseni (küçük LLM,
     ~115 ms). Yeni model/mekanizma YOK.
  5. Sınıflandırıcı da kararsız/erişilemez → **yeni-istek** (karar 2).

GEÇMİŞ DÜRÜSTLÜĞÜ: kesilen metin konuşma geçmişine "söylenmiş" gibi girmez.
`unheard_note()` bir sonraki pi prompt'una GERÇEKTEN duyulan kısmı bildirir —
`truth_check` ilkesinin aynısı: harness NE OLDUĞUNU bilir, model tahmin etmez.

BAYRAK: `BARGE_RESUME_ENABLED` (varsayılan açık). Kapalıyken davranış bugünküyle
BİRE BİR aynı — çağıran taraf her kancayı bu bayrakla kapatır.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import urllib.error
from dataclasses import dataclass, field
from typing import Optional

import truth_check
from truth_check import fold

logger = logging.getLogger("barge")


def _envflag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _envfloat(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


# Ana bayrak. Kapalı → kancaların HEPSİ no-op, bugünkü davranış.
RESUME_ENABLED = _envflag("BARGE_RESUME_ENABLED", True)
# Devam bekleyen metnin ömrü. Kullanıcı konuyu değiştirdiyse eski cevabın yarısı
# yarım dakika sonra ortaya çıkmamalı.
RESUME_TTL = _envfloat("BARGE_RESUME_TTL", 30.0)
# Kesilen cümlenin NE KADARI duyulduysa onu tekrar etmeden sonrakinden devam et.
# Tek kural iki durumu birden kapatır: "neredeyse bitmişti" ve "zaten çok kısaydı,
# neredeyse tamamı duyuldu". 1.0'a çekilirse kesilen cümle HER ZAMAN baştan söylenir.
NEAR_END_RATIO = _envfloat("BARGE_RESUME_NEAR_END_RATIO", 0.85)
# Deterministik kapı kararsız kalırsa sorulacak uç: truth_check katman-3 ile AYNI.
BARGE_CHECK_ENABLED = _envflag("BARGE_CHECK_ENABLED", True)
BARGE_CHECK_TIMEOUT = _envfloat("BARGE_CHECK_TIMEOUT", 0.6)

CHAT = "chat"          # sohbet/geri-bildirim → kaldığı yerden devam
NEW = "new"            # yeni istek → kalan atılır, bugünkü davranış

# TTS/anlatım işaretleri (`[mood:x]`, `[speed:x]`, duraklama token'ları). Hem
# hizalamada hem "kaç harf duyuldu" ölçümünde HARİÇ tutulur: odaya giden transkript
# bunları temizler, ham metin temizlemez — ikisini aynı ölçüye indirmezsek kesme
# noktası kayardı.
_MARKUP_RE = re.compile(r"\[[^\[\]\n]{0,60}\]")
_MOOD_HEAD_RE = re.compile(r"\[mood:[^\[\]\n]{1,30}\]", re.IGNORECASE)


# ── Geri-bildirim sözlüğü (tam metin eşleşmesi) ──────────────────────────────
# Kullanıcı bunları söylediğinde ANLATIYI KESMEK istemez, "seni dinliyorum" der.
# Tam metin eşleşmesi ŞART: "candan" → sohbet ama "candan saat kaç" → yeni istek.
_FEEDBACK: frozenset[str] = frozenset({
    # onay / tasdik
    "evet", "evet evet", "he", "hee", "ha", "hi hi", "hihi", "hi", "hm", "hmm",
    "mhm", "aha", "haa", "tamam", "tamam tamam", "tamamdir", "peki", "pekala",
    "anladim", "anlasildi", "dogru", "aynen", "aynen oyle", "aynen aynen",
    "kesinlikle", "elbette", "tabii", "tabi", "tabii ki", "ok", "okey", "okay",
    "olur", "oldu", "iyi", "iyi iyi", "katiliyorum", "hakli", "haklisin",
    "biliyorum", "eyvallah",
    # beğeni / tepki
    "guzel", "cok guzel", "harika", "super", "muhtesem", "vay", "vay be",
    "vay canina", "oha", "yok artik", "inanmiyorum", "ilginc", "enteresan",
    "maasallah", "insallah", "ya", "yaa", "oyle mi", "sahi mi", "gercekten mi",
    "hadi ya", "olamaz",
    # kahkaha
    "haha", "hahaha", "hah", "hehe", "hihihi", "ha ha", "ha ha ha",
    # teşekkür
    "tesekkurler", "tesekkur ederim", "sagol", "sag ol", "sagolun",
    # isim seslenişi / dinleme sinyali
    "candan", "candancim", "efendim", "dinliyorum", "seni dinliyorum",
    # açık "devam" (emir kipi işaretine TAKILMADAN önce burada eşleşir)
    "devam", "devam et", "devam etsene", "devam edebilirsin",
})

# ── Yeni-istek işaretleri (deterministik) ────────────────────────────────────
# Soru eki/zamiri. Türkçe soru eki ayrı yazılır ("gider mi") ya da çekimlenir
# ("gidiyor musun") — ikisi de burada.
_QUESTION_RE = re.compile(
    r"\b(mi|mu|mi̇|mu̇|misin|musun|miyim|muyum|miyiz|muyuz|misiniz|musunuz"
    r"|miydi|muydu|mudur|midir"
    r"|ne|neyi|neye|nede|neden|niye|nicin|nasil|kim|kimi|kime|kimin"
    r"|nerede|nereye|nereden|neresi|hangi|hangisi|kac|kacta|kaci)\b"
)
# Emir kipi / eylem talebi fiilleri. Kök + yaygın emir ekleri.
_ACTION_RE = re.compile(
    r"\b(yap|soyle|anlat|ac|kapat|dur|bekle|sus|kes|birak|getir|goster|oku|yaz"
    r"|ara|bul|cal|oynat|gonder|hatirlat|kaydet|unut|baslat|bitir|iptal|sil"
    r"|degistir|ayarla|azalt|artir|arttir|yukselt|dusur|yavasla|hizlan|hizlandir"
    r"|atla|gec|ver|sor|dinle|bak|gel|git|tekrarla|listele|hesapla|cevir|coz"
    r"|acikla|ozetle|yardim|kur|olustur|ekle|cikar|kontrol)"
    r"(sana|sene|senize|saniza|iver|iversene|in|iniz|alim|elim|ayim|eyim)?\b"
)
# Emir kipine benzeyip ASLINDA geri-bildirim olan kelimeler (sözlükte tam metin
# olarak da var; burada çok kelimeli ifadelerde yanlış "yeni istek" demesin diye).
_ACTION_EXEMPT_RE = re.compile(r"\b(devam et|seni dinliyorum|dinliyorum seni)\b")


def classify_fast(text: str) -> tuple[Optional[str], bool]:
    """(karar, sınıflandırıcıya sorulsun mu) — deterministik kapı.

    Dönüşler:
      ("chat", False) → geri-bildirim; kaldığı yerden devam.
      ("new",  False) → yeni istek; kalan atılır (bugünkü davranış).
      (None,   True)  → belirsiz; çağıran isterse küçük LLM'e sorar.

    **Tipik kesme turu burada kapanır**: sözlük ya da işaret listesi tutar,
    LLM ÇAĞRILMAZ. Ölçüm testte kilitli (test_barge_resume).
    """
    raw = (text or "").strip()
    if not raw:
        return NEW, False                      # sessizlik/gürültü → sussun (karar 2)
    low = fold(_MARKUP_RE.sub(" ", raw))
    # Noktalama at (fold zaten büyük/küçük ve Türkçe-i'yi katladı).
    flat = re.sub(r"[^\w\s]+", " ", low)
    flat = re.sub(r"\s+", " ", flat).strip()
    if not flat:
        return NEW, False
    # 1) Geri-bildirim sözlüğü — tam metin. "Candan?" burada eşleşir, soru
    #    işaretine TAKILMADAN: isim seslenişi bir istek değildir.
    if flat in _FEEDBACK:
        return CHAT, False
    # 2) Yeni-istek işaretleri.
    if "?" in raw:
        return NEW, False
    if not _ACTION_EXEMPT_RE.search(flat) and (
        _QUESTION_RE.search(flat) or _ACTION_RE.search(flat)
    ):
        return NEW, False
    # 3) Çok kısa ifade (≤2 kelime) → geri-bildirim say. Buraya gelen kısa
    #    ifadeler soru/emir işareti TAŞIMIYOR demektir.
    if len(flat.split()) <= 2:
        return CHAT, False
    # 4) Belirsiz → çağıran sınıflandırıcıya sorabilir.
    return None, True


BARGE_CHECK_PROMPT = (
    "Konuşan sesli asistanın sözünü kesen bu ifade YENİ BİR İSTEK/SORU mu, yoksa "
    "yalnızca onay/tepki (asistan devam etsin) mi? Yalnız YENI veya TEPKI yaz."
)


def parse_verdict(answer: str) -> Optional[str]:
    """Yargıcın cevabını çöz. YENI→'new', TEPKI→'chat', anlaşılmadı→None."""
    low = fold(answer)
    if not low:
        return None
    if "tepki" in low or "onay" in low:
        return CHAT
    if "yeni" in low or "istek" in low or "soru" in low:
        return NEW
    return None


async def classify_llm(text: str, *, timeout: Optional[float] = None) -> Optional[str]:
    """Belirsiz kesmeyi küçük LLM'e sor. BEST-EFFORT: erişilemez/yavaş → None.

    `truth_check` katman-3'ün AYNI ucunu, AYNI taşımasını ve AYNI zaman aşımı
    davranışını kullanır (yeni mekanizma yok). None dönerse çağıran karar 2'ye
    düşer: yeni-istek say, sussun."""
    said = (text or "").strip()
    url = truth_check.CLAIM_CHECK_URL
    if not BARGE_CHECK_ENABLED or not said or not url:
        return None
    said = said[: truth_check.CLAIM_CHECK_MAX_CHARS]
    payload = {
        "model": truth_check.CLAIM_CHECK_MODEL,
        "messages": [
            {"role": "user", "content": f"{BARGE_CHECK_PROMPT}\n\nİfade: {said}"}
        ],
        "max_tokens": 3,
        "temperature": 0,
        "stream": False,
    }
    budget = BARGE_CHECK_TIMEOUT if timeout is None else timeout
    try:
        answer = await asyncio.wait_for(
            asyncio.to_thread(truth_check._post_sync, url, payload, budget),
            timeout=budget + 0.2,
        )
    except (asyncio.TimeoutError, urllib.error.URLError, OSError, ValueError) as exc:
        logger.info("kesme sınıflandırıcı atlandı (%s)", exc.__class__.__name__)
        return None
    except Exception:  # noqa: BLE001 — yargıç ASLA turu düşürmez
        logger.warning("kesme sınıflandırıcı beklenmedik hata", exc_info=True)
        return None
    return parse_verdict(answer)


async def classify(text: str) -> str:
    """Kesen ifadenin sınıfı: 'chat' | 'new'. Şüphede 'new' (kullanıcının kararı)."""
    verdict, ask = classify_fast(text)
    if verdict is not None:
        return verdict
    if not ask:
        return NEW
    return await classify_llm(text) or NEW


# ── Cümle sınırları ve "nerede kesildi" ──────────────────────────────────────
def split_sentences(text: str) -> list[str]:
    """Metni cümlelere böl. Parçaların birleşimi metnin AYNISIDIR (kayıp yok).

    Sayı içindeki nokta (18.30) sınır sayılmaz. Ayırıcıdan sonra boşluk/metin
    sonu aranır: kısaltma ya da url ortasında bölmemek için."""
    out: list[str] = []
    start = 0
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in ".!?…":
            j = i
            while j < n and text[j] in ".!?…":
                j += 1
            if c == "." and i > 0 and text[i - 1].isdigit() and j < n and text[j].isdigit():
                i = j
                continue
            k = j
            while k < n and text[k] in " \t\r\n":
                k += 1
            if k >= n or k > j:          # ayırıcıdan sonra boşluk ya da metin sonu
                out.append(text[start:k])
                start = k
                i = k
                continue
            i = j
            continue
        i += 1
    if start < n:
        out.append(text[start:])
    return out


def _speakable_mask(text: str) -> list[int]:
    """Metnin SESLENDİRİLEN harf/rakam konumları (markup hariç), sırayla."""
    spans = [(m.start(), m.end()) for m in _MARKUP_RE.finditer(text)]
    idx: list[int] = []
    for pos, ch in enumerate(text):
        if not ch.isalnum():
            continue
        if any(a <= pos < b for a, b in spans):
            continue
        idx.append(pos)
    return idx


def speakable_len(text: str) -> int:
    """Kaç seslendirilebilir harf/rakam var (markup hariç)."""
    return len(_speakable_mask(text))


def cut_index(full: str, spoken: str) -> int:
    """`spoken` kadarı söylendiyse `full` içinde kesme NEREDE oldu (karakter indeksi).

    Karakter karakter KARŞILAŞTIRMAZ — yalnız SAYAR. Odaya giden transkript
    markup'ı temizler, noktalamayı değiştirebilir; iki metni "seslendirilen
    harf sayısı" ölçüsüne indirip o kadarını `full` üzerinde tüketiriz. Böylece
    hizalama biçim farklarından etkilenmez."""
    heard = speakable_len(spoken)
    if heard <= 0:
        return 0
    mask = _speakable_mask(full)
    if heard >= len(mask):
        return len(full)
    return mask[heard - 1] + 1


def resume_from(full: str, spoken: str, *, near_end_ratio: float = NEAR_END_RATIO) -> str:
    """Kesilen cevabın DEVAMI: kaldığı cümlenin BAŞINDAN + kalan cümleler.

    Karar 1 (kullanıcının): yarım kalan cümle baştan söylenir. TEK istisna,
    kullanıcının kendi ölçüsü: cümle neredeyse bitmişse (`near_end_ratio`)
    tekrar etmek yapay durur → sonraki cümleden devam edilir. Aynı kural "cümle
    zaten çok kısaydı, tamamı duyuldu" durumunu da kapatır.

    Metin YENİDEN ÜRETİLMEZ: burada yalnız elde olan metin kırpılır."""
    if not full:
        return ""
    cut = cut_index(full, spoken)
    sents = split_sentences(full)
    if not sents:
        return ""
    pos = 0
    idx = len(sents) - 1
    for k, s in enumerate(sents):
        if cut < pos + len(s):
            idx = k
            break
        pos += len(s)
    else:
        pos = len(full) - len(sents[-1])
    sent = sents[idx]
    total = speakable_len(sent)
    heard = speakable_len(full[pos:cut]) if cut > pos else 0
    # Neredeyse bitmişti → tekrar etme, sonraki cümleden sür. Aksi hâlde kaldığı
    # cümlenin BAŞINDAN (kullanıcının kararı 1).
    near_end = bool(total) and heard / total >= near_end_ratio
    start = pos + len(sent) if near_end else pos
    rest = full[start:].strip()
    if not rest or not speakable_len(rest):
        return ""
    if start > 0:
        # Cevabın BAŞINDAKİ `[mood:X]` işareti turun tonunu kuruyordu; devam ederken
        # ton sıfırlanmış olur (agent.py `reset_mood` her yeni turda). Aynı renkle
        # sürsün diye o işaret devam metninin önüne geri konur.
        # `[speed:X]` BİLEREK taşınmaz: hız kalıcı bir ayardır, oturumda zaten
        # yürürlüktedir; ikinci kez uygulamak kademeyi tekrar oynatırdı.
        m = _MOOD_HEAD_RE.search(full[:80])
        if m and not _MOOD_HEAD_RE.search(rest[:80]):
            rest = m.group(0) + " " + rest
    return rest


# ── Bekleyen devam (ömrü sınırlı) ────────────────────────────────────────────
@dataclass
class Pending:
    """Kesilmiş bir cevap: modelin ÜRETTİĞİ tam metin + GERÇEKTEN duyulan kısım."""

    full: str
    spoken: str
    at: float = field(default_factory=time.monotonic)

    def expired(self, now: Optional[float] = None, ttl: float = RESUME_TTL) -> bool:
        return ((now if now is not None else time.monotonic()) - self.at) > ttl

    def resume(self) -> str:
        return resume_from(self.full, self.spoken)


def unheard_note(spoken: str, max_chars: int = 200) -> str:
    """pi'ya giden GERÇEK: kesilen cevabın nesi duyuldu, nesi duyulmadı.

    `truth_check` ilkesi: harness NE OLDUĞUNU söyler, model tahmin etmez. Canlı
    hatanın ikinci yarısı buydu — model kesilen metnin duyulduğunu varsaydı."""
    heard = re.sub(r"\s+", " ", _MARKUP_RE.sub(" ", spoken or "")).strip()
    if not heard:
        return (
            "(Sistem — GERÇEK: bir önceki cevabın sözü daha DUYULMADAN kesildi. "
            "Kullanıcı o cevaptan HİÇBİR ŞEY duymadı; duyduğunu varsayma.)"
        )
    if len(heard) > max_chars:
        heard = "…" + heard[-max_chars:]
    return (
        "(Sistem — GERÇEK: bir önceki cevabın sözü kesildi. Kullanıcı YALNIZCA şu "
        f"kadarını duydu: \"{heard}\" — gerisi SÖYLENMEDİ. Duyulduğunu varsayma, "
        "gerekiyorsa kısaca özetle ya da kaldığın yerden anlat.)"
    )


__all__ = [
    "CHAT",
    "NEAR_END_RATIO",
    "NEW",
    "RESUME_ENABLED",
    "RESUME_TTL",
    "Pending",
    "classify",
    "classify_fast",
    "classify_llm",
    "cut_index",
    "parse_verdict",
    "resume_from",
    "speakable_len",
    "split_sentences",
    "unheard_note",
]
