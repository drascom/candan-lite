"""Hızlı tool-router — cümle → llama-server (json_schema) → {tool, args, multi_intent}.

Amaç: sık kullanılan, küçük sonuçlu (low-tier) tool çağrılarını ANA MODELE HİÇ
GİTMEDEN ~400ms'de karara bağlamak. Ana model (pi.dev / gpt-5.6) uzak API — sohbet
için iyi, "ışığı kapat" için pahalı ve yavaş.

Model: Qwen3.5-4B-Instruct Q8_0, llama.cpp/llama-server (.25), grammar = json_schema.
Şema/katalog/karar gerekçeleri: worker/tool_catalog.py + experiments/router-bench/.

═══════════════════════════════════════════════════════════════════════════════
 KIRMIZI ÇİZGİ — GÜVENLİ BAŞARISIZLIK
═══════════════════════════════════════════════════════════════════════════════
Router NE ŞEKİLDE olursa olsun başarısız olursa (servis kapalı, timeout, HTTP
hatası, bozuk JSON, abstain, multi_intent, executor yok) → SESSİZCE ANA MODELE
DÜŞÜLÜR. Yani `route()` None döner ve çağıran taraf bugünkü akışı aynen sürdürür.
Router ASLA istisna sızdırmaz, ASLA turu bloklamaz, ASLA sistemi bozmaz.
Bu davranış ZORUNLUDUR — değiştirme.

`route()` içindeki her şey tek bir try/except ile sarılıdır ve `decide()` de kendi
içinde savunmalıdır: iki kat, çünkü buradaki bir sızıntı KULLANICININ SESLİ
ASİSTANINI SUSTURUR.

═══════════════════════════════════════════════════════════════════════════════
 multi_intent — SESSİZ YARIM-İŞ BUG'INA KARŞI
═══════════════════════════════════════════════════════════════════════════════
"Salonun ışığını aç VE Neva'ya aşağı gelmesini söyle" — model bu cümlede yine TEK
tool döndürür (light_control). O çağrıyı kullanmak = işin yarısını yapıp diğer
yarısını SESSİZCE yutmak. Benchmark'ta multi_intent bayrağı 6/6 yakaladı (TR),
yanlış-pozitif %0.8. Bu yüzden: multi_intent=true → tool ATILIR, ana modele düşülür.
Ana model iki niyeti de görür. ASLA "hem bayrağa bak hem tool'u çalıştır" yapma.

═══════════════════════════════════════════════════════════════════════════════
 DISPATCH — neden EXECUTORS boş?
═══════════════════════════════════════════════════════════════════════════════
Router bir tool SEÇEBİLİR ama onu ÇALIŞTIRMAK ayrı bir iş ve bugün Python tarafında
karşılığı YOK:

  1) Tool'ların çalışan kodu TypeScript'te (pi/extensions/family-memory/index.ts).
     Veriyi ORASI sahiplenir: memory/*.md (yetkili kaynak) + FTS index (mem.db) +
     memory/events.db, ayrıca reminder_add'in "yarın 9'a" → due_ts çözümü de orada
     (server-side). Bunları Python'da yeniden yazmak = aynı veri için İKİ KAYNAK
     (markdown/index/events.db ve TR tarih ayrıştırma iki yerde ayrışır).
  2) pi'nın RPC komut yüzeyinde "şu tool'u çalıştır" DİYE BİR KOMUT YOK
     (prompt/abort/steer/follow_up/state/model/thinking/queue/compaction/retry/
     bash/session — bkz. pi docs/rpc.md). Bir tool ancak ANA MODEL çağırmaya karar
     verirse çalışır. Yani "router seçsin, pi çalıştırsın" da mümkün değil.
  3) 23 low tool'un yalnızca 6'sının kodu var (memory_add, memory_search, soul_add,
     reminder_add, reminder_list, web_search). Diğer 17'si (weather, light_control,
     timer_set, ...) benchmark için uydurulmuştu — HİÇBİR YERDE kodu yok.

Ayrıca: ana modeli atlamak SESLİ CEVABI da atlar. Bugün cevabı Candan'ın persona'sı
üretiyor; router çalıştırırsa cevabı Python'da konserve string olarak üretmek gerekir
("Tamam, kurdum") → persona kaybı. Bu bir ÜRÜN kararı, sessizce verilemez.

Bu üçü de mimari/ürün kararı gerektirir (bkz. rapor). O yüzden bu sürüm router'ı
GÖLGE (shadow) modda getirir: kararı ver, LOGLA, ana modele düş → davranış BUGÜNKÜYLE
BİREBİR AYNI. Karar kalitesi canlı konuşmada doğrulandıktan ve execute yolu
seçildikten sonra EXECUTORS doldurulur; `route()` mantığı hazır bekliyor.
"""
import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

import aiohttp

from tool_catalog import TOOL_ORIGIN, TOOL_TIER, build_prompt, router_json_schema

logger = logging.getLogger("worker.router")


def _envflag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# Router'ı ÇALIŞTIR. Varsayılan KAPALI — kullanıcı kendi test edene kadar.
ROUTER_ENABLED = _envflag("ROUTER_ENABLED", False)
# Seçilen tool'u GERÇEKTEN çalıştır (kısa devre). Varsayılan KAPALI = gölge mod:
# karar loglanır, ana model yine de cevap verir → davranış değişmez.
ROUTER_EXECUTE = _envflag("ROUTER_EXECUTE", False)
ROUTER_URL = os.environ.get("ROUTER_URL", "http://192.168.0.25:8080")
# Aşılırsa ana modele düş. 1500ms = ölçülen p50'nin (~400ms) rahat üstü; router
# yavaşladığında kullanıcıyı bekletmektense sessizce ana modele geçmek YEĞDİR.
ROUTER_TIMEOUT_MS = float(os.environ.get("ROUTER_TIMEOUT_MS", "1500") or 1500)


@dataclass
class RouterDecision:
    """Tek bir router kararı. `outcome` neden ana modele düşüldüğünü (ya da
    düşülmediğini) anlatır — canlıda 'router doğru mu karar veriyor' sorusunun cevabı."""

    text: str
    tool: Optional[str] = None
    args: dict = field(default_factory=dict)
    multi_intent: bool = False
    latency_ms: float = 0.0
    # executed   → tool çalıştı, ana modele GİDİLMEDİ
    # shadow     → tool seçildi ama ROUTER_EXECUTE=false → ana modele düşüldü
    # abstain    → tool=null (sohbet/bilgi/belirsiz) → ana modele
    # multi      → multi_intent=true → tool ATILDI → ana modele
    # no_exec    → tool seçildi ama Python executor'ı yok → ana modele
    # error      → timeout/HTTP/parse → ana modele
    outcome: str = "error"
    error: Optional[str] = None

    def log(self) -> None:
        """Tek satır, izlenebilir, gürültüsüz. Kullanıcı canlıda buna bakar:
            router: shadow tool=reminder_add multi=false 412ms "yarın 9'a alarm kur"
        """
        head = f"router: {self.outcome} tool={self.tool} multi={str(self.multi_intent).lower()}"
        tail = f'{self.latency_ms:.0f}ms "{self.text[:60]}"'
        if self.error:
            logger.warning("%s %s err=%s", head, tail, self.error)
        else:
            logger.info("%s %s", head, tail)


# ---------------------------------------------------------------------------
# EXECUTORS — tool adı → onu çalıştıran async fonksiyon.
# BOŞ: yukarıdaki "DISPATCH" notuna bak. Buraya bir tool eklemeden ÖNCE o tool'un
# verisini kimin sahiplendiğine karar verilmiş olmalı (Python mı, TS mi) — yoksa
# aynı veri için iki kaynak yaratırsın.
# İmza: async (args: dict) -> str   (dönen metin kullanıcıya SESLİ okunur)
# ---------------------------------------------------------------------------
Executor = Callable[[dict], Awaitable[str]]
EXECUTORS: dict[str, Executor] = {}


class Router:
    """llama-server istemcisi. Süreç ömrü boyunca tek örnek (aiohttp session paylaşılır)."""

    def __init__(self, url: str = ROUTER_URL, timeout_ms: float = ROUTER_TIMEOUT_MS):
        self._url = url.rstrip("/") + "/completion"
        self._timeout = aiohttp.ClientTimeout(total=timeout_ms / 1000.0)
        self._schema = router_json_schema()
        self._session: Optional[aiohttp.ClientSession] = None

    async def _sess(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def aclose(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def decide(self, text: str) -> RouterDecision:
        """Cümle → karar. İSTİSNA ATMAZ; hata → outcome='error' (çağıran ana modele düşer)."""
        d = RouterDecision(text=text)
        t0 = time.monotonic()
        try:
            payload = {
                "prompt": build_prompt(text),
                "json_schema": self._schema,   # grammar — geçersiz tool adı İMKÂNSIZ
                "cache_prompt": True,          # statik tool önekinin KV-cache'i (prefill ~99ms)
                "temperature": 0.0,
                "n_predict": 256,
                "stop": ["<|im_end|>"],
                # ── repeat_penalty: SİLME. multi_intent'i AYAKTA TUTAN ayar. ──
                # Benchmark Ollama üzerinde koştu; Ollama bu iki değeri VARSAYILAN olarak
                # uygular (repeat_penalty=1.1, repeat_last_n=64). llama-server uygulamaz.
                # Prompt'un KUYRUĞUNDA `{"tool": null, "args": {}, "multi_intent": false}`
                # örneği duruyor → `false` token'ı son-64 penceresine giriyor → ceza onu
                # bastırıyor ve SINIRDAKİ çok-niyetli cümleler `true`ya devriliyor.
                # ÖLÇÜLDÜ (132 vaka, TR):
                #   cezasız     : multi 50.0%  (m05/m06 KAÇIYOR) | trap 86.0% | high 69.2%
                #   rp=1.1      : multi 100.0% (6/6)             | trap 86.0% | high 76.9%
                #   benchmark   : multi 100.0%                   | trap 82.0% | high 69.2%
                # multi_intent = sessiz yarım-iş kalkanı; %50'ye düşerse kalkan DELİNİR
                # (yarısı sessizce yapılır). Recall (%94.1) her iki hâlde de aynı.
                "repeat_penalty": 1.1,
                "repeat_last_n": 64,
            }
            sess = await self._sess()
            async with sess.post(self._url, json=payload) as r:
                if r.status != 200:
                    d.error = f"http {r.status}"
                    d.latency_ms = (time.monotonic() - t0) * 1000
                    return d
                body = await r.json()
            d.latency_ms = (time.monotonic() - t0) * 1000
            obj = json.loads((body.get("content") or "").strip())
        except asyncio.TimeoutError:
            d.latency_ms = (time.monotonic() - t0) * 1000
            d.error = f"timeout >{self._timeout.total * 1000:.0f}ms"
            return d
        except Exception as e:  # noqa: BLE001 — HER hata ana modele düşüş demek
            d.latency_ms = (time.monotonic() - t0) * 1000
            d.error = repr(e)[:120]
            return d

        tool = obj.get("tool")
        if isinstance(tool, str) and tool.strip().lower() in ("", "null", "none"):
            tool = None            # grammar null verir; yine de metinsel "null"a karşı korun
        d.tool = tool if isinstance(tool, str) else None
        d.args = obj.get("args") if isinstance(obj.get("args"), dict) else {}
        d.multi_intent = bool(obj.get("multi_intent"))

        # ── karar ağacı ── (sıra ÖNEMLİ: multi_intent, tool'dan ÖNCE gelir)
        if d.multi_intent:
            d.outcome = "multi"    # tool'u AT — yarım iş yapma, ana model iki niyeti de görsün
            d.tool = None
        elif d.tool is None:
            d.outcome = "abstain"
        elif TOOL_TIER.get(d.tool) != "low":
            # Grammar enum'u zaten low ile sınırlı; buraya düşmek katalog bozulması demek.
            d.outcome = "no_exec"
            d.error = f"low olmayan tool sızdı: {d.tool} (tier={TOOL_TIER.get(d.tool)})"
        elif d.tool not in EXECUTORS:
            d.outcome = "no_exec"  # kodu yok (origin=%s) → ana model halleder
            d.error = f"executor yok (origin={TOOL_ORIGIN.get(d.tool)})"
        else:
            d.outcome = "shadow" if not ROUTER_EXECUTE else "executed"
        return d

    async def route(self, text: str) -> Optional[str]:
        """Ana giriş. Tool çalıştıysa SESLİ CEVAP metnini döner; aksi hâlde None
        (= ana modele düş). HİÇBİR KOŞULDA istisna atmaz."""
        if not ROUTER_ENABLED or not text.strip():
            return None
        try:
            d = await self.decide(text)
            if d.outcome != "executed":
                d.log()
                return None
            try:
                spoken = await EXECUTORS[d.tool](d.args)
            except Exception as e:  # noqa: BLE001 — executor patlarsa da ana modele düş
                d.outcome = "error"
                d.error = f"executor: {e!r}"[:120]
                d.log()
                return None
            d.log()
            return spoken
        except Exception as e:  # noqa: BLE001 — SON savunma: router asla turu bozmaz
            logger.warning("router beklenmedik hata → ana modele düşülüyor: %r", e)
            return None


# ---------------------------------------------------------------------------
# CLI: python worker/router.py "cümle"  → canlı servise tek cümle sor (curl yerine)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    async def _main() -> None:
        r = Router()
        for s in sys.argv[1:] or ["salondaki ışıkları kapat"]:
            d = await r.decide(s)
            d.log()
        await r.aclose()

    asyncio.run(_main())
