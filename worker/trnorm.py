"""trnorm — TTS öncesi Türkçe metin normalizasyonu. Bağımsız, taşınabilir modül.

    from trnorm import normalize_tr
    normalize_tr("Bugün %25 indirim var, 3.500 TL yerine 2.658 TL.")
    # 'Bugün yüzde yirmi beş indirim var, üç bin beş yüz lira yerine iki bin altı yüz elli sekiz lira.'

Selftest:  python3 trnorm.py --selftest

──────────────────────────────────────────────────────────────────────────────────────
NEDEN KENDİMİZ YAZIYORUZ
──────────────────────────────────────────────────────────────────────────────────────
• PyPI'da `trnorm` / `turkish-normalizer` / `turkish-text-normalizer` / `tr-normalizer` /
  `turknorm` paketlerinin HİÇBİRİ yok (hepsi 404). VoxCPM2 TR LoRA kartındaki "trnorm"
  yayınlanmış bir paket değil.
• `num2words` Türkçe biliyor AMA çıktısı BİTİŞİK: num2words(3500,lang="tr") →
  "üçbinbeşyüz". TTS'e bitişik dev kelime gitmesi istenmiyor; geri bölmek için morfem
  ayrıştırıcı gerekir, o da hataya açık. Türkçe sayı sistemi tamamen DÜZENLİ olduğu için
  boşluklu üretimi doğrudan yazmak hem kısa hem kesin.
• OmniVoice'un kendi `normalize_text=True`'su Çince/İngilizce dışındaki dillerde YALNIZ
  çıplak tam sayıları çeviriyor → `%25`, `3.500` (binlik ayıraç), `14:30'da` (kesme ekli)
  ham kalıyor.

Bağımlılık YOK (yalnız stdlib) — worker/'a olduğu gibi kopyalanabilir.
"""
from __future__ import annotations

import re
import sys

__all__ = ["normalize_tr"]

# ── Sayı → yazı (boşluklu) ────────────────────────────────────────────────────────────
_ONES = ["", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz"]
_TENS = ["", "on", "yirmi", "otuz", "kırk", "elli", "altmış", "yetmiş", "seksen", "doksan"]
_SCALES = [(10**9, "milyar"), (10**6, "milyon"), (10**3, "bin")]


def _under_1000(n: int) -> list[str]:
    out: list[str] = []
    h, rest = divmod(n, 100)
    if h:
        out.append("yüz" if h == 1 else f"{_ONES[h]} yüz")  # "bir yüz" DEĞİL
    t, o = divmod(rest, 10)
    if t:
        out.append(_TENS[t])
    if o:
        out.append(_ONES[o])
    return out


def number_to_words(n: int) -> str:
    """Tam sayı → boşluklu Türkçe. 3500 → 'üç bin beş yüz'."""
    if n < 0:
        return "eksi " + number_to_words(-n)
    if n == 0:
        return "sıfır"
    parts: list[str] = []
    for scale, name in _SCALES:
        q, n = divmod(n, scale)
        if not q:
            continue
        # "bir bin" DEĞİL "bin"; ama "bir milyon" DOĞRU
        parts.append(name if (q == 1 and name == "bin") else f"{number_to_words(q)} {name}")
    parts += _under_1000(n)
    return " ".join(parts)


# ── Ünlü uyumu + ünsüz benzeşmesi (kesme işaretli ekler için) ─────────────────────────
_VOWELS = "aeıioöuü"
_VOICELESS = "fstkçşhp"  # "fıstıkçı şahap" — sonrasında d→t


def _last_vowel(word: str) -> str:
    for ch in reversed(word.lower()):
        if ch in _VOWELS:
            return ch
    return "a"


def _h2(word: str) -> str:
    """2'li uyum: a/e."""
    return "a" if _last_vowel(word) in "aıou" else "e"


def _h4(word: str) -> str:
    """4'lü uyum: ı/i/u/ü."""
    v = _last_vowel(word)
    return {"a": "ı", "ı": "ı", "e": "i", "i": "i", "o": "u", "u": "u", "ö": "ü", "ü": "ü"}[v]


# Ham ek → ek tipi. Sayıdan sonra en sık gelenler.
_SUFFIX_TYPES = {
    **dict.fromkeys(["da", "de", "ta", "te"], "LOC"),
    **dict.fromkeys(["dan", "den", "tan", "ten"], "ABL"),
    **dict.fromkeys(["lı", "li", "lu", "lü"], "LI"),
    **dict.fromkeys(["lık", "lik", "luk", "lük"], "LIK"),
    **dict.fromkeys(["a", "e", "ya", "ye"], "DAT"),
    **dict.fromkeys(["ı", "i", "u", "ü", "yı", "yi", "yu", "yü"], "ACC"),
    **dict.fromkeys(["ın", "in", "un", "ün", "nın", "nin", "nun", "nün"], "GEN"),
}


def attach_suffix(spoken: str, raw_suffix: str) -> str:
    """Yazıya çevrilmiş sayıya kesme işaretli eki uyumlu biçimde ekle.

    'bin dokuz yüz doksan dört' + 'te' → '... dörtte'   (ö → e, t sert → te)
    'otuz' + 'da'                      → 'otuzda'
    'yirmi beş' + 'lik'                → 'yirmi beşlik'
    Tanınmayan ek olduğu gibi eklenir (README'de "bilinen eksik").
    """
    kind = _SUFFIX_TYPES.get(raw_suffix.lower())
    if kind is None:
        return spoken + raw_suffix
    base = spoken.split()[-1]
    a, i = _h2(base), _h4(base)
    d = "t" if base[-1] in _VOICELESS else "d"
    ends_vowel = base[-1] in _VOWELS
    form = {
        "LOC": d + a,
        "ABL": d + a + "n",
        "LI": "l" + i,
        "LIK": "l" + i + "k",
        "DAT": ("y" if ends_vowel else "") + a,
        "ACC": ("y" if ends_vowel else "") + i,
        "GEN": ("n" if ends_vowel else "") + i + "n",
    }[kind]
    return spoken + form


# ── Sözlükler ─────────────────────────────────────────────────────────────────────────
_MONTHS = ["", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
           "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]

_CURRENCY = {"TL": "lira", "₺": "lira", "$": "dolar", "€": "euro", "£": "sterlin", "USD": "dolar", "EUR": "euro"}

# Yalnız NET olanlar. "m" gibi belirsizler bilerek YOK (İngilizce kelimeyle çakışır).
_UNITS = {
    "kg": "kilogram", "gr": "gram", "cm": "santimetre", "mm": "milimetre",
    "km": "kilometre", "lt": "litre", "ml": "mililitre", "sn": "saniye", "dk": "dakika",
}

_ABBREV = {
    "vs.": "vesaire", "vb.": "ve benzeri", "Dr.": "Doktor", "Prof.": "Profesör",
    "No.": "numara", "Av.": "Avukat", "Sn.": "Sayın",
}

# Korunacaklar: TTS etiketleri ve [mood:...] — AYNEN geçer (higgs_tts._to_higgs_markup
# trnorm'dan SONRA çalışıp bunları Higgs sözdizimine çevirir).
_PROTECT_RE = re.compile(r"\[[^\]]*\]")
_SUF = r"(?:'([a-zçğıöşü]+))?"  # kesme işaretli ek (opsiyonel)


def _spoken(n: int, suffix: str | None) -> str:
    w = number_to_words(n)
    return attach_suffix(w, suffix) if suffix else w


def normalize_tr(text: str) -> str:
    """Türkçe metni TTS'e verilebilir sözlü forma çevir.

    İngilizce ödünç kelimelere (Wi-Fi, router, restart...) ve [etiket]'lere DOKUNMAZ:
    yalnız rakam/simge/kısaltma kalıpları hedeflenir.
    """
    if not text:
        return text

    # 1) [etiket]'leri kilitle
    saved: list[str] = []

    def _stash(m: re.Match) -> str:
        saved.append(m.group(0))
        # Yer tutucu RAKAM İÇEREMEZ: aşağıdaki "çıplak tam sayı" kuralı onu da çevirirdi.
        # Özel kullanım alanı (U+E000+) tek karakter → hiçbir kalıba yakalanmaz.
        return chr(0xE000 + len(saved) - 1)

    text = _PROTECT_RE.sub(_stash, text)

    # 2) Tarih  12.03.2026 → "on iki Mart iki bin yirmi altı"   (binlik ayıraçtan ÖNCE!)
    def _date(m: re.Match) -> str:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not (1 <= d <= 31 and 1 <= mo <= 12):
            return m.group(0)
        return f"{number_to_words(d)} {_MONTHS[mo]} {_spoken(y, m.group(4))}"

    text = re.sub(rf"\b(\d{{1,2}})\.(\d{{1,2}})\.(\d{{4}})\b{_SUF}", _date, text)

    # 3) Saat  14:30 → "on dört otuz" · 09:05 → "dokuz sıfır beş" · 14:00 → "on dört"
    def _time(m: re.Match) -> str:
        h, mi = int(m.group(1)), int(m.group(2))
        if not (0 <= h <= 23 and 0 <= mi <= 59):
            return m.group(0)
        if mi == 0:
            return _spoken(h, m.group(3))
        mins = f"sıfır {number_to_words(mi)}" if mi < 10 else number_to_words(mi)
        return f"{number_to_words(h)} {attach_suffix(mins, m.group(3)) if m.group(3) else mins}"

    text = re.sub(rf"\b(\d{{1,2}}):(\d{{2}})\b{_SUF}", _time, text)

    # 4) Yüzde  %25 → "yüzde yirmi beş"  (Türkçede "yüzde" sayıdan ÖNCE)
    def _pct(m: re.Match) -> str:
        num = m.group(1).replace(".", "")
        return "yüzde " + _spoken(int(num), m.group(2))

    text = re.sub(rf"%\s?(\d{{1,3}}(?:\.\d{{3}})*|\d+){_SUF}", _pct, text)

    # 5) Ondalık  2,5 → "iki virgül beş"  (kesirli kısım basamak basamak)
    def _dec(m: re.Match) -> str:
        whole, frac = m.group(1).replace(".", ""), m.group(2)
        frac_w = " ".join(number_to_words(int(c)) for c in frac)
        return attach_suffix(f"{number_to_words(int(whole))} virgül {frac_w}", m.group(3) or "")

    text = re.sub(rf"\b(\d{{1,3}}(?:\.\d{{3}})*|\d+),(\d+)\b{_SUF}", _dec, text)

    # 6) Binlik ayıraçlı tam sayı  3.500 → "üç bin beş yüz"
    text = re.sub(rf"\b(\d{{1,3}}(?:\.\d{{3}})+)\b{_SUF}",
                  lambda m: _spoken(int(m.group(1).replace(".", "")), m.group(2)), text)

    # 7) Çıplak tam sayı  1994 → "bin dokuz yüz doksan dört"
    text = re.sub(rf"\b(\d+)\b{_SUF}", lambda m: _spoken(int(m.group(1)), m.group(2)), text)

    # 8) Para birimi ve birimler (artık sayı yazıya döndü, simge tek başına kaldı).
    #    Kesme işaretli ek SİMGEYE de gelebilir: "4.750 TL'den" → "... liradan".
    #    Ek, çevrilen KELİMEYE göre uyumlanır (lira+dan → liradan, lira+ye → liraya).
    def _sym_sub(word: str):
        return lambda m: attach_suffix(word, m.group(1)) if m.group(1) else word

    for sym, word in _CURRENCY.items():
        text = re.sub(rf"(?<!\w){re.escape(sym)}(?!\w){_SUF}", _sym_sub(word), text)
    for sym, word in _UNITS.items():
        text = re.sub(rf"(?<=\s){re.escape(sym)}(?!\w){_SUF}", _sym_sub(word), text)

    # 9) Kısaltmalar
    for ab, word in _ABBREV.items():
        text = re.sub(rf"(?<!\w){re.escape(ab)}", word, text)

    # 10) Etiketleri geri koy + boşluk temizliği
    text = re.sub(r"[\ue000-\uf8ff]", lambda m: saved[ord(m.group(0)) - 0xE000], text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


# ── Selftest ──────────────────────────────────────────────────────────────────────────
_CASES: list[tuple[str, str]] = [
    # sayı
    ("3.500 TL", "üç bin beş yüz lira"),
    ("2.658 TL", "iki bin altı yüz elli sekiz lira"),
    ("1994 yılında", "bin dokuz yüz doksan dört yılında"),
    ("100 kişi", "yüz kişi"),
    ("1.000.000 kişi", "bir milyon kişi"),
    # yüzde — "yüzde" ÖNDE
    ("%25 indirim", "yüzde yirmi beş indirim"),
    ("%25'lik dilim", "yüzde yirmi beşlik dilim"),
    # ondalık
    ("2,5 litre", "iki virgül beş litre"),
    # saat
    ("saat 14:30", "saat on dört otuz"),
    ("saat 09:05", "saat dokuz sıfır beş"),
    ("saat 14:00", "saat on dört"),
    # kesme işaretli ek — ünlü uyumu + sert ünsüz benzeşmesi
    ("saat 14:30'da başlıyor", "saat on dört otuzda başlıyor"),
    ("1994'te doğdu", "bin dokuz yüz doksan dörtte doğdu"),
    ("2026'ya kadar", "iki bin yirmi altıya kadar"),
    ("5'ten fazla", "beşten fazla"),
    # tarih
    ("12.03.2026 tarihinde", "on iki Mart iki bin yirmi altı tarihinde"),
    # para simgeleri
    ("250 $ ödedim", "iki yüz elli dolar ödedim"),
    # ek SİMGEYE gelebilir — ek, çevrilen kelimeye göre uyumlanır
    ("4.750 TL'den", "dört bin yedi yüz elli liradan"),
    ("6.300 TL'ye", "altı bin üç yüz liraya"),
    ("23 kg'dan", "yirmi üç kilogramdan"),
    # DOKUNULMAYACAKLAR
    ("Wi-Fi router'ı restart ettim", "Wi-Fi router'ı restart ettim"),
    ("Merhaba [laughter] nasılsın", "Merhaba [laughter] nasılsın"),
    ("Bak [mood:excited] geldim", "Bak [mood:excited] geldim"),
    ("link'e tıkla, download et", "link'e tıkla, download et"),
    # kısaltma
    ("Dr. Ahmet geldi", "Doktor Ahmet geldi"),
    ("kalem, defter vs. aldım", "kalem, defter vesaire aldım"),
]


def _selftest() -> int:
    ok = 0
    for src, want in _CASES:
        got = normalize_tr(src)
        if got == want:
            ok += 1
        else:
            print(f"  BAŞARISIZ: {src!r}\n    beklenen: {want!r}\n    gelen   : {got!r}")
    print(f"\nselftest: {ok}/{len(_CASES)} geçti")
    return 0 if ok == len(_CASES) else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    for line in sys.stdin:
        print(normalize_tr(line.rstrip("\n")))
