"""truth_check — modelin ANLATTIĞI ile araçların YAPTIĞI arasındaki çelişki denetimi.

İlke (bu repo'nun değişmezi): **model NE YAPILACAĞINA karar verir, harness NE
OLDUĞUNU söyler.** `docs/TURN-SAFE-SPEAKER-IDENTITY.md` bunu kimlik için kurdu;
burada aynı ilkeyi KAYIT/EYLEM iddialarına genişletiyoruz.

NEDEN (ölçüldü, `bench/26b-baseline/sonuclar.md` §4): tool hata dönerken model
**10/10 uydurdu**, 0/10 hatayı söyledi. Canlıda (2026-07-26) `memory_add` →
"kaydedilmedi" dönerken model "Notumu aldım Ayhan!" dedi. Harness gerçeği ZATEN
biliyordu (`toolResult` olayı `isError` ile akışa giriyor); eksik olan KARŞILAŞTIRMA.

Üç katman:

  **Katman 1 — tur defteri** (`TurnLedger`, deterministik, BEDAVA).
  Tur boyunca her `toolResult`: araç adı + `isError` + sonuç metni. Başarısızlık
  YALNIZ `isError` değildir: extension'lar bazı retleri düz metinle döner
  (`"guest: hafıza yok, kaydedilmedi."` — `isError` YOK). O yüzden metin
  işaretleri de okunur.

  **Katman 2 — kritik yazma araçlarının sonucunu HARNESS söyler** (deterministik).
  `memory_add`/`soul_add`/`reminder_add`/… hata döndüyse onay cümlesini worker
  üretir; modelin o turdaki anlatısı kullanıcıya HİÇ ulaşmaz (çağıran taraf
  `guard_on` ile delta'ları tamponlar ve atar). Model ne derse desin kullanıcı
  GERÇEĞİ duyar. Araç BAŞARILIYSA müdahale YOK — çift onay sesi kötü UX.

  **Katman 3 — araç sonucu OLMAYAN iddialar için küçük LLM yargıcı**
  (`claims_success`). "Şu an durumu düzelttim" gibi karşılaştıracak sonucu
  olmayan cümleler için. Kelime listesiyle **OR** çalışır: biri "başarı iddiası
  var" derse müdahale (amaç hassasiyet değil YAKALAMA).

  **MALİYET KAPISI** (şart): sınıflandırıcı HER TUR çalışmaz. GPU'da beyin+TTS+STT
  aynı kartta; her tura ~115 ms eklemek kabul edilemez. `decide()` yalnız bir RİSK
  SİNYALİ varken (turda hata dönen bir araç) ve deterministik katmanlar zaten
  müdahale etmediyse `True` döner. Hatasız tipik turda çağrı SIFIRDIR.

Katman 3 best-effort'tur: erişilemez/yavaşsa (timeout) tur BLOKLANMAZ — atlanır.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import unicodedata
import urllib.error
import urllib.request
from typing import Any, Optional

import speech_speed

logger = logging.getLogger("truth_check")


# ── Türkçe-duyarlı katlama (ı/İ dahil) ───────────────────────────────────────
# NFKD 'ş/ğ/ü/ö/ç'yi ayrıştırır ama 'ı' (U+0131) BİLEŞİK DEĞİLDİR → elle eşle.
# 'İ' NFKD'de 'I' + birleşen nokta olur; çeviri NFKD'den SONRA yapılır ki ikisi de yakalansın.
_TR_FOLD = str.maketrans({"ı": "i", "I": "i", "ı".upper(): "i"})


def fold(s: str) -> str:
    """Aksan/büyük-küçük/Türkçe-i duyarsız normalize: 'Kaydettim' → 'kaydettim'."""
    s = unicodedata.normalize("NFKD", s or "").translate(_TR_FOLD)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.casefold()).strip()


# ── Katman 2: kritik yazma araçları ──────────────────────────────────────────
# VERİ YAZAN araçlar. Bunların sonucunu model DEĞİL harness anlatır (hata hâlinde).
# Salt-okuma araçları (memory_search, reminder_list, web_search) burada YOK: onların
# hatası kullanıcıya yanlış bir "kaydettim" olarak dönmez.
CRITICAL_WRITE_TOOLS: frozenset[str] = frozenset({
    "memory_add",
    "soul_add",
    "reminder_add",
    "reminder_cancel",
    "memory_consolidate",
})

# Araç → harness'ın kullanacağı FİİL (hata hâlinde).
_FAIL_VERB: dict[str, str] = {
    "memory_add": "Kaydedemedim",
    "soul_add": "Bunu kalıcı olarak kaydedemedim",
    "reminder_add": "Hatırlatmayı kuramadım",
    "reminder_cancel": "Hatırlatmayı iptal edemedim",
    "memory_consolidate": "Hafızayı toparlayamadım",
}
_FAIL_VERB_DEFAULT = "Bunu yapamadım"

# Sonuç METNİNDEKİ başarısızlık işaretleri → kullanıcıya söylenecek KISA gerekçe.
# (fold edilmiş metinde aranır; SIRA ÖNEMLİ, ilk eşleşen kazanır.)
#
# DİKKAT — bunlar BAŞARI metinleriyle çakışmamalı. memory_add başarıyla
# "Zaten kayıtlı (private). Tekrar eklenmedi." dönebilir: listede "eklenmedi" YOK,
# olmamalı. Aynı şekilde "Kaydedildi (private)." ile "kaydedilmedi" alt-dize olarak
# eşleşmez. Yeni işaret eklerken bunu koru.
_FAIL_MARKERS: tuple[tuple[str, str], ...] = (
    ("guest:", "seni henüz tanımıyorum"),
    ("kaydedilmedi", "hafızaya erişemiyorum"),
    ("yazilamadi", "hafızaya erişemiyorum"),
    ("acilamadi", "hafızaya erişemiyorum"),
    ("yetkin yok", "buna iznin yok"),
    ("yazilmadi", "söyleyeceğin boş geldi"),
    ("bos hatirlatma", "neyi hatırlatacağımı anlamadım"),
    ("kurulamaz", "seni henüz tanımıyorum"),
    ("hafiza yok", "seni henüz tanımıyorum"),
    ("ruh kaydi yok", "seni henüz tanımıyorum"),
    ("gecersiz", "verdiğin bilgi geçersiz"),
    ("eslesen bekleyen hatirlatma yok", "eşleşen bir hatırlatma yok"),
    # events.ts resolveDue hataları (İngilizce, isError=true ile gelir)
    ("no time given", "saati anlayamadım"),
    ("invalid time", "saati anlayamadım"),
    ("unrecognized time format", "saati anlayamadım"),
    ("cannot be negative", "saati anlayamadım"),
    ("still too big", "hafıza dosyası hâlâ çok büyük"),
)
_FAIL_REASON_DEFAULT = "bir hata oldu"


def fail_reason(result: str, is_error: bool) -> Optional[str]:
    """Sonuç bir BAŞARISIZLIK mı? Öyleyse kullanıcıya söylenecek kısa gerekçe.

    None = başarı (müdahale yok). `isError` tek başına yeter; ayrıca metin
    işaretleri okunur çünkü extension'lar bazı retleri `isError`'SIZ döner
    (canlı vaka: `memory_add` → "guest: hafıza yok, kaydedilmedi.")."""
    low = fold(result)
    for marker, reason in _FAIL_MARKERS:
        if marker in low:
            return reason
    if is_error:
        return _FAIL_REASON_DEFAULT
    return None


def failure_line(name: str, reason: str) -> str:
    """Harness'ın SÖYLEYECEĞİ tek cümle. Sesli asistan → KISA."""
    return f"{_FAIL_VERB.get(name, _FAIL_VERB_DEFAULT)}, {reason}."


# ── Kelime listesi: "başarıyla yaptım" iddiası (deterministik, bedava) ───────
# `pi/AGENTS.md` içindeki yasak kalıplarla AYNI aile. Amaç hassasiyet değil
# YAKALAMA: yanlış bir "kaydettim" geçmesin. (fold edilmiş metinde aranır.)
#
# İki tür: "record" (bir şey KAYDETTİM) ve "action" (bir işi YAPTIM). Düzeltme
# cümlesi buna göre seçilir — kayıt iddiasına "yapamadım", eylem iddiasına
# "kaydetmedim" demek kullanıcıyı yanıltır.
_CLAIM_RECORD: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p) for p in (
        # Türkçe ekli hâlleri de yakala: "notumu aldım", "notunu ettim" (canlı vaka
        # "Notumu aldım Ayhan!" düz "not aldım" kalıbıyla KAÇIYORDU).
        r"\bnot\w* al(d[iı]m|[iı]yorum)\b",
        r"\bnot\w* ettim\b",
        r"\bnot olarak (ald[iı]m|ekledim|yazd[iı]m)\b",
        r"\bkaydett?im\b",
        r"\bkay[iı]t ettim\b",
        # SES KAYDI iddiası (canlı 27 Tem: "Harika Çiğdem, ses kaydını aldım" —
        # oysa enroll REDDEDİLMİŞTİ). "not aldım" kalıbı bunu KAÇIRIYORDU.
        r"\bkayd[iı]n[iı]?\w*\s+ald[iı]m\b",
        r"\bkay[iı]t ald[iı]m\b",
        r"\bkaydediyorum\b",
        r"\bakl[iı]mda tut(acag[iı]m|uyorum|ar[iı]m)\b",
        r"\bakl[iı]ma (yazd[iı]m|not ettim)\b",
        r"\bunutmam\b",
        r"\bunutmayacag[iı]m\b",
        r"\bhat[iı]rlat(acag[iı]m|acam|[iı]r[iı]m)\b",
        r"\b(hat[iı]rlatma|alarm)\w* (kurdum|ayarlad[iı]m|olusturdum)\b",
        r"\bekledim\b",
        r"\byazd[iı]m bile\b",
    )
)
_CLAIM_ACTION: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p) for p in (
        r"\bduzelttim\b",
        r"\bhallettim\b",
        r"\bayarlad[iı]m\b",
        r"\bgerekeni yapt[iı]m\b",
        r"\bislemi tamamlad[iı]m\b",
    )
)


def claim_kind(text: str) -> Optional[str]:
    """Metindeki başarı iddiasının türü: 'record' | 'action' | None."""
    low = fold(text)
    if any(p.search(low) for p in _CLAIM_RECORD):
        return "record"
    if any(p.search(low) for p in _CLAIM_ACTION):
        return "action"
    return None


def has_claim_phrase(text: str) -> bool:
    """Metin, kelime listesindeki bir BAŞARI İDDİASI kalıbını içeriyor mu?"""
    return claim_kind(text) is not None


# ── Katman 2 (mod): "hangi moddayım" iddiası da harness'ın bileceği bir şeydir ─
# Canlı kanıt: model `enter_dev_mode` çağırıp "İstediğin gibi normal moda geçtim"
# dedi; başka bir turda sistem NORMAL moddayken "Hâlâ geliştirme modundayım" dedi.
# Mod, worker'ın DETERMİNİSTİK olarak bildiği bir durumdur (PiBrain._mode /
# _pending_mode) → LLM'e bırakılmaz, karşılaştırılır. Ölçüldü: küçük yargıç bu
# cümleyi "kayıt/eylem iddiası" saymıyor (HAYIR) → burada deterministik olmak ŞART.
_MODE_CLAIM_DEV: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p) for p in (
        r"\b(gelistirme|kod|dev) modunday[iı]m\b",
        r"\b(gelistirme|kod|dev) mod(a|una) (gectim|giriyorum|girdim|gecti[mk])\b",
        r"\b(gelistirme|kod|dev) modunda kal(d[iı]m|[iı]yorum)\b",
    )
)
_MODE_CLAIM_NORMAL: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p) for p in (
        r"\bnormal mod(day[iı]m|a gectim|a dondum|a donuyorum)\b",
        r"\b(gelistirme|kod|dev) modundan (c[iı]kt[iı]m|c[iı]k[iı]yorum)\b",
        r"\b(gelistirme|kod|dev) modunda degilim\b",
    )
)
_MODE_LINE = {
    "dev": "Aslında hâlâ geliştirme modundayım.",
    "normal": "Aslında normal moddayım.",
}


def mode_claim(text: str) -> Optional[str]:
    """Metin hangi modda OLDUĞUNU iddia ediyor? 'dev' | 'normal' | None.

    İkisi birden geçerse (ör. 'geliştirme modundan çıkıp normal moddayım') çelişki
    yoktur — ikisi de aynı şeyi söyler; iddia yine 'normal' sayılır."""
    low = fold(text)
    dev = any(p.search(low) for p in _MODE_CLAIM_DEV)
    normal = any(p.search(low) for p in _MODE_CLAIM_NORMAL)
    if dev and not normal:
        return "dev"
    if normal and not dev:
        return "normal"
    return None


# ── Katman 2 (hız): "hızlandırdım" da harness'ın bileceği bir şeydir ─────────
# CANLI KANIT (27 Tem 18:21 ve 18:33), üç turda üst üste:
#     Ayhan : Biraz daha hızlı konuşabilir misin?
#     Candan: ...konuşma hızımı biraz daha artırıyorum.
#     Ayhan : Hayır olmadı. İki birim daha arttır.
#     Candan: ...hızımı iki birim daha artırıyorum.
#     Ayhan : Hâlâ çok yavaş konuşuyorsun.
# Tempo hiç değişmedi ve değişemezdi (hız kolu YOKTU); üstelik model olmayan bir
# "birim" uydurdu. Artık kol VAR (`speech_speed`) ama iddia yine yanlış olabilir:
# ya işareti hiç koymamıştır ya da TAVANDADIR. Kademe worker'ın DETERMİNİSTİK
# olarak bildiği bir durum → LLM'e sorulmaz, KARŞILAŞTIRILIR (mod denetimiyle
# birebir aynı ilke).
_CLAIM_SPEED_FAST: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p) for p in (
        # "hızlandırıyorum / hızlandırdım / hızlanıyorum"
        r"\bhizlan(d[iı]r)?[iı]?(yorum|d[iı]m|acagim)\b",
        # "hızımı (biraz daha / iki birim) artırıyorum / yükseltiyorum / yukarı çekiyorum"
        r"\bh[iı]z\w*\b[^.!?]{0,40}\b(art[iı]r|artt[iı]r|yukselt)\w*(yorum|d[iı]m|acagim)\b",
        r"\bh[iı]z\w*\b[^.!?]{0,40}\byukar[iı] cek(iyorum|tim)\b",
        r"\bdaha h[iı]zl[iı] konus(uyorum|acagim|maya basl[iı]yorum)\b",
        r"\btempo\w*\b[^.!?]{0,30}\b(art[iı]r|artt[iı]r|h[iı]zland[iı]r)\w*(yorum|d[iı]m)\b",
    )
)
_CLAIM_SPEED_SLOW: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p) for p in (
        r"\byavasl(at)?[iı]?(yorum|d[iı]m|t[iı]m|ayacagim)\b",
        r"\bh[iı]z\w*\b[^.!?]{0,40}\b(dusur|azalt)\w*(uyorum|yorum|d[iı]m|acagim)\b",
        r"\bdaha yavas konus(uyorum|acagim|maya basl[iı]yorum)\b",
    )
)
# ⚠️ "-abilirim" hâlleri BİLEREK dışarıda: "istersen hızımı artırabilirim" bir TEKLİF,
# iddia değil. Teklifi düzeltmek kullanıcıyı şaşırtırdı.

SPEED_CEILING_LINE = "Aslında daha hızlı konuşamıyorum, bu en hızlı kademem."
SPEED_FLOOR_LINE = "Aslında daha yavaş konuşamıyorum, bu en yavaş kademem."
SPEED_UNCHANGED_LINE = "Aslında konuşma hızımı değiştiremedim."


def speed_claim(text: str) -> Optional[str]:
    """Metin hız DEĞİŞTİRDİĞİNİ iddia ediyor mu? 'faster' | 'slower' | None.

    İkisi birden geçerse ('yavaşlatmıyorum, hızlandırıyorum') karar verilmez → None;
    belirsiz bir cümleyi düzeltmek, düzeltmemekten kötüdür.
    """
    low = fold(text)
    fast = any(p.search(low) for p in _CLAIM_SPEED_FAST)
    slow = any(p.search(low) for p in _CLAIM_SPEED_SLOW)
    if fast and not slow:
        return "faster"
    if slow and not fast:
        return "slower"
    return None


def speed_line(said: str, before: Optional[str]) -> Optional[str]:
    """Hız iddiası GERÇEKLEŞTİ mi? Gerçekleşmediyse harness'ın söyleyeceği cümle.

    `before` = TUR BAŞINDAKİ kademe (tur sonunda okumak yanıltır: `[speed:X]` o ana
    kadar zaten uygulanmış olur ve "değişmedi" sanılırdı).
    İddia yoksa None → hiçbir müdahale yok; hız işareti koyup hiç konuşmamak da serbest.
    """
    if before is None:
        return None                      # hız kolu bağlı değil (OmniVoice) → denetim yok
    claim = speed_claim(said)
    if claim is None:
        return None
    now = speech_speed.requested(said) or before
    delta = speech_speed.index(now) - speech_speed.index(before)
    if (claim == "faster" and delta > 0) or (claim == "slower" and delta < 0):
        return None                      # iddia DOĞRU: kademe gerçekten değişti
    if claim == "faster" and speech_speed.at_ceiling(now):
        return SPEED_CEILING_LINE
    if claim == "slower" and speech_speed.at_floor(now):
        return SPEED_FLOOR_LINE
    return SPEED_UNCHANGED_LINE


# ── Kullanıcıya giden düzeltme cümleleri (Katman 2b / 3) ─────────────────────
# Sesli asistan: KISA. Model zaten bir şey söyledi; buraya bir cümleden fazlası eklenmez.
UNBACKED_LINE = "Aslında bunu kaydetmedim, kusura bakma."
UNVERIFIED_LINE = "Aslında bunu yapamadım, kusura bakma."


# ── Katman 1: tur defteri ────────────────────────────────────────────────────
class TurnLedger:
    """Bir turun tool sonuçları defteri. Deterministik, LLM YOK, maliyet YOK.

    Çağıran (`PiStream._run`) her `message_end`/`turn_end` mesajını `record_message`
    ile buraya düşürür; aynı olay iki kez gelebilir (message_end + turn_end aynı
    mesajı taşıyabilir) → `toolCallId` ile elenir."""

    def __init__(self) -> None:
        self.entries: list[dict] = []
        self._seen: set[str] = set()

    # -- kayıt --------------------------------------------------------------
    def record(
        self, name: str, result: str = "", is_error: bool = False, call_id: str = ""
    ) -> None:
        if call_id:
            if call_id in self._seen:
                return
            self._seen.add(call_id)
        reason = fail_reason(result, is_error)
        self.entries.append({
            "name": name or "",
            "result": result or "",
            "isError": bool(is_error),
            "failed": reason is not None,
            "reason": reason,
        })

    def record_message(self, message: Any) -> None:
        """pi mesajından `toolResult` ise deftere yaz. Ayrıştırma hatası turu BOZMAZ."""
        try:
            if not isinstance(message, dict) or message.get("role") != "toolResult":
                return
            text = "".join(
                c.get("text", "")
                for c in message.get("content", []) or []
                if isinstance(c, dict) and c.get("type") == "text"
            )
            self.record(
                message.get("toolName") or "",
                text,
                bool(message.get("isError")),
                message.get("toolCallId") or "",
            )
        except Exception:  # noqa: BLE001 — defter tutmak konuşmayı BOZMAZ
            logger.warning("tur defteri: toolResult ayrıştırılamadı", exc_info=True)

    # -- sorgular -----------------------------------------------------------
    def has_failure(self) -> bool:
        """Turda hata/ret dönen HERHANGİ bir araç var mı? (Katman 3 risk sinyali)"""
        return any(e["failed"] for e in self.entries)

    def failed_writes(self) -> list[dict]:
        return [
            e for e in self.entries
            if e["failed"] and e["name"] in CRITICAL_WRITE_TOOLS
        ]

    def ok_writes(self) -> list[dict]:
        return [
            e for e in self.entries
            if not e["failed"] and e["name"] in CRITICAL_WRITE_TOOLS
        ]

    def ok_any(self) -> list[dict]:
        """Turda BAŞARIYLA dönen herhangi bir araç (eylem iddiasının dayanağı)."""
        return [e for e in self.entries if not e["failed"]]

    @property
    def guard_on(self) -> bool:
        """Kritik bir yazma BAŞARISIZ → bu turda anlatıyı HARNESS devralır.
        Çağıran bunu görünce modelin delta'larını canlıya vermez, tamponlar."""
        return bool(self.failed_writes())

    def write_failure_line(self) -> Optional[str]:
        """Katman 2'nin söyleyeceği cümle(ler). Başarısız yazma yoksa None.

        Aynı (araç, gerekçe) tekrar etmez; en çok İKİ cümle (sesli asistan)."""
        seen: set[tuple[str, str]] = set()
        lines: list[str] = []
        for e in self.failed_writes():
            key = (e["name"], e["reason"] or "")
            if key in seen:
                continue
            seen.add(key)
            lines.append(failure_line(e["name"], e["reason"] or _FAIL_REASON_DEFAULT))
            if len(lines) == 2:
                break
        return " ".join(lines) or None

    def summary(self) -> str:
        """Log için tek satır: `memory_add=hata soul_add=ok`."""
        if not self.entries:
            return "tool yok"
        return " ".join(
            f"{e['name']}={'hata' if e['failed'] else 'ok'}" for e in self.entries
        )


# ── Karar: hangi katman devreye girsin ───────────────────────────────────────
def decide(
    ledger: TurnLedger, said: str, *, mode: Optional[str] = None,
    speed: Optional[str] = None,
) -> tuple[Optional[str], bool]:
    """(deterministik düzeltme cümlesi, sınıflandırıcı çalışsın mı).

    `mode`  = worker'ın bildiği GERÇEK mod ("normal"|"dev"); None → mod denetimi yok.
    `speed` = TUR BAŞINDAKİ konuşma hızı kademesi; None → hız denetimi yok.

    Sıra ve MALİYET KAPISI:
      1. Kritik yazma hatası → Katman 2 devralır, cümleyi harness söyler. LLM YOK.
      2. Mod iddiası gerçekle çelişiyor → deterministik düzeltme. LLM YOK.
      2b. Hız iddiası gerçekleşmedi (işaret yok ya da TAVANDA) → deterministik
         düzeltme. LLM YOK. (Canlı hata: üç turda üst üste "hızlandırıyorum".)
      3. Kelime listesi "başarı iddiası" diyor ama turda onu DESTEKLEYEN başarılı
         bir araç YOK (kayıt iddiası → yazma aracı, eylem iddiası → herhangi bir
         araç). Deterministik düzeltme. LLM YOK.
         (Canlı vaka: model hiç tool çağırmadan "aklımda tutacağım" diyor.)
      4. Turda hata dönen bir araç VAR ama yukarıdakiler tutmadı → tek risk
         sinyali bu; sınıflandırıcıya SORULUR (~115 ms).
      5. Hiçbiri → (None, False). **Tipik hatasız turda buraya düşülür: LLM çağrısı
         SIFIR.** Kapının tamamı burada; çağıran taraf ayrıca bayrak tutmaz.
    """
    line = ledger.write_failure_line()
    if line is not None:
        return line, False
    if mode in ("normal", "dev"):
        claimed = mode_claim(said)
        if claimed is not None and claimed != mode:
            return _MODE_LINE[mode], False
    hiz = speed_line(said, speed)
    if hiz is not None:
        return hiz, False
    kind = claim_kind(said)
    if kind == "record" and not ledger.ok_writes():
        return UNBACKED_LINE, False
    if kind == "action" and not ledger.ok_any():
        return UNVERIFIED_LINE, False
    if ledger.has_failure() and (said or "").strip():
        return None, True
    return None, False


# ── Katman 3: küçük LLM yargıcı (best-effort) ────────────────────────────────
# ÖLÇÜLDÜ (gemma-4-12b, llama-server .25:8082): medyan 115 ms, en yavaş 126 ms,
# doğruluk 7/8. Kaçırdığı vaka ("Aklımda tutacağım, merak etme.") kelime listesinde
# VAR → OR mantığı onu zaten yakalıyor.
def _envflag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


CLAIM_CHECK_ENABLED = _envflag("CLAIM_CHECK_ENABLED", True)
CLAIM_CHECK_URL = os.environ.get(
    "CLAIM_CHECK_URL", "http://192.168.0.25:8082/v1/chat/completions"
).strip()
# llama-server tek model sunar ve alanı yok sayar; uzak/OpenAI uyumlu bir uca
# yönlendirilirse burası doldurulur.
CLAIM_CHECK_MODEL = os.environ.get("CLAIM_CHECK_MODEL", "local").strip() or "local"
# Ölçülen en yavaş 126 ms; pay bırakıldı. Aşılırsa tur BLOKLANMAZ, atlanır.
CLAIM_CHECK_TIMEOUT = float(os.environ.get("CLAIM_CHECK_TIMEOUT", "0.6") or 0.6)
# Yargıca giden metin: cevabın SONU (iddia oradadır) — uzun cevap prefill yakmasın.
CLAIM_CHECK_MAX_CHARS = int(os.environ.get("CLAIM_CHECK_MAX_CHARS", "300") or 300)


def reload_settings() -> None:
    """`.env` YÜKLENDİKTEN SONRA ayarları tazele. `agent.py` çağırır.

    `barge.reload_settings()` ile AYNI gerekçe: `agent.py`'nin `load_dotenv()`'i
    import blokundan SONRA çalışır, bu dosya ise modül seviyesinde `os.environ`
    okur → import anında `.env` HENÜZ YOKTUR. Ölçüldü (27 Tem, sunucuda):
    `CLAIM_CHECK_ENABLED=false` yazmak yargıcı KAPATMIYORDU. `barge.classify_llm`
    de bu değerleri `truth_check.` üzerinden okur; tek yerden tazelemek ikisini
    birden düzeltir."""
    global CLAIM_CHECK_ENABLED, CLAIM_CHECK_URL, CLAIM_CHECK_MODEL  # noqa: PLW0603
    global CLAIM_CHECK_TIMEOUT, CLAIM_CHECK_MAX_CHARS               # noqa: PLW0603
    CLAIM_CHECK_ENABLED = _envflag("CLAIM_CHECK_ENABLED", True)
    CLAIM_CHECK_URL = os.environ.get(
        "CLAIM_CHECK_URL", "http://192.168.0.25:8082/v1/chat/completions"
    ).strip()
    CLAIM_CHECK_MODEL = os.environ.get("CLAIM_CHECK_MODEL", "local").strip() or "local"
    CLAIM_CHECK_TIMEOUT = float(os.environ.get("CLAIM_CHECK_TIMEOUT", "0.6") or 0.6)
    CLAIM_CHECK_MAX_CHARS = int(os.environ.get("CLAIM_CHECK_MAX_CHARS", "300") or 300)


CLAIM_CHECK_PROMPT = (
    "Bu cümle bir KAYIT/EYLEM işleminin BAŞARIYLA yapıldığını iddia ediyor mu? "
    "Yalnız EVET veya HAYIR."
)


def _post_sync(url: str, payload: dict, timeout: float) -> str:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — sabit iç uç
        body = json.loads(resp.read().decode("utf-8", "replace"))
    choices = body.get("choices") or []
    if not choices:
        return ""
    return ((choices[0].get("message") or {}).get("content") or "").strip()


def parse_verdict(answer: str) -> Optional[bool]:
    """Yargıcın cevabını çöz. EVET→True, HAYIR→False, anlaşılmadı→None (atla)."""
    low = fold(answer)
    if not low:
        return None
    if low.startswith("evet") or "evet" in low:
        return True
    if low.startswith("hayir") or "hayir" in low:
        return False
    return None


async def claims_success(said: str, *, timeout: Optional[float] = None) -> Optional[bool]:
    """Cümle bir başarı iddiası mı? True/False/None(bilinmiyor → müdahale YOK).

    BEST-EFFORT: uç kapalı, yavaş ya da bozuk cevap → None. Tur ASLA bloklanmaz;
    deterministik katmanlar zaten devrede."""
    text = (said or "").strip()
    if not CLAIM_CHECK_ENABLED or not text or not CLAIM_CHECK_URL:
        return None
    text = text[-CLAIM_CHECK_MAX_CHARS:]
    payload = {
        "model": CLAIM_CHECK_MODEL,
        "messages": [{"role": "user", "content": f"{CLAIM_CHECK_PROMPT}\n\nCümle: {text}"}],
        "max_tokens": 3,
        "temperature": 0,
        "stream": False,
    }
    budget = CLAIM_CHECK_TIMEOUT if timeout is None else timeout
    try:
        answer = await asyncio.wait_for(
            asyncio.to_thread(_post_sync, CLAIM_CHECK_URL, payload, budget),
            timeout=budget + 0.2,   # soket timeout'u kaçırsa bile tur takılmasın
        )
    except (asyncio.TimeoutError, urllib.error.URLError, OSError, ValueError) as exc:
        logger.info("iddia sınıflandırıcı atlandı (%s)", exc.__class__.__name__)
        return None
    except Exception:  # noqa: BLE001 — yargıç ASLA turu düşürmez
        logger.warning("iddia sınıflandırıcı beklenmedik hata", exc_info=True)
        return None
    return parse_verdict(answer)


__all__ = [
    "CRITICAL_WRITE_TOOLS",
    "SPEED_CEILING_LINE",
    "SPEED_FLOOR_LINE",
    "SPEED_UNCHANGED_LINE",
    "UNBACKED_LINE",
    "UNVERIFIED_LINE",
    "TurnLedger",
    "claim_kind",
    "claims_success",
    "decide",
    "fail_reason",
    "failure_line",
    "fold",
    "has_claim_phrase",
    "mode_claim",
    "parse_verdict",
    "speed_claim",
    "speed_line",
]
