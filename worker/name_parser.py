"""Conservative spoken-name extraction for voice enrollment.

Port of hermes-livekit/voice/name_parser.py (parse_spoken_name) plus the small
reply-classifier helpers used by the enrollment state machine
(is_affirmative_reply, _is_decline_enroll, _is_enroll_command), ported from
hermes-livekit/adapter.py + update_check.py.

The parser intentionally accepts only clear self-identification phrases. If a
sentence is ambiguous, returning None is safer than storing the wrong speaker
name.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional


_FILLER_WORDS = {
    "şey", "sey", "ya", "yani", "işte", "iste", "aslında", "aslinda",
    "hımm", "hmm", "ıı", "ee", "evet", "tamam", "merhaba", "selam",
    "ben", "benim", "adım", "adim", "ismim", "ismin", "isim",
    "actually", "well", "um", "uh", "yes", "yeah", "ok", "okay",
    "hello", "hi", "my", "name", "is", "i", "am", "im", "i'm",
}
_BOUNDARY_WORDS = {
    "ama", "fakat", "ve", "de", "da", "diye", "olarak", "kaydet",
    "kaydedebilirsin", "çağır", "cagir", "dersin", "diyebilirsin",
    "but", "and", "as", "please", "thanks", "thank", "you", "call",
    "me",
}
_NAME_TOKEN_RE = re.compile(r"^[A-Za-zÇĞİÖŞÜçğıöşü][A-Za-zÇĞİÖŞÜçğıöşü'’-]{1,31}$")

# İsim OLAMAYACAK yaygın Türkçe cevap/soru sözcükleri. Canlı hata: parser
# "Efendim" / "Anlamadım" / "Ne dedin" gibi cevapları İSİM sanıyordu (bare-name
# modu ilk sözcüğe bakıp geçiriyordu) → yanlış kişi kaydı riski.
_NON_NAME_WORDS = {
    "efendim", "efendi", "anlamadım", "anlamadim", "anladım", "anladim",
    "anlayamadım", "anlayamadim", "duymadım", "duymadim", "duydun", "dedim",
    "dedin", "diyorsun", "diyorum", "bilmiyorum", "biliyorum", "bilmem",
    "ne", "kim", "kimsin", "kimim", "nasıl", "nasil", "nerede", "neden",
    "niye", "niçin", "nicin", "hangi", "hani", "hava", "bir", "iki", "dakika",
    "saniye", "adını", "adini", "adımı", "adimi", "adın", "adin", "ismini",
    "söylemek", "soylemek", "söyle", "soyle", "söyler", "soyler", "söyledim",
    "istemiyorum", "istiyorum", "sen", "sana", "seni", "senin", "bana",
    "beni", "bende", "bu", "şu", "su", "var", "yok", "lütfen", "lutfen",
    "tekrar", "misin", "mısın", "misiniz", "musun", "müsün", "galiba",
    "sanırım", "sanirim", "tabii", "tabi", "peki", "hadi", "haydi", "oldu",
    "olur", "dur", "bekle", "canım", "canim", "abi", "abla", "anne", "baba",
    "kocam", "eşim", "esim", "hayır", "hayir", "sonra", "şimdi", "simdi",
    "değil", "degil", "günaydın", "gunaydin", "hoşgeldin", "hosgeldin",
    "what", "who", "sorry", "again", "know", "understand", "nothing",
}

# Fiil çekim eki taşıyan sözcük isim olamaz ("Anlamadım", "Bilmiyorum").
# İsim + koşaç eki (-yım/-yim) BU KONTROLDEN ÖNCE soyulur → çakışmaz.
_VERBISH_RE = re.compile(
    r"(?:yorum|yorsun|yoruz|yorlar|madım|madim|medim|medım|mıyorum|miyorum"
    r"|muyorum|müyorum|acağım|eceğim|acagim|ecegim|mişim|misim|mişsin"
    r"|abilir|ebilir|malıyım|meliyim|dınız|diniz|siniz|sınız)$",
    re.IGNORECASE,
)

# Türkçe ek soyma: "Havva'yım" → Havva, "Zeynep'im" → Zeynep, "Ayhan'ın" → Ayhan.
_APOS_SUFFIX_RE = re.compile(r"['’][a-zçğıöşü]{1,4}$")
# Kesme işaretsiz koşaç: "Havvayım" → Havva. Yalnız ünlüden sonraki "y" tamponu
# ile → "Selim"/"Kerim" gibi gerçek isimler bozulmaz.
_COPULA_BARE_RE = re.compile(r"(?<=[aeıioöuüAEIİOÖUÜ])y[ıiuü]m$")

_PREFIX_PATTERNS = (
    r"\b(?:benim\s+)?(?:adım|adim|ismim|isimim)\s+(.+)$",
    r"\bben\s+([A-Za-zÇĞİÖŞÜçğıöşü][A-Za-zÇĞİÖŞÜçğıöşü'’-]*(?:\s+[A-Za-zÇĞİÖŞÜçğıöşü][A-Za-zÇĞİÖŞÜçğıöşü'’-]*)?)\b",
    r"\b(?:bana|beni)\s+(.+?)\s+(?:de|diye\s+çağır|diye\s+cagir|olarak\s+kaydet|kaydet)\b",
    r"\bmy\s+name\s+is\s+(.+)$",
    r"\bi\s*(?:am|'m|m)\s+(.+)$",
    r"\b(?:call\s+me|it's|it\s+is|this\s+is)\s+(.+)$",
)

# İSİM-ÖNDE kalıpları (prefix'ler tutmazsa denenir). Canlı hata: evin annesi
# "Havi adım" dedi — kalıplar yalnız "adım X" biçimini biliyordu, "X adım"
# biçimini bilmiyordu → isim anlaşılamadı → guest'e düştü.
_SUFFIX_PATTERNS = (
    r"^(.+?)\s+(?:adım|adim|ismim|isimim|adımdır|adimdir|ismimdir)\b",
    r"^(?:bana\s+)?(.+?)\s+(?:diyebilirsin|dersin|derler|diye\s+çağır"
    r"|diye\s+cagir|olarak\s+kaydet)\b",
)


def _strip_suffix(word: str) -> str:
    """Koşaç/iyelik ekini soy: "Havva'yım" → "Havva", "Havvayım" → "Havva"."""
    stripped = _APOS_SUFFIX_RE.sub("", word)
    if stripped != word and len(stripped) >= 2:
        return stripped
    stripped = _COPULA_BARE_RE.sub("", word)
    if stripped != word and len(stripped) >= 3:
        return stripped
    return word


def _looks_like_name(word: str) -> bool:
    """Sözcük isim OLABİLİR mi? (biçim + kara liste + fiil eki)"""
    if not _NAME_TOKEN_RE.match(word):
        return False
    low = word.casefold()
    if low in _NON_NAME_WORDS or low in _FILLER_WORDS or low in _BOUNDARY_WORDS:
        return False
    return not _VERBISH_RE.search(low)


def _normalize_for_match(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("’", "'")
    text = re.sub(r"[\"“”‘]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize(text: str) -> str:
    text = _normalize_for_match(text)
    text = re.sub(r"[.!?,;:]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _candidate_span(text: str, start: int, end: int) -> str:
    candidate = text[start:end]
    # Enrollment phrases often continue as a sentence: "Ben Ayhan. Bugün ...".
    # The name candidate must stop at sentence punctuation before normalization
    # erases that boundary.
    return re.split(r"[.!?,;:]+", candidate, maxsplit=1)[0].strip()


def _clean_candidate(candidate: str) -> list[str]:
    words = []
    for raw in re.split(r"\s+", _normalize(candidate)):
        word = raw.strip("-_ ")
        low = word.casefold()
        if not word:
            continue
        if low in _FILLER_WORDS:
            continue
        if low in _BOUNDARY_WORDS:
            break
        word = _strip_suffix(word)
        if not _looks_like_name(word):
            break
        words.append(word)
        if len(words) == 2:
            break
    return words


def _starts_with_rejected_word(candidate: str) -> bool:
    parts = [p for p in re.split(r"\s+", _normalize(candidate)) if p]
    if not parts:
        return True
    first = parts[0].casefold()
    return first in _FILLER_WORDS or first in _BOUNDARY_WORDS


def _format_name(words: list[str]) -> Optional[str]:
    if not words:
        return None
    if any(w.casefold() in _FILLER_WORDS or w.casefold() in _BOUNDARY_WORDS for w in words):
        return None
    name = " ".join(words).strip()
    return name[:40].title() if name else None


def parse_spoken_name(text: str) -> Optional[str]:
    match_text = _normalize_for_match(text)
    normalized = _normalize(match_text)
    if not normalized:
        return None
    for pattern in _PREFIX_PATTERNS + _SUFFIX_PATTERNS:
        match = re.search(pattern, match_text, flags=re.IGNORECASE)
        if not match:
            continue
        original_candidate = _candidate_span(match_text, match.start(1), match.end(1))
        if _starts_with_rejected_word(original_candidate):
            continue
        name = _format_name(_clean_candidate(original_candidate))
        if name:
            return name

    words = _clean_candidate(normalized)
    # Bare-name modu: dolgu sözcükleri ("ben", "adım", "evet") atıldıktan sonra
    # geriye 1–2 isim-benzeri sözcük kalmalı → "Havva ben" / "Havva'yım" geçer.
    # Uzun doğal cümle bare mod'dan GEÇMEZ (isim orada ancak kalıpla çıkarılır)
    # — böylece "Ne diyorsun sen" gibi sözler isim sanılmaz.
    core = [w for w in normalized.split() if w.casefold() not in _FILLER_WORDS]
    if words and len(words) == len(core) <= 2:
        return _format_name(words)
    return None


# ---------------------------------------------------------------------------
# Reply classifiers (port: update_check.is_affirmative_reply + adapter helpers)
# ---------------------------------------------------------------------------
_AFFIRMATIVE_WORDS = {
    "evet", "güncelle", "guncelle", "yükle", "yukle", "olur",
    "onayla", "yes", "update", "tamam", "kaydet",
}


def is_affirmative_reply(text: str) -> bool:
    # TAM KELİME eşleşmesi (substring değil) → "çok/yok" gibi sözler onaylamaz.
    words = set(re.findall(r"[a-zçğıöşü0-9]+", (text or "").casefold()))
    return bool(words & _AFFIRMATIVE_WORDS)


# Onboarding/enroll sorusuna ret — isim sorulunca bunlardan biri gelirse akış
# sessizce iptal (kullanıcı tanıtılmak istemiyor). Kısa söz olmalı; uzun cümlede
# tesadüfi geçiş tetiklemesin (isim "hayırcan" gibi başlayabilir).
_DECLINE_PHRASES = (
    "istemiyorum", "gerek yok", "gerekmiyor", "hayır", "hayir", "yok",
    "sonra", "şimdi değil", "simdi degil", "boş ver", "bos ver", "vazgeç",
    "no", "not now", "maybe later", "leave it", "skip",
)


def _is_decline_enroll(text: str) -> bool:
    low = re.sub(r"\s+", " ", (text or "").casefold()).strip().rstrip(".!?")
    if not low or len(low.split()) > 4:
        return False
    return any(low == p or low.startswith(p + " ") or low.endswith(" " + p)
               for p in _DECLINE_PHRASES)


# Açık enrollment komutları — bunlardan biri geçerse enroll başlatılabilir.
# Kısa (≤6 kelime) sözde geçmeli; uzun cümle içinde tesadüfi geçiş tetiklemez.
_ENROLL_PHRASES = (
    "beni kaydet", "beni tanı", "sesimi kaydet", "sesimi öğren",
    "beni öğren", "beni hatırla", "sesimi tanı",
    "enroll me", "remember me", "register me", "remember my voice",
)


def _is_enroll_command(text: str) -> bool:
    low = re.sub(r"\s+", " ", (text or "").casefold()).strip().rstrip(".!?")
    if not low or len(low.split()) > 6:
        return False
    return any(p in low for p in _ENROLL_PHRASES)
