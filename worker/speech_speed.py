"""speech_speed — konuşma hızı KADEMELERİ. Tek kaynak; hem TTS hem denetim buradan okur.

NEDEN VAR (canlı, 27 Tem 18:21 ve 18:33): kullanıcı üst üste "biraz daha hızlı
konuş" dedi; Candan her seferinde "hızımı artırıyorum", hatta **"iki BİRİM daha
artırıyorum"** dedi. Ne birim vardı, ne de hız değişti — elinde hız kolu YOKTU.
`truth_check`'in kapattığı "araç yalanı"nın kardeşi: **yetenek yalanı**.

İKİ TASARIM KARARI, ikisi de o canlı kayıttan çıktı:

  1. **Kademeler SINIRLI ve ADLANDIRILMIŞ.** Serbest sayı olsaydı model "iki birim"
     uydurmasını sürdürürdü. Dört kademe var, hepsinin ÖLÇÜLMÜŞ bir karşılığı var
     ve `[speed:X]` MUTLAK kademeyi söyler ("bir tık artır" değil). Tavan/taban
     aşılamaz — clamp burada, `step()` içinde.
  2. **Kalıcı ayar, cümlelik süs değil.** Kullanıcının şikâyeti tam buydu: bir sonraki
     cevapta eski tempoya dönmek. Kademe TTS eklentisinde oturum boyunca yaşar;
     `reset_mood()` (tur sınırı) ona DOKUNMAZ.

ORANLAR ÖLÇÜLDÜ (`experiments/konusma-hizi/`, 12 örnek/koşul, canlı streaming ucu,
Whisper geri-dönüşü — taban WER 0.004):

    kademe      oran   kelime/s   kazanç   WER
    slow        0.85     2.124    -%15.0   0.004
    normal      1.00     2.498       —     0.004
    fast        1.15     2.868    +%14.8   0.004
    very_fast   1.30     3.239    +%29.7   0.004

Anlaşılırlık HİÇBİR kademede bozulmuyor (WER tabanla aynı, 12/12 anlaşıldı).
1.45 de ölçüldü (+%44.4, WER 0.004) ama KADEME DEĞİL: tavanı yükseltmek kulak
testine bağlı (`experiments/konusma-hizi/hiz-seti.html`).

⚠️ Higgs'in kendi `<|prosody:speed_fast|>` token'ı bu iş için REDDEDİLDİ: aynı
ölçümde en fazla +%7.2 kazandırdı (ölçütümüz ≥%15) ve WER'i 0.004 → 0.030'a
çıkardı. Motorun `speed` gövde parametresi de aday değil — `server/higgs-tts/server.py`
sözleşmesinde YAZIYOR ama kod onu hiç okumuyor (canlı doğrulaması da yapıldı).
"""
from __future__ import annotations

import re
from typing import Optional

# Yavaştan hızlıya SIRALI. Sıra anlamlıdır: `step()`/`index()` buna dayanır.
LEVELS: tuple[str, ...] = ("slow", "normal", "fast", "very_fast")
DEFAULT = "normal"

# Kademe → tempo oranı (>1 hızlandırır). Hepsi ölçüldü (bkz. modül başı).
RATES: dict[str, float] = {
    "slow": 0.85,
    "normal": 1.0,
    "fast": 1.15,
    "very_fast": 1.30,
}

# Kullanıcıya/loga giden Türkçe ad.
TR: dict[str, str] = {
    "slow": "yavaş",
    "normal": "normal",
    "fast": "hızlı",
    "very_fast": "çok hızlı",
}

# `[speed:X]` KONTROL işareti — seslendirilmez, metinden SİLİNİR. Desen LEVELS'ten
# üretilir ki yeni kademe eklenince regex'i güncellemek unutulmasın (aynı gerekçe
# `higgs_tts._MOOD_RE`'de de var: unutulan regex etiketi SESLİ okuturdu).
TAG_RE = re.compile(r"\s*\[speed:(" + "|".join(LEVELS) + r")\]\s*", re.IGNORECASE)


def is_level(level: Optional[str]) -> bool:
    return level in RATES


def rate(level: Optional[str]) -> float:
    """Kademenin tempo oranı. Bilinmeyen/None → 1.0 (BUGÜNKÜ hız, hiç işlem yok)."""
    return RATES.get(level or "", 1.0)


def index(level: Optional[str]) -> int:
    """Kademenin sırası; bilinmeyen → `normal`in sırası."""
    try:
        return LEVELS.index(level or "")
    except ValueError:
        return LEVELS.index(DEFAULT)


def step(level: Optional[str], n: int) -> str:
    """`n` kademe kaydır, UÇLARDA DURUR (clamp). Tavan/taban tek yerde tanımlı."""
    i = min(max(index(level) + n, 0), len(LEVELS) - 1)
    return LEVELS[i]


def at_ceiling(level: Optional[str]) -> bool:
    return index(level) >= len(LEVELS) - 1


def at_floor(level: Optional[str]) -> bool:
    return index(level) <= 0


def requested(text: str) -> Optional[str]:
    """Metindeki İLK `[speed:X]` işareti (yoksa None). Küçük harfe indirilir.

    DİKKAT: burada "anlatılıyor mu kullanılıyor mu" ayrımı YAPILMAZ; onu
    `higgs_tts._extract_speed` yapıyor (mention koruması `_is_mention` ile paylaşılır).
    Bu işlev denetim tarafının ucuz sorusu için: "model bu turda hız işareti yazdı mı".
    """
    if not text or "[" not in text:
        return None
    m = TAG_RE.search(text)
    return m.group(1).lower() if m else None


__all__ = [
    "DEFAULT", "LEVELS", "RATES", "TAG_RE", "TR",
    "at_ceiling", "at_floor", "index", "is_level", "rate", "requested", "step",
]
