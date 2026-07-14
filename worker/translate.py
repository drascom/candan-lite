"""TR→EN çeviri katmanı — YALNIZCA router'ın anlaması için.

═══════════════════════════════════════════════════════════════════════════════
 NEDEN
═══════════════════════════════════════════════════════════════════════════════
Router (Qwen3.5-4B) Türkçe cümlede SEMANTİK KOMŞU tuzağına düşüyor: katalogda
OLMAYAN bir cihaz istendiğinde en yakın tool'u çağırıyor ("kombiyi aç" →
light_control). Aynı cümle İngilizce verildiğinde bu hata belirgin azalıyor.
Ama çeviri ÖZEL İSİMLERİ bozuyor ("Kuzu Kuzu" → "Lamb Lamb", "Müslüm Gürses" →
"the Musicians", "İskender" → "Alexander").

Çözüm (ölçüldü, bkz. docs/ROUTER-EXPERIMENTS.md): router'a İKİ METİN birlikte
verilir — TÜRKÇE orijinal (argümanlar buradan) + İngilizce çeviri (tool seçimi
buradan). Birleştirmeyi tool_catalog.build_prompt(text, text_en=...) yapar.

═══════════════════════════════════════════════════════════════════════════════
 KIRMIZI ÇİZGİ — GÜVENLİ BAŞARISIZLIK
═══════════════════════════════════════════════════════════════════════════════
Çeviri servisi kapalı / yavaş / bozuk → `translate()` None döner ve router
TÜRKÇE-DOĞRUDAN moda düşer (bugünkü davranış). Bu katman ASLA istisna sızdırmaz,
ASLA turu bloklamaz. Çeviri bir İYİLEŞTİRME'dir, bir BAĞIMLILIK değil.

Servis arka arkaya birkaç kez düşerse DEVRE KESİCİ devreye girer: bir süre hiç
denenmez — yoksa her cümlede boşuna timeout beklenir (kullanıcı sesli asistanın
yavaşladığını duyar).

Servis: tools/translate_server.py (.25, systemd `candan-translate.service`, :8081,
CTranslate2 int8 CPU — GPU'ya dokunmaz).
"""
import asyncio
import logging
import os
import time
from typing import Optional

import aiohttp

logger = logging.getLogger("worker.translate")


def _envflag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# Router'a Türkçe cümlenin İngilizce çevirisini de ver. VARSAYILAN KAPALI: kazanç mütevazı
# (semantik komşu +9pp = 22 vakada 2) ve bedeli ~100-145ms. Aynı sorunu sıfır gecikmeyle
# çözebilecek alternatifler (unsupported_request bayrağı / negatif sınırlı açıklamalar)
# ölçülene kadar açma. true → TR + parantez içinde çeviri (çeviri VERİ'dir, EMİR değil:
# "tool'u şundan seç" demek abstain'i YIKIYOR — ölçüldü, trap_neigh 72.7 → 4.5).
ROUTER_TRANSLATE = _envflag("ROUTER_TRANSLATE", False)
ROUTER_TRANSLATE_URL = os.environ.get("ROUTER_TRANSLATE_URL",
                                      "http://192.168.0.25:8081/translate")
# Ölçülen p50 ~60-110ms (ct2 int8 CPU + LAN). 500ms = rahat üst sınır; aşılırsa çeviri
# YOK sayılır (TR-doğrudan), router yine de çalışır.
ROUTER_TRANSLATE_TIMEOUT_MS = float(
    os.environ.get("ROUTER_TRANSLATE_TIMEOUT_MS", "500") or 500)

# Devre kesici: bu kadar ARDIŞIK hatadan sonra, şu kadar saniye hiç deneme.
_FAIL_LIMIT = 3
_COOLDOWN_S = 60.0


class Translator:
    """Çeviri servisi istemcisi. Süreç ömrü boyunca tek örnek (session paylaşılır)."""

    def __init__(self, url: str = ROUTER_TRANSLATE_URL,
                 timeout_ms: float = ROUTER_TRANSLATE_TIMEOUT_MS):
        self._url = url
        self._timeout = aiohttp.ClientTimeout(total=timeout_ms / 1000.0)
        self._session: Optional[aiohttp.ClientSession] = None
        self._fails = 0
        self._skip_until = 0.0
        self._warned = False

    async def _sess(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def aclose(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    def _fail(self, why: str) -> None:
        self._fails += 1
        if self._fails >= _FAIL_LIMIT:
            self._skip_until = time.monotonic() + _COOLDOWN_S
            self._fails = 0
            logger.warning("çeviri servisi %d kez düştü (%s) → %.0fs boyunca TR-doğrudan",
                           _FAIL_LIMIT, why, _COOLDOWN_S)
        elif not self._warned:
            self._warned = True
            logger.warning("çeviri başarısız (%s) → bu cümle TR-doğrudan gidiyor", why)

    async def translate(self, text: str) -> Optional[str]:
        """TR cümle → EN çeviri. Başarısızlıkta None (= çevirisiz devam et).
        İSTİSNA ATMAZ."""
        if not text.strip():
            return None
        if time.monotonic() < self._skip_until:   # devre kesici açık
            return None
        try:
            sess = await self._sess()
            async with sess.post(self._url, json={"text": text}) as r:
                if r.status != 200:
                    self._fail(f"http {r.status}")
                    return None
                body = await r.json()
            en = (body.get("text_en") or "").strip()
            if not en:
                return None
            self._fails = 0
            return en
        except asyncio.TimeoutError:
            self._fail(f"timeout >{self._timeout.total * 1000:.0f}ms")
            return None
        except Exception as e:  # noqa: BLE001 — HER hata = çevirisiz devam
            self._fail(repr(e)[:80])
            return None


# ---------------------------------------------------------------------------
# CLI: python worker/translate.py "kombiyi aç"  → servisi tek cümleyle dene
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    async def _main() -> None:
        t = Translator()
        for s in sys.argv[1:] or ["Kuzu Kuzu çal"]:
            t0 = time.monotonic()
            en = await t.translate(s)
            print("%-45s -> %-55s %.0fms" % (s, en, (time.monotonic() - t0) * 1000))
        await t.aclose()

    asyncio.run(_main())
