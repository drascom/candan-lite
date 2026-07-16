"""pi_brain — warm `pi --mode rpc` beyni, livekit-agents LLM adaptörü.

Tasarım: docs/pi-brain-design.md. Oturum başına BİR kalıcı `pi --mode rpc`
alt-süreci sürülür (stdin/stdout JSON-lines). Her chat turu stdin'e
`{"type":"prompt","message":...}` yazar; stdout'tan gelen `message_update`
event'lerindeki `assistantMessageEvent.text_delta` parçaları LLMStream olarak
stream edilir. Tur `agent_settled` event'inde biter. Barge-in `{"type":"abort"}`.

İki katman:
  - PiRpcClient   — saf asyncio alt-süreç RPC (livekit'e bağımsız; smoke test bunu kullanır).
  - PiBrain/PiStream — livekit-agents `llm.LLM` / `llm.LLMStream` adaptörü.

Smoke test (model tüketmez):  python pi_brain.py smoke
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from name_parser import (
    parse_spoken_name,
    is_affirmative_reply,
    _is_decline_enroll,
)
from log_utils import DedupeFilter

logger = logging.getLogger("pi_brain")
logger.addFilter(DedupeFilter())  # tekrarlayan warning/info loglarını seyreltir

# Repo kökü = worker/'ın bir üstü. cwd bu olmalı ki local pi/ ve sessions/ çözülsün.
REPO_ROOT = Path(__file__).resolve().parent.parent

PI_BIN = os.environ.get("PI_BIN", "pi")
# Global varsayılan model (gpt-5.6-luna) rpc'de bozuk ("Model not found") → pinlemek ZORUNLU.
PI_MODEL = os.environ.get("PI_MODEL", "openai-codex/gpt-5.6-terra")
PI_DEFAULT_PERSONA = os.environ.get("PI_DEFAULT_PERSONA", "candan")
PI_PERSONA_DIR = os.environ.get("PI_PERSONA_DIR", "pi/personas")
PI_SKILLS_DIR = os.environ.get("PI_SKILLS_DIR", "pi/skills")
PI_SESSION_DIR = os.environ.get("PI_SESSION_DIR", "sessions")
PI_AGENTS_MD = os.environ.get("PI_AGENTS_MD", "pi/AGENTS.md")
# Gecikme ayarı: thinking seviyesi. minimal en hızlı (ölçüldü: off=6.6s, minimal=2.6s,
# default=3.2s). Boş / "default" → bayrak eklenmez (pi'nın kendi varsayılanı).
PI_THINKING = os.environ.get("PI_THINKING", "minimal")

# ── BEYİN SEÇİMİ (oturum başında) ────────────────────────────────────────────
# Kullanıcı web'de oturum BAŞINDA hangi modelle konuşacağını seçer. Seçim token
# route'undan agent dispatch metadata'sına ({"brain":"local"|"remote"}) gömülür; worker
# `ctx.job.metadata` ile İŞ DOĞARKEN okur (agent.py) → pi alt-süreci doğru modelle doğar.
# Yarış YOK: seçim, pi süreci başlamadan ÖNCE eldedir.
#
# PI_THINKING modele BAĞLI (bkz. .env.example):
#   yerel  → "default": model reasoning'siz (models.json reasoning=false, sunucuda
#            --reasoning off). "minimal" desteklenmeyen bir thinking seviyesi zorlar.
#   uzak   → "minimal": en düşük gecikme (ölçüldü: off=6.6s, minimal=2.6s, default=3.2s).
#
# Seçim gelmezse/bozuksa → .env'deki PI_MODEL/PI_THINKING (bugünkü davranış).
BRAINS: dict[str, tuple[str, str]] = {
    "local": ("llama-cpp/gemma-4-12B-it-qat-q4_0", "default"),
    "remote": ("openai-codex/gpt-5.6-terra", "minimal"),
}


def resolve_brain(choice: Optional[str]) -> tuple[str, str]:
    """Beyin seçimini (model, thinking) çiftine çöz. Geçersiz/boş → .env varsayılanı."""
    key = (choice or "").strip().lower()
    if key in BRAINS:
        return BRAINS[key]
    return PI_MODEL, PI_THINKING


# Tur stall watchdog: son ilerlemeden (text_delta / başlangıç) bu kadar saniye HİÇ
# olay gelmezse turu temiz kapat (WebSocket 1000 gibi ~33-40s takılmalara karşı).
PI_TURN_STALL_TIMEOUT = float(os.environ.get("PI_TURN_STALL_TIMEOUT", "12") or 12)
# SOĞUK İLK TUR toleransı. Taze doğan pi süreci (boot / konuşmacı swap / mod swap)
# oturum geçmişini BAŞTAN yükler → llama-server'da KV cache soğuk → prefill 9-17s.
# ÖLÇÜLDÜ: 16:34:19 swap → 16:34:33 stall (12s), llama-server aynı anda
# "prompt eval time = 8893.82 ms / 8717 tokens". Yani watchdog SAĞLIKLI bir turu
# kesip "Bir saniye, tekrar dener misin?" yedeğini söyletiyordu. Sıcak turlar 0.4-1s.
# Bu tolerans SADECE sürecin ilk text_delta'sına kadar geçerli; delta gelir gelmez
# watchdog PI_TURN_STALL_TIMEOUT'a düşer → gerçek takılmalarda hız kaybı YOK.
PI_FIRST_TURN_STALL_TIMEOUT = float(
    os.environ.get("PI_FIRST_TURN_STALL_TIMEOUT", "45") or 45
)
# Soğuk yükleme bu saniyeyi geçerse kullanıcıya TEK kısa cümle söyle (10+ sn sessiz
# bekletmek kötü). 0 → hiç söyleme. Yalnız soğuk turda, tur başına EN FAZLA bir kez.
PI_COLD_NOTICE_DELAY = float(os.environ.get("PI_COLD_NOTICE_DELAY", "5") or 0)
PI_COLD_NOTICE_TEXT = (
    os.environ.get("PI_COLD_NOTICE_TEXT") or "Bir saniye, aklımı topluyorum."
)
# ── Compaction (bağlam sıkıştırma) ara sözü ─────────────────────────────────
# pi bağlam dolunca geçmişi özetler (`compaction_start` → uzun LLM çağrısı →
# `compaction_end`). ACİL DEĞİLSE bunu tur SONUNA saklar (agent-session
# _handlePostAgentRun) — oraya DOKUNMUYORUZ, cevap zaten söylenmiş olur.
# Sorun ACİL halde: bağlam taşarsa (reason="overflow") cevap ÜRETİLEMEDEN
# compaction çalışır → kullanıcı cevabını beklerken uzun süre SESSİZLİK duyar.
# Ayrım için reason'a değil, kullanıcının o turda BİR ŞEY DUYUP DUYMADIĞINA
# (got_delta) bakıyoruz: duyduysa sus, duymadıysa tek kısa cümle söyle.
PI_COMPACTION_NOTICE_TEXT = (
    os.environ.get("PI_COMPACTION_NOTICE_TEXT") or "Bir saniye, aklımı toparlıyorum."
)
# Compaction penceresinde watchdog toleransı. ZORUNLU: compaction_start ile
# compaction_end arasında pi HİÇBİR olay yaymaz (araya tek bir `compact()` LLM
# çağrısı giriyor) → normal 12s tolerans sağlıklı bir sıkıştırmayı keser, turu
# abort ederdi. Sıkıştırma tüm bağlamı özetler → dakikayı bulabilir.
PI_COMPACTION_STALL_TIMEOUT = float(
    os.environ.get("PI_COMPACTION_STALL_TIMEOUT", "120") or 120
)
# Hafıza (Faz A). memory/ yoksa/policy yoksa graceful → Faz 2/3 davranışı aynen.
MEMORY_DIR = os.environ.get("MEMORY_DIR", "memory")


def _envflag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


# Tool politikası (gecikme + güvenlik). pi bir KODLAMA ajanı: read/bash/edit/write/
# grep/find/ls built-in'leri açık ve oto-onaylı (--approve). Sesli asistanda bunların
# neredeyse tamamı gereksiz; her tool çağrısı fazladan bir model isteği bacağı =
# TTFT'yi katlıyor + ara sıra 30-100s takılma. Ayrıca asistanın repo'da oto-onaylı
# bash/edit çalıştırması GÜVENLİK riski (ölçümde bash ile dosyaya gerçekten yazdı).
#
# ÖLÇÜLDÜ (bench, tool_execution_start olayları sayılarak):
#   - `-nbt` TEK BAŞINA YETMİYOR: built-in'ler (read/find/ls) YİNE çağrıldı.
#   - Built-in'leri gerçekten kesen şey ALLOWLIST (`-t`). Allowlist ile: built-in
#     çağrısı SIFIR, memory_add/memory_search + web_search çalışmaya devam ediyor.
# Bu yüzden DEFAULT = allowlist (+ `-nbt` savunma katmanı olarak).
#
#   PI_TOOLS_ALLOWLIST (DEFAULT dolu) → `-t a,b,c`: built-in/extension/custom genelinde
#     allowlist; listede olmayan HİÇBİR tool çağrılamaz. Boşaltmak (="") → allowlist yok.
#   PI_NO_BUILTIN_TOOLS=true (DEFAULT) → `-nbt`: ek savunma katmanı (tek başına yetersiz).
# İkisini de kapatmak → pi'nın kendi varsayılanı (eski davranış; geri dönüş kolay).
PI_NO_BUILTIN_TOOLS = _envflag("PI_NO_BUILTIN_TOOLS", True)
# NOT: buraya EKLENMEYEN tool çalışmaz. reminder_* (proaktif hatırlatma) ve
# memory_consolidate (bağlam şişmesi) mem extension'ından geliyor → allowlist'te olmalı.
# SIRA ÖNEMLİ — dekoratif değil. Bu listenin sırası pi'nın modele gönderdiği `tools[]`
# dizisinin sırasını BİREBİR belirler (kanıt: pi 0.80.6 core/agent-session.js:1966-1986 →
# allowlist adları önce gelir, registry adları sonra eklenir, `new Set(...)` ilk görüleni
# tutar → allowlist sırası korunur; extension yükleme/`-e` sırası ETKİSİZ. Sahte
# llama-server proxy'siyle gerçek HTTP gövdesi yakalanarak doğrulandı).
#
# ÖLÇÜLEN HATA (N=20, gerçek 9 tool, "15 dk sonra çamaşırı al" isteği): model rol yapma
# modundayken (soul_add ile "korsan gibi konuş" kalıcı talimatı) ve `web_search`,
# `reminder_add`'den ÖNCE geldiğinde reminder_add çağrısı 5/20'ye düşüyor. 2x2 kontrol
# koşusu suçlunun SIRA olduğunu gösterdi (açıklama uzunluğu değil). Rol yapma tek başına
# ve sıra tek başına zararsız; ikisi birleşince çöküyor.
#
# KURAL: kritik EYLEM tool'ları (reminder_add, memory_add, soul_add) web_search/
# fetch_content'ten ÖNCE gelmeli. Yeni tool eklerken bu sırayı bozma.
PI_TOOLS_ALLOWLIST = os.environ.get(
    "PI_TOOLS_ALLOWLIST",
    # web_search    → @oresk/pi-searxng (kendi SearXNG'miz, .25:8888)
    # fetch_content → pi-web-access (doğrudan HTTP + Readability; sağlayıcı yok)
    "reminder_add,memory_add,soul_add,memory_search,"
    "reminder_list,reminder_cancel,web_search,fetch_content,memory_consolidate",
)

# pi'nın GLOBAL npm paket dizini (repo DIŞI). Web eklentileri buradan explicit `-e` ile
# yükleniyor — bkz. _build_pi_args(). `npm:paket` deseydik her pi doğuşunda temp kurulum
# olurdu (persona swap sık → saniyeler kaybı). Kurulum: docs/SEARXNG-KURULUM.md
PI_NPM_DIR = Path(
    os.environ.get("PI_NPM_DIR", os.path.expanduser("~/.pi/agent/npm/node_modules"))
)
# Eski Qwant araması (CAPTCHA yüzünden ÖLÜ) — sadece acil geri dönüş için. true yapılırsa
# SearXNG yerine o yüklenir. Dosya SİLİNMEDİ, sadece varsayılanda yüklenmiyor.
WEB_SEARCH_LEGACY_QWANT = _envflag("WEB_SEARCH_LEGACY_QWANT", False)

# Zaman dilimi: kullanıcı Londra'da. due_at hesabı pi extension'da (server-side),
# burada SADECE modele her turda verilen "şu an" satırı için kullanılır.
CANDAN_TZ = os.environ.get("CANDAN_TZ", "Europe/London")
# profile.md / family.md sert sınırı (bunlar HER TURDA bağlama enjekte edilir).
MEM_CONTEXT_LIMIT = int(os.environ.get("MEM_CONTEXT_LIMIT_BYTES", "2048") or 2048)
CONSOLIDATE_COOLDOWN = float(os.environ.get("CONSOLIDATE_COOLDOWN_SECONDS", "86400") or 86400)

# İZOLASYON (PI_ISOLATED, DEFAULT açık). Worker'ın pi süreci, kullanıcının GLOBAL pi
# kurulumundan (~/.pi/agent/: settings.json extensions+packages, skills/, prompts,
# themes, mcp.json) HİÇBİR ŞEY miras ALMAZ. Global kurulum DEĞİŞMEZ — sadece bu süreç
# izole başlar (kullanıcının kendi `pi`'ı aynen çalışır).
#
# ÖLÇÜLDÜ (izolasyon yokken worker pi'sinde yüklenen global şeyler, startup event'leri):
#   filechanges, memory.ts (global hafıza ext'i!), zz-read-only-mode, context.ts,
#   custom-header, md-link, ask-user-question, web-fetch/google-image-search/…,
#   npm paketleri (pi-web-access, @smoose/pi-beautify, pi-mcp-adapter) ve
#   pi-mcp-adapter üzerinden mcp.json → "MCP: 0/1 servers" (ha-builtin MCP sunucusu).
#
# Bayraklar (pi --help ile doğrulandı; hepsi "keşfi kapat", explicit yolları BOZMAZ):
#   -ne  --no-extensions       → sadece `-e` ile verilen ext'ler yüklenir (LOKAL mem yaşar)
#   -ns  --no-skills           → sadece `--skill` ile verilen skill'ler (LOKAL memory yaşar)
#   -np  --no-prompt-templates → global prompt template/komut keşfi kapalı
#   --no-themes                → global tema keşfi kapalı
#   -nc  --no-context-files    → global/ata AGENTS.md+CLAUDE.md keşfi kapalı
#                                (bizim pi/AGENTS.md zaten --append-system-prompt ile giriyor)
# PI_ISOLATED=false → eski davranış (global her şey tekrar sızar).
PI_ISOLATED = _envflag("PI_ISOLATED", True)


# ── SESLE GELİŞTİRME MODU (self-development, Faz 0) ───────────────────────────
# Kullanıcı sesle "geliştirme moduna geç" deyince worker pi alt-sürecini SWAP eder:
# normal beyin (Gemma, kod tool'ları KAPALI, family-memory AÇIK) → dev beyin (GPT-5.6,
# kod tool'ları AÇIK, family-memory KAPALI, izole git worktree, AYRI session-id).
# "Normal moda dön" → geri swap. Tetikleyici = native pi tool'u (enter_dev_mode/
# exit_dev_mode, pi/extensions/mode-switch): tool çağrısı worker'ın event akışından
# yakalanır → swap. DEV_MODE_ENABLED=false → tüm mekanizma kapalı, davranış bugünküyle
# BİRE BİR aynı (enter_dev_mode tool'u bile sunulmaz).
DEV_MODE_ENABLED = _envflag("DEV_MODE_ENABLED", True)
DEV_PERSONA = os.environ.get("DEV_PERSONA", "dev")
DEV_SESSION_ID = os.environ.get("DEV_SESSION_ID", "self-dev")
# Dev beyni: uzak güçlü model (GPT-5.6). GPU gerekmez (Codex uzak).
DEV_MODEL = os.environ.get("DEV_MODEL", "openai-codex/gpt-5.6-terra")
DEV_THINKING = os.environ.get("DEV_THINKING", "minimal")
# İzole çalışma dizini = ayrı git worktree + ayrı branch. İlk girişte oluşturulur,
# sonraki girişlerde tekrar kullanılır (bkz. _ensure_dev_worktree).
DEV_WORKTREE = Path(
    os.environ.get("DEV_WORKTREE", str(REPO_ROOT.parent / "candan-lite-selfdev"))
)
DEV_BRANCH = os.environ.get("DEV_BRANCH", "self-dev")
# Dev tool allowlist. BOŞ (default) → allowlist YOK + --no-builtin-tools YOK → tüm
# native kod tool'ları (read/bash/edit/write/grep/find/ls) + mode-switch (exit_dev_mode)
# açık. Kısıtlamak istersen virgüllü liste ver (o zaman exit_dev_mode'u da EKLE).
DEV_TOOLS_ALLOWLIST = os.environ.get("DEV_TOOLS_ALLOWLIST", "")


def _ensure_dev_worktree() -> Path:
    """Dev worktree'yi oluştur (ilk giriş) veya yeniden kullan. İzole branch = DEV_BRANCH.
    Var olan worktree'yi/branch'i ASLA sıfırlamaz → önceki dev işi korunur. Senkron; swap
    anında asyncio.to_thread ile çağrılır (event loop'u bloklamaz)."""
    import subprocess

    wt = DEV_WORKTREE
    if (wt / ".git").exists():
        return wt  # zaten kurulu worktree → tekrar kullan

    def _git(*a: str) -> "subprocess.CompletedProcess[str]":
        return subprocess.run(
            ["git", "-C", str(REPO_ROOT), *a], capture_output=True, text=True
        )

    branch_exists = (
        _git("rev-parse", "--verify", "--quiet", f"refs/heads/{DEV_BRANCH}").returncode == 0
    )
    if branch_exists:
        r = _git("worktree", "add", str(wt), DEV_BRANCH)
    else:
        r = _git("worktree", "add", "-b", DEV_BRANCH, str(wt))
    if r.returncode != 0:
        logger.warning("dev worktree kurulamadı: %s", (r.stderr or "").strip())
    else:
        logger.info("dev worktree hazır: %s (branch %s)", wt, DEV_BRANCH)
    return wt


# Wake word ("konuşma penceresi") — sistem sürekli açık; agent normalde uyur,
# WAKE_WORD duyunca uyanır, WAKE_WINDOW_SECONDS sessizlikten sonra tekrar uyur.
# WAKE_ENABLED=false → gate yok (her tur işlenir, mevcut davranış).
WAKE_ENABLED = _envflag("WAKE_ENABLED", True)
WAKE_WORD = os.environ.get("WAKE_WORD", "candan")
WAKE_WINDOW_SECONDS = float(os.environ.get("WAKE_WINDOW_SECONDS", "15") or 15)
# Fuzzy/fonetik wake toleransı: izole "candan"ın tutarlı yanlış-transkripsiyonları
# (Whisper'ın kısa-izole-kelime zaafı: "John Don", "Kandan", "Can dan"...). Sadece
# İZOLE-KISA metne uygulanır (cümle içinde DEĞİL → yanlış-pozitif olmasın). Varyant
# kümesi virgülle; default liste gözlenen yanlış çevirileri kapsar.
WAKE_VARIANTS = os.environ.get(
    "WAKE_VARIANTS", "candan,kandan,canden,candon,johndon,johndonne,jondon,candam"
)
# İzole-wake denemesi eşiği: en çok bu kadar kelime VE bu kadar (boşluksuz) karakter.
_WAKE_FUZZY_MAX_WORDS = 2
_WAKE_FUZZY_MAX_LEN = 12
# Bir varyanta izin verilen en büyük Levenshtein mesafesi (0 = sadece tam varyant).
_WAKE_FUZZY_DIST = 1


def _wake_norm(s: str) -> str:
    """Aksan/büyük-küçük duyarsız normalize: NFKD ile diakritikleri ayıkla + casefold.
    'Candan'/'CANDAN'/'çandan' → 'candan'."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.casefold()


def _has_wake(text: str, wake_norm: str) -> bool:
    """Metinde wake word var mı — kelime-sınırı, aksan/case duyarsız."""
    return any(_wake_norm(tok) == wake_norm for tok in re.findall(r"\w+", text or "", re.UNICODE))


def _strip_wake(text: str, wake_norm: str) -> str:
    """Wake word token'larını metinden ayıkla; kalan metni temizle (kelime-sınırı)."""
    out = []
    for tok in re.findall(r"\w+|\W+", text or "", re.UNICODE):
        if re.fullmatch(r"\w+", tok, re.UNICODE) and _wake_norm(tok) == wake_norm:
            continue
        out.append(tok)
    # Baştaki/sondaki noktalama+boşluğu kırp, iç boşlukları sadeleştir.
    return re.sub(r"\s+", " ", "".join(out)).strip(" ,.!?;:-\n\t")


def _wake_squash(s: str) -> str:
    """Fuzzy karşılaştırma için: normalize (diakritik/case) + TÜM boşluk/noktalamayı
    at. 'Can dan.'→'candan', 'John Don'→'johndon', 'CANDAN'→'candan'."""
    return re.sub(r"[^\w]", "", _wake_norm(s), flags=re.UNICODE)


def _levenshtein(a: str, b: str) -> int:
    """Küçük saf-Python Levenshtein mesafesi (kısa wake string'leri için)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _wake_variants(word: str = WAKE_WORD, raw: str = WAKE_VARIANTS) -> frozenset:
    """Fuzzy varyant kümesi (squash edilmiş): wake word + WAKE_VARIANTS listesi."""
    out = {_wake_squash(word)}
    for v in (raw or "").split(","):
        v = _wake_squash(v)
        if v:
            out.add(v)
    return frozenset(out)


def wake_match(text: str, wake_norm: Optional[str] = None,
               variants: Optional[frozenset] = None) -> tuple[bool, str]:
    """MERKEZİ wake eşleştirme (pi_brain + wake_stt bunu paylaşır → kopya sapmaz).

    Döner: (eşleşti?, kalan_metin).
      1. Gerçek "candan" kelimesi (izole ya da cümle içinde, kelime-sınırı/aksan/case
         duyarsız) → exact eşleşme; kalan `_strip_wake` ile ayıklanır. MEVCUT DAVRANIŞ
         KORUNUR ("candan hava nasıl" → strip "hava nasıl"; sadece "candan" → "").
      2. Exact yoksa ve metin İZOLE-KISA ise (≤2 kelime VE ≤~12 karakter) → boşluksuz
         normalize edilmiş metin fuzzy varyant kümesine yakınsa (tam eşit veya
         Levenshtein ≤ _WAKE_FUZZY_DIST) → wake (kalan boş). Bu, izole "candan"ın
         yanlış-transkripsiyonlarını ("John Don", "Kandan", "Can dan") yakalar.
      3. UZUN cümlede fuzzy UYGULANMAZ → cümlede "kandan"/"john don" wake TETİKLEMEZ."""
    wake_norm = _wake_norm(WAKE_WORD) if wake_norm is None else wake_norm
    if _has_wake(text, wake_norm):
        return True, _strip_wake(text, wake_norm)
    squashed = _wake_squash(text)
    words = re.findall(r"\w+", text or "", re.UNICODE)
    if squashed and len(words) <= _WAKE_FUZZY_MAX_WORDS and len(squashed) <= _WAKE_FUZZY_MAX_LEN:
        vs = _wake_variants() if variants is None else variants
        for v in vs:
            if squashed == v or _levenshtein(squashed, v) <= _WAKE_FUZZY_DIST:
                return True, ""
    return False, text


# ── Sohbet sıfırlama komutu (deterministik ifade eşleşmesi) ──────────────────
# NEDEN LLM'e SORULMUYOR: ölçtük — model, uygulanabilir görünen talimatı bir tool
# ÇAĞIRARAK değil, "tamam yaptım" DİYEREK geçiştiriyor (soul_add 0/8). "Yeni sohbet
# başlat" aynı sınıfta → tool'a bağlansaydı sıfırlama hiç OLMAZDI. Bu yüzden wake
# word gibi: transkript üzerinde, pi'ya prompt GİTMEDEN, burada eşleşir.
RESET_ENABLED = (os.environ.get("RESET_ENABLED", "true") or "").strip().lower() not in (
    "0", "false", "no", "off",
)
# Sıfırlama ifadeleri (virgülle). Eşleşme aksan/case/noktalama duyarsız (_wake_squash).
RESET_PHRASES = os.environ.get(
    "RESET_PHRASES",
    # ÇIKARMA ÖLÇÜTÜ = MUĞLAKLIK, "gereksiz tekrar" DEĞİL. Ölçüldü: listeden düşen bir
    # ifade yakın-ıska bandına (2,4] DÜŞMÜYOR, çok uzak kalıyor → sessizce LLM'e gider
    # → model "yaptım" der (sabah 2 kez yaşandı). Yani zararsız eşanlamlıyı silmek
    # sessiz-ıska deliğini geri AÇAR; maliyeti sıfır olanı listede tutmak bedava sigorta.
    # ÇIKARILANLAR (yalnız muğlak olanlar):
    #   "baştan başla"  → "baştan anlat" ile mesafe 3 = yakın-ıska → her seferinde
    #                     gereksiz soru. Canlı doğrulandı (18:29:27).
    #   "yeni sayfa aç" → kitap/tarayıcı açmak da olabilir.
    #   "yeni sohbet"   → iki kelime; tolerans 2 ile fazla gevşek.
    # "her şeyi sil"/"her şeyi sıfırla" BİLEREK YOK: sıfırlama hiçbir şey SİLMEZ
    # (memory/ korunur, dosya arşivlenir) → kullanıcı hafızası silindi SANIRDI. Ayrıca
    # ileride gerçek bir "beni unut" özelliği yapılırsa o ifade ONA ait olmalı.
    "yeni oturum aç,yeni oturum başlat,yeni sohbet başlat,yeni sohbete başla,"
    "sohbeti sıfırla,oturumu sıfırla,sıfırdan başlayalım,sohbeti resetle,"
    "geçmişi sıfırla,geçmişi temizle,sohbeti temizle,yeni konuşma başlat,oturumu yenile",
)
# Sıfırlama sonrası sesli/görsel onay (kullanıcı komutun İŞLEDİĞİNİ duysun).
RESET_ACK = os.environ.get("RESET_ACK", "Tamam, yeni sohbet başlattım. Seni dinliyorum.")
RESET_FAIL = os.environ.get("RESET_FAIL", "Şu anda sohbeti sıfırlayamadım, sonra tekrar deneyelim.")
# Yakın-ıska soru/ret satırları (bkz. reset_near_match).
RESET_CONFIRM_ASK = os.environ.get(
    "RESET_CONFIRM_ASK", "Yeni sohbet başlatmamı mı istiyorsun?"
)
RESET_CONFIRM_NO = os.environ.get("RESET_CONFIRM_NO", "Tamam, devam ediyoruz.")
# Komut denemesi eşiği: sıfırlama YIKICI-hissi bir işlem → sadece KISA/kasıtlı sözde
# ara. Uzun cümle içinde ("dün yeni sohbet başlat demiştim") TETİKLEMEZ.
# 5 → 7: kullanıcı doğal konuşuyor ("bu oturumu kapatıp yeni bir oturum açar mısın"
# 7 kelimeydi). Yanlış-pozitif riski DÜŞÜK: asıl koruma kelime sayısı değil, TÜM
# cümlenin squash'ı ile ifadenin squash'ı arasındaki mesafe (uzun cümle → uzak).
_RESET_MAX_WORDS = 7
# 1 → 2. NEDEN: "başlat" → "başladı" bir STT kazası DEĞİL, Türkçe'de yapısal — emir
# kipi ile geçmiş zaman tek-iki harfle ayrışıyor. Ölçüldü: "yeni sohbet başladı"
# mesafe 2 idi → tolerans 1 ile SESSİZCE ıskalandı, metin LLM'e gitti, model
# sıfırlamayı yapmadan "yaptım" dedi. Kullanıcı sıfırladım sandı.
_RESET_FUZZY_DIST = 2
# Yakın-ıska bandının üst sınırı: (_RESET_FUZZY_DIST, _RESET_NEAR_DIST] aralığı
# "benziyor ama emin değilim" demek → YÜRÜTME, SOR. Levenshtein >= |uzunluk farkı|
# olduğundan 4 mesafe zaten uzunlukları 4 içine hapseder → ayrı uzunluk guard'ı
# gerekmez. Bant genişletmenin maliyeti yanlış SİLME değil, fazladan bir SORU.
_RESET_NEAR_DIST = 4


def _reset_squash(s: str) -> str:
    """_wake_squash + Türkçe noktasız-ı katlaması ('ı'→'i').

    NEDEN: NFKD 'ş'→'s', 'ğ'→'g' yapar ama 'ı' AYRI bir harftir, 'i'ye inmez. STT
    kimi zaman "sıfırla", kimi zaman ASCII "sifirla" yazar → squash'lar 2 karakter
    ayrışır, _RESET_FUZZY_DIST(1) yetmez, komut SESSİZCE kaçardı. İki yazımı da
    aynı forma indiriyoruz. (wake yolu ETKİLENMEZ — _wake_squash'a dokunulmadı.)"""
    return _wake_squash(s).replace("ı", "i")


def _reset_phrases(raw: str = RESET_PHRASES) -> frozenset:
    """Sıfırlama ifadelerinin squash edilmiş (boşluksuz/aksansız) kümesi."""
    out = set()
    for p in (raw or "").split(","):
        p = _reset_squash(p)
        if p:
            out.add(p)
    return frozenset(out)


def _reset_distance(text: str, phrases: Optional[frozenset] = None) -> Optional[int]:
    """Metnin sıfırlama ifadelerine EN YAKIN squash mesafesi; komut hiç denenmiyorsa None.

    Wake word ZATEN ayıklanmış metin beklenir ("candan yeni sohbet başlat" →
    "yeni sohbet başlat"). Sadece KISA söz (≤_RESET_MAX_WORDS) denenir → uzun
    cümlede geçen aynı kelimeler sıfırlama TETİKLEMEZ (yanlış-pozitif koruması).
    None ("hiç bakmadık") ile büyük mesafe ("baktık, uzak") AYRI: yakın-ıska
    yolu ikisini farklı ele alır."""
    if not RESET_ENABLED:
        return None
    words = re.findall(r"\w+", text or "", re.UNICODE)
    if not words or len(words) > _RESET_MAX_WORDS:
        return None
    squashed = _reset_squash(text)
    if not squashed:
        return None
    ps = _reset_phrases() if phrases is None else phrases
    if not ps:
        return None
    return min(_levenshtein(squashed, p) for p in ps)


def reset_match(text: str, phrases: Optional[frozenset] = None) -> bool:
    """Metin bir sohbet-sıfırlama komutu mu? Deterministik (LLM YOK). Tam eşleşme
    bandı: mesafe ≤ _RESET_FUZZY_DIST → SORMADAN yürüt."""
    d = _reset_distance(text, phrases)
    return d is not None and d <= _RESET_FUZZY_DIST


def reset_near_match(text: str, phrases: Optional[frozenset] = None) -> bool:
    """Yakın-ıska: sıfırlamaya BENZİYOR ama tam eşleşmiyor → YÜRÜTME, SOR.

    NEDEN: tolerans ne olursa olsun eşiğin bir kenarı vardır ve kenarın dışına
    düşen kasıtlı komut bugüne kadar SESSİZCE LLM'e gidiyordu — model de tool
    çağırmadan "yaptım" diyordu. Bant, o kenarı sessiz-ıska yerine SORU'ya
    çevirir: model SORAR, deterministik kod YÜRÜTÜR (sıfırlama ASLA modele
    bırakılmaz)."""
    d = _reset_distance(text, phrases)
    return d is not None and _RESET_FUZZY_DIST < d <= _RESET_NEAR_DIST


def _find_session_file(session_id: str, session_dir: Path) -> Optional[Path]:
    """`sessions/` içinde header id'si `session_id` olan jsonl'i bul (pi'nın
    --session-id çözümüyle AYNI kural: dosya ADI değil, ilk satırdaki header.id).
    Bulunamazsa None. Sadece OKUR."""
    if not session_dir.is_dir():
        return None
    for p in sorted(session_dir.glob("*.jsonl"), reverse=True):
        try:
            with p.open("r", encoding="utf-8", errors="replace") as f:
                first = f.readline()
            hdr = json.loads(first)
        except Exception:  # noqa: BLE001 — bozuk/boş dosya → aday değil
            continue
        if isinstance(hdr, dict) and hdr.get("type") == "session" and hdr.get("id") == session_id:
            return p
    return None


def _rotate_session_id(session_id: str, session_dir: Path) -> Optional[Path]:
    """Sohbet geçmişini "sıfırla" — SİLMEDEN.

    Eski jsonl'in header `id`'sini `<slug>-<timestamp>` ile döndürür (dosya YERİNDE
    kalır, tek satır değişir). Sonuç: pi bir daha `--session-id <slug>` ile o dosyayı
    BULAMAZ → taze oturum açar; eski geçmiş diskte ve panoda (tools/dashboard.py
    dosya ADINDAN kişi çıkarır, ad DEĞİŞMEZ) okunabilir kalır.

    NEDEN dosyayı taşımıyoruz: pi'nın tarama'sı `sessions/` KÖKÜNDE non-recursive
    (`readdir` + `.jsonl`), alt dizin resume'a girmez — ama pano da göremezdi.
    NEDEN pi'nın `new_session` RPC'si TEK BAŞINA yetmiyor: o, süreç-içi dosyayı
    `<ts>_<rastgele-id>.jsonl`'e çevirir ama ESKİ dosyanın header id'si `<slug>`
    kalır → worker bir sonraki açılışta `--session-id <slug>` ile ESKİ geçmişi
    yeniden resume ederdi (sıfırlama kalıcı OLMAZDI).

    Döner: arşivlenen dosya (yoksa None = zaten temiz, sıfırlanacak bir şey yok)."""
    p = _find_session_file(session_id, session_dir)
    if p is None:
        return None
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    if not lines:
        return None
    hdr = json.loads(lines[0])
    stamp = time.strftime("%Y%m%dT%H%M%S", time.localtime())
    # assertValidSessionId: sadece [A-Za-z0-9._-], alnum ile başla/bit → slug+stamp uyar.
    hdr["id"] = f"{session_id}-eski-{stamp}"
    lines[0] = json.dumps(hdr, ensure_ascii=False) + "\n"
    # Atomik: geçici dosyaya yaz + replace → yarıda kesilirse eski dosya BOZULMAZ.
    tmp = p.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(lines), encoding="utf-8")
    os.replace(tmp, p)
    return p


class WakeGate:
    """Konuşma-penceresi kapısı (saf-Python, livekit'siz test edilebilir).

    Uyurken: wake word yoksa 'silent' (pi'ya gitme, ChatChunk yok). Wake word
    varsa uyan, kalan metin varsa 'process', yoksa 'scripted' (kısa karşılık).
    Uyanıkken: 'process' (pencere sıfırlanır). WAKE_WINDOW_SECONDS sessizlikten
    sonra tekrar uyur. enabled=False → hep 'process' (gate yok).

    Uyku sayacı (last_activity) — "SON konuşmadan sonra" saymalı:
      - kullanıcı konuşurken (VAD 'speaking' / STT partial) ve asistan cevap
        verirken (thinking/speaking) sayaç DURUR (busy) → o sırada ASLA uyunmaz;
      - ikisinden hangisi SONRA biterse sayaç ORADAN başlar (set_* → touch).
    Böylece uzun kullanıcı sözü / uzun asistan cevabı sırasında pencere dolmaz."""

    def __init__(self, enabled: bool = WAKE_ENABLED, word: str = WAKE_WORD,
                 window: float = WAKE_WINDOW_SECONDS, greeting: str = "Efendim?",
                 on_change: Optional[Callable[[bool], None]] = None):
        self.enabled = enabled
        self.wake_norm = _wake_norm(word)
        self.wake_variants = _wake_variants(word)
        self.window = window
        self.greeting = greeting
        self.awake = False
        self.last_activity = 0.0
        # Sayaç duraklatma bayrakları (agent.py: user_state_changed/agent_state_changed).
        self.user_speaking = False   # kullanıcı şu an konuşuyor (VAD)
        self.agent_busy = False      # asistan düşünüyor/konuşuyor (cevap sürüyor)
        # Proaktif seslenme sürerken True: kullanıcının onay sözü ("efendim") pi'ya
        # GİTMESİN (yoksa hem biz hatırlatmayı iletiriz hem pi ayrıca cevap verir).
        self.hold = False
        # Uyku↔uyanık GEÇİŞİNDE çağrılır (sync). Web'e attribute yayını + transcript
        # kapısı buraya bağlanır. None → geçiş sinyali yok (mevcut davranış).
        self.on_change = on_change

    # ── uyku sayacı: aktivite kancaları ──────────────────────────────────
    def busy(self) -> bool:
        """Sayaç DURSUN mu? (kullanıcı konuşuyor ya da asistan cevap veriyor)"""
        return self.user_speaking or self.agent_busy

    def touch(self, now: Optional[float] = None) -> None:
        """Aktivite gördük → uyku sayacını sıfırla. UYANDIRMAZ (awake'e dokunmaz);
        uyurken çağrılması zararsızdır."""
        self.last_activity = time.monotonic() if now is None else now

    def set_user_speaking(self, speaking: bool, now: Optional[float] = None) -> None:
        """VAD: kullanıcı konuşmaya başladı/bitti. Bittiğinde sayaç TAM bu andan başlar."""
        self.user_speaking = bool(speaking)
        self.touch(now)

    def set_agent_busy(self, busy: bool, now: Optional[float] = None) -> None:
        """Asistan cevabı başladı (thinking/speaking) / bitti. Bittiğinde sayaç bu andan
        başlar (kullanıcı cevabı bekleniyor olabilir)."""
        self.agent_busy = bool(busy)
        self.touch(now)

    def _set_awake(self, value: bool) -> None:
        """awake'i değiştir; DEĞİŞTİYSE on_change(value) tetikle (best-effort)."""
        if value == self.awake:
            return
        self.awake = value
        cb = self.on_change
        if cb is not None:
            try:
                cb(value)
            except Exception:  # noqa: BLE001 — sinyal hatası akışı bozmasın
                logger.warning("wake on_change hata", exc_info=True)

    def expire(self, now: Optional[float] = None) -> bool:
        """Pencere dolduysa uyut. Yeni uyuduysa True döner.

        Konuşma sürerken (kullanıcı ya da asistan) sayaç DURUR: uykuya geçilmez ve
        last_activity kayar → konuşma bitince 15sn TAM o andan itibaren sayılır."""
        now = time.monotonic() if now is None else now
        if self.busy():
            self.last_activity = now
            return False
        if self.awake and (now - self.last_activity) >= self.window:
            self._set_awake(False)
            return True
        return False

    def wake_now(self, now: Optional[float] = None) -> bool:
        """Erken uyandır (transcript anında, PiBrain turundan ÖNCE). awake=True yap +
        on_change(True) tetikle (çan). Zaten uyanıksa TEKRAR tetikleme (çift çan yok).
        last_activity'i sıfırla. Idempotent; yeni uyandıysa True döner. Kapalı → no-op."""
        if not self.enabled:
            return False
        now = time.monotonic() if now is None else now
        was = self.awake
        self._set_awake(True)   # değiştiyse on_change(True) → çan (idempotent)
        self.last_activity = now
        return not was

    def sleep_now(self) -> bool:
        """Erken uyut (pencereyi KAPAT). Proaktif seslenmeye cevap gelmediğinde kullanılır:
        seslenmek için uyandırdık, karşılık yoksa açık bırakmak boşuna dinleme/token demek.
        Uyanıktan uykuya GEÇTİYSE True + on_change(False) (çan/attribute). Kapalı → no-op."""
        if not self.enabled:
            return False
        was = self.awake
        self._set_awake(False)
        return was

    def decide(self, text: str, now: Optional[float] = None) -> tuple[str, Optional[str]]:
        """('process', metin) | ('scripted', satır) | ('silent', None).

        "candan" TEK BAŞINA (uyurken ya da uyanıkken) → 'silent': uyan (çan) ama
        pi'ya GİTME, sözlü yanıt YOK. Wake + kalan metin → uyan + 'process' (kalan).
        Uyurken + wake yok → 'silent'."""
        if self.hold:
            # Proaktif seslenme sürüyor: kullanıcının onay sözünü BİZ işliyoruz,
            # pi'ya gitmesin (çift cevap yok). Kısa ve deterministik kapı.
            return ("silent", None)
        if not self.enabled:
            return ("process", text)
        now = time.monotonic() if now is None else now
        self.expire(now)
        has_wake, rem = wake_match(text, self.wake_norm, self.wake_variants)
        if self.awake:
            self.last_activity = now
            if has_wake:
                # sadece "candan" (kalan boş) → çan zaten çaldı, pi'ya gitme.
                return ("process", rem) if rem else ("silent", None)
            return ("process", text)
        if has_wake:
            self._set_awake(True)   # uyan → on_change(True) → çan
            self.last_activity = now
            if rem:
                return ("process", rem)   # "candan hava nasıl" → kalanı işle (geri uyumlu)
            return ("silent", None)        # sadece wake → SADECE çan, sözlü yanıt yok
        return ("silent", None)


def _policy_path() -> Path:
    """memory/policy.json (MEMORY_DIR mutlak yol ise o kullanılır — test izolasyonu)."""
    return REPO_ROOT / MEMORY_DIR / "policy.json"


def _read_policy() -> dict:
    """policy.json → dict. Dosya yok / bozuk / dict değil → {} (güvenli taban)."""
    try:
        pol = json.loads(_policy_path().read_text())
    except Exception:
        return {}
    return pol if isinstance(pol, dict) else {}


ROLES = ("adult", "child", "guest")


def _policy_set(user: str, role: Optional[str] = None) -> Optional[str]:
    """policy.json'a rol yaz — KİLİTLİ + ATOMİK (flock + tmp dosya + os.replace).

    role verilirse o rol yazılır (yükseltme/düşürme).
    role=None → ENROLL kuralı, kilit altında karar verilir:
      - kullanıcı policy'de zaten varsa → DOKUNMA, mevcut rolü döner
      - policy BOŞSA → 'adult' (ilk tanışılan = ev sahibi)
      - policy DOLUYSA → 'guest' (sonradan tanışılan; adult sözle yükseltebilir)
    Dönen: kullanıcının nihai rolü; yazılamazsa None."""
    import fcntl
    import tempfile

    if not user or (role is not None and role not in ROLES):
        return None
    path = _policy_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = path.parent / (path.name + ".lock")
        with open(lock, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                pol = _read_policy()
                if role is None:  # enroll kuralı (kilit altında → yarış yok)
                    if user in pol:
                        return pol[user]
                    role = "adult" if not pol else "guest"
                if pol.get(user) == role:
                    return role
                pol[user] = role
                fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".policy-", suffix=".json")
                try:
                    with os.fdopen(fd, "w") as f:
                        json.dump(pol, f, ensure_ascii=False, indent=2, sort_keys=True)
                        f.write("\n")
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp, path)  # atomik: yarıda kalan yazım policy'yi bozmaz
                except BaseException:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                    raise
                return role
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
    except Exception as e:  # noqa: BLE001
        logger.warning("policy yazılamadı (%s=%s): %s", user, role, e)
        return None


def _role(user: str) -> str:
    """memory/policy.json'dan rol; dosya/policy yoksa veya okunamıyorsa 'guest'."""
    return _read_policy().get(user, "guest") or "guest"


def _mem_user(user: str) -> str:
    """Hafıza kimliği (MEM_USER): tanınan slug ANCAK role != guest ise; yoksa ''.
    Guest/unknown → '' (hafıza yok). candan (default persona, policy'de yok) da ''."""
    return user if (user and _role(user) != "guest") else ""


def _slug(name: str) -> str:
    """İsmi dosya/oturum-güvenli slug'a çevir (persona dosyası + session-id için).
    policy.json anahtarı == MEM_USER == memory/users/<user>/ dizini = BU slug."""
    s = "".join(c if (c.isalnum() or c in "-_") else "-" for c in (name or "").strip().lower())
    return "-".join(p for p in s.split("-") if p) or ""


# "Ayhan'ı yetişkin yap" / "Ayhan'ı aileye ekle" → rol yükseltme komutu (SADECE adult).
_PROMOTE_RE = re.compile(
    r"(?:yeti[şs]kin\s+yap"
    r"|aile\s+[üu]yesi\s+yap"
    r"|aile(?:m[ıi]z)?y?[ea]\s+(?:ekle|kat|al))",  # aileye / ailemize / aileme
    re.IGNORECASE,
)
_APOSTROPHES = "'’‘´`"


def parse_promote(text: str) -> Optional[str]:
    """Rol-yükseltme cümlesindeki hedef ismi çıkar; komut değilse None.
    "Ayhan'ı yetişkin yap" → "Ayhan" | "Zeynep'i aileye ekle" → "Zeynep"."""
    m = _PROMOTE_RE.search(text or "")
    if not m:
        return None
    head = (text[: m.start()]).strip()
    if not head:
        return None
    tok = head.split()[-1].strip(".,;:!?\"()")
    for ap in _APOSTROPHES:  # kesme işaretli ek: "ayhan'ı" → "ayhan"
        if ap in tok:
            tok = tok.split(ap)[0]
            break
    return tok or None


def _persona_exists(persona: str) -> bool:
    return (REPO_ROOT / PI_PERSONA_DIR / f"{persona}.md").is_file()


# ── Prewarm tahmini: son bilinen konuşmacı ───────────────────────────────────
# NEDEN: job init'te varsayılan persona (candan/candan) için warm pi kuruluyordu;
# kullanıcı konuşup speaker-ID onu Ayhan yapınca _current_client o warm süreci ÇÖPE
# ATIP sıfırdan ayhan/ayhan doğuruyordu. Yani prewarm HER oturumda israftı ve soğuk
# yükleme maliyeti kullanıcı konuştuktan SONRA ödeniyordu (ölçüm: 17:14:34 wake →
# aynı saniye swap → cevap 17:15:43, 69 sn). Son konuşanı ısıtırsak tahmin TUTTUĞUNDA
# swap hiç olmaz. Tahmin TUTMAZSA swap yolu aynen çalışır → bugünkünden kötü değil.
# İşaret dosyası çünkü: speakers.db'deki updated_at son ENROLL'u gösterir, son
# GÖRÜLMEyi değil — tanınmak updated_at'i bümez, o yüzden proxy olarak yanlış.
def _last_speaker_path() -> Path:
    """memory/last_speaker.json (MEMORY_DIR mutlak ise o — test izolasyonu)."""
    return REPO_ROOT / MEMORY_DIR / "last_speaker.json"


def read_last_speaker() -> Optional[str]:
    """Son tanınan konuşmacının slug'ı. Dosya yok/bozuk → None (sessizce varsayılana
    düşülür). Bu bir İPUCU'dur, doğruluk garantisi değil."""
    try:
        d = json.loads(_last_speaker_path().read_text())
    except Exception:  # noqa: BLE001 — ipucu okunamazsa varsayılanla devam
        return None
    slug = d.get("slug") if isinstance(d, dict) else None
    return slug if isinstance(slug, str) and slug else None


def write_last_speaker(slug: str) -> None:
    """İşaret dosyasını ATOMİK yaz (tmp + os.replace). Kilit YOK: bu sadece bir
    prewarm ipucu, son yazan kazanır — yanlış değer en fazla bir swap'a mal olur.
    Best-effort: hata konuşmayı BOZMAZ."""
    import tempfile

    if not slug:
        return
    path = _last_speaker_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".last-speaker-", suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"slug": slug, "at": time.time()}, f, ensure_ascii=False)
                f.write("\n")
            os.replace(tmp, path)  # atomik: yarıda kalan yazım ipucunu bozmaz
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception:  # noqa: BLE001 — ipucu yazılamazsa davranış bugünküyle AYNI
        logger.debug("last_speaker yazılamadı", exc_info=True)


def _build_pi_args(
    persona: str,
    session_id: str,
    model: Optional[str] = None,
    thinking: Optional[str] = None,
    dev: bool = False,
) -> list[str]:
    """pi --mode rpc bayrakları (docs/pi-brain-design.md).

    model/thinking: oturum başı beyin seçimi (bkz. BRAINS/resolve_brain). None →
    .env varsayılanı (PI_MODEL/PI_THINKING) = bugünkü davranış.

    dev=True → SESLE GELİŞTİRME modu (bkz. DEV_MODE_ENABLED): native kod tool'ları
    AÇIK (--no-builtin-tools YOK, dev allowlist), family-memory ve kişisel hafıza
    enjeksiyonu KAPALI (dev sohbeti asistanın hafızasına karışmaz). dev=False →
    bugünkü normal davranış BİRE BİR korunur."""
    model = model or PI_MODEL
    thinking = PI_THINKING if thinking is None else thinking
    args = [PI_BIN, "--mode", "rpc", "--approve", "--model", model]
    # İzolasyon: global (~/.pi/agent) extension/skill/prompt/theme/context keşfini kapat.
    # Aşağıdaki explicit `-e` / `--skill` / `--append-system-prompt` yolları etkilenmez.
    if PI_ISOLATED:
        args += ["--no-extensions", "--no-skills", "--no-prompt-templates",
                 "--no-themes", "--no-context-files"]
    # Gecikme: thinking seviyesi (minimal en hızlı). Boş/"default" → pi varsayılanı.
    if thinking and thinking.lower() != "default":
        args += ["--thinking", thinking]
    # Tool politikası: built-in'leri (read/edit/bash/grep/web_search…) kapat; lokal mem
    # extension'ı (memory_add/memory_search) yaşasın. İsteğe bağlı allowlist ile tek tek
    # tool geri açılabilir (ör. web_search).
    # Dev modunda native kod tool'ları AÇIK → --no-builtin-tools EKLENMEZ.
    if PI_NO_BUILTIN_TOOLS and not dev:
        args += ["--no-builtin-tools"]
    raw_allow = DEV_TOOLS_ALLOWLIST if dev else PI_TOOLS_ALLOWLIST
    allow_items = [t.strip() for t in raw_allow.split(",") if t.strip()]
    # Mod-değişim tetikleyici tool'u ilgili modun allowlist'ine gir: normal → enter,
    # dev → exit. Allowlist boşsa (dev default) HİÇ eklenmez → kısıtlama yok, tool zaten
    # yüklü extension'dan çağrılabilir. Böylece boş-allowlist = "tümü açık" korunur.
    if DEV_MODE_ENABLED and allow_items:
        mode_tool = "exit_dev_mode" if dev else "enter_dev_mode"
        if mode_tool not in allow_items:
            allow_items.append(mode_tool)
    allowlist = ",".join(allow_items)
    if allowlist:
        args += ["--tools", allowlist]
    # Ortak taban + kişilik overlay'i sistem prompt'una ekle.
    agents_md = REPO_ROOT / PI_AGENTS_MD
    if agents_md.is_file():
        args += ["--append-system-prompt", str(agents_md)]
    persona_file = REPO_ROOT / PI_PERSONA_DIR / f"{persona}.md"
    if persona_file.is_file():
        args += ["--append-system-prompt", str(persona_file)]
    # Kişiye özel agent "ruhu" (kalıcı davranış hafızası; soul_add tool'u yazar).
    # Ortak taban memory/soul.md HERKESE (guest dahil) yüklenir. Kişiye özel olan
    # (memory/users/<user>/soul.md) SADECE tanınan kullanıcıya ve ortak tabanın
    # ARDINDAN (sonra gelen = öncelikli) yüklenir → çelişirse kişininki geçerli.
    # Dosya yoksa graceful: hiçbir şey eklenmez, davranış bugünküyle aynı.
    # Kişisel/aile hafıza enjeksiyonu SADECE normal modda. Dev modunda KAPALI: dev
    # sohbeti asistanın hafızasını bağlamına almaz (ve family-memory tool'u da yüklenmez
    # → dev sohbeti hafızaya YAZAMAZ; ikinci karışmama garantisi).
    if not dev:
        soul_common = REPO_ROOT / MEMORY_DIR / "soul.md"
        if soul_common.is_file():
            args += ["--append-system-prompt", str(soul_common)]
        # Hafıza çekirdeği (küçük, boot'ta yüklü). Kullanıcı kimliği = session_id slug'ı
        # (tanınan kişi). Guest/unknown → mem_user boş → hiçbir şey eklenmez (Faz 2 aynen).
        mem_user = _mem_user(session_id)
        if mem_user:
            mem = REPO_ROOT / MEMORY_DIR
            profile = mem / "users" / mem_user / "profile.md"
            if profile.is_file():
                args += ["--append-system-prompt", str(profile)]
            family = mem / "family.md"
            if family.is_file():  # role != guest zaten garanti (mem_user dolu)
                args += ["--append-system-prompt", str(family)]
            # Kişiye özel ruh (ortak tabanın ÜSTÜNDE; çelişirse bu geçerli).
            soul = mem / "users" / mem_user / "soul.md"
            if soul.is_file():
                args += ["--append-system-prompt", str(soul)]
            # Sapma #4: pi $MEM_USER shell-expand'ine güvenme; açık kimlik satırı enjekte et.
            args += [
                "--append-system-prompt",
                (f"Aktif kullanıcı: {mem_user}. "
                 f"Hafıza yolun: {MEMORY_DIR}/users/{mem_user}/ "
                 f"(notlar: notes/, profil: profile.md). "
                 f"Ortak aile hafızası: {MEMORY_DIR}/family.md."),
            ]
    skills = REPO_ROOT / PI_SKILLS_DIR
    if skills.exists():
        args += ["--skill", str(skills)]
    # LOKAL pi extension: family-memory (memory_add/memory_search + reminder_* +
    # memory_consolidate). Sadece worker'ın pi'sinde yüklenir (global DEĞİL). Guest'te de
    # yüklenebilir — tool'lar MEM_USER boşsa kendini reddeder. Dosya yoksa graceful.
    # family-memory SADECE normal modda (dev sohbeti hafızaya yazamasın).
    if not dev:
        mem_ext = REPO_ROOT / "pi" / "extensions" / "family-memory" / "index.ts"
        if mem_ext.is_file():
            args += ["-e", str(mem_ext)]
    # mode-switch: enter_dev_mode/exit_dev_mode tool'ları. İKİ modda da yüklenir (normal →
    # enter'ı, dev → exit'i sunar). DEV_MODE_ENABLED=false → hiç yüklenmez (mekanizma kapalı).
    if DEV_MODE_ENABLED:
        ms_ext = REPO_ROOT / "pi" / "extensions" / "mode-switch" / "index.ts"
        if ms_ext.is_file():
            args += ["-e", str(ms_ext)]
    # ---- WEB ERİŞİMİ (2026-07-14: Qwant → SearXNG) ---------------------------
    # ESKİ YOL (DEVRE DIŞI, dosya duruyor): pi/extensions/websearch/index.ts = anahtarsız
    # Qwant kazıması. Qwant CAPTCHA döndürmeye başladı → `web_search` canlıda ÖLÜYDÜ.
    # Dosya SİLİNMEDİ (kullanıcı kuralı: sormadan kaldırma), sadece artık YÜKLENMİYOR.
    # Geri açmak istersen: WEB_SEARCH_LEGACY_QWANT=true (aşağıda) — ama CAPTCHA duruyor.
    #
    # YENİ YOL — iki eklenti, iki AYRI iş; ikisi de anahtarsız:
    #   1) web_search    → @oresk/pi-searxng → KENDİ SearXNG'miz (.25:8888, systemd).
    #      Hesap/anahtar yok, üçüncü taraf hesabına bağlı değil. Yapılandırma:
    #      ~/.pi/agent/pi-searxng.jsonc (repo DIŞI → docs/SEARXNG-KURULUM.md).
    #   2) fetch_content → pi-web-access → sayfayı DOĞRUDAN HTTP ile çekip Readability +
    #      Turndown ile markdown'a çevirir (sağlayıcı YOK). Aynı pakette bir `web_search`
    #      de var ama ~/.pi/web-search.json içinde "webSearch.enabled": false ile
    #      KAPATILDI → iki `web_search` tool'u ÇAKIŞMAZ.
    #
    # Neden node_modules'tan explicit `-e` yol:  pi'ya `npm:paket` deseydik HER pi
    # doğuşunda (persona swap = sık) paketi temp dizine kurmaya kalkardı = saniyeler.
    # Kurulu yolu doğrudan vermek bu maliyeti SIFIRLIYOR. `-e` explicit olduğu için
    # --no-extensions (PI_ISOLATED) bunları BOZMAZ.
    if WEB_SEARCH_LEGACY_QWANT:
        legacy_ext = REPO_ROOT / "pi" / "extensions" / "websearch" / "index.ts"
        if legacy_ext.is_file():
            args += ["-e", str(legacy_ext)]
    else:
        searxng_ext = PI_NPM_DIR / "@oresk" / "pi-searxng" / "searxng.ts"
        if searxng_ext.is_file():
            args += ["-e", str(searxng_ext)]
    # fetch_content (sayfa/PDF/GitHub/YouTube okuma). Arama yolundan bağımsız → legacy
    # modda da yüklenir. Dosya yoksa graceful: tool kaydolmaz, allowlist girişi zararsız.
    web_access_ext = PI_NPM_DIR / "pi-web-access" / "index.ts"
    if web_access_ext.is_file():
        args += ["-e", str(web_access_ext)]
    # Session dizini: dev'de pi'nın cwd'si worktree olduğu için RELATİF "sessions" worktree'ye
    # düşerdi → ANA repo'nun sessions/'ına sabitle (dev session'ı ayrı ID ile orada, normal
    # sohbete KARIŞMAZ). Normal modda cwd=REPO_ROOT olduğundan sonuç bugünküyle aynı dizin.
    session_dir = str(REPO_ROOT / PI_SESSION_DIR) if dev else PI_SESSION_DIR
    args += ["--session-dir", session_dir, "--session-id", session_id]
    return args


class PiRpcClient:
    """Kalıcı `pi --mode rpc` alt-süreci. stdin JSON-line yaz, stdout JSON-line oku.

    - `response` tipli satırlar id ile korelasyon için `_pending`'e gider.
    - Diğer tüm satırlar (AgentSessionEvent) aktif turun kuyruğuna (`_turn_q`) gider.
    """

    def __init__(
        self,
        persona: str,
        session_id: str,
        model: Optional[str] = None,
        thinking: Optional[str] = None,
        cwd: Optional[Path] = None,
        dev: bool = False,
    ):
        self._args = _build_pi_args(persona, session_id, model, thinking, dev=dev)
        # cwd: normal → REPO_ROOT (bugünkü); dev → izole worktree (kod EDIT'leri orada kalır).
        self._cwd = Path(cwd) if cwd is not None else REPO_ROOT
        self._dev = dev
        # Alt-sürece geçecek hafıza kimliği. Dev'de family-memory yüklenmez → boş (hafıza yok).
        self._mem_user = "" if dev else _mem_user(session_id)
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._pending: dict[str, asyncio.Future] = {}
        self._turn_q: Optional[asyncio.Queue] = None
        self._turn_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        # Bu SÜREÇ hiç text_delta üretti mi? False iken ilk tur "soğuk" sayılır
        # (oturum geçmişi baştan yüklenir, KV cache soğuk) → uzun stall toleransı.
        # İlk delta ile True olur ve süreç ölene kadar öyle kalır (yeni PiRpcClient =
        # yeni süreç = yeniden False; swap zaten yeni nesne kurar).
        self.warmed_up = False

    @property
    def started(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        async with self._start_lock:
            if self.started:
                return
            self._proc = await asyncio.create_subprocess_exec(
                *self._args,
                cwd=str(self._cwd),
                env={**os.environ, "MEM_USER": self._mem_user},
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._reader_task = asyncio.create_task(self._read_stdout())

    async def _read_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            while True:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "response":
                    fut = self._pending.pop(obj.get("id"), None)
                    if fut is not None and not fut.done():
                        fut.set_result(obj)
                    continue
                # AgentSessionEvent → aktif tura ilet.
                q = self._turn_q
                if q is not None:
                    q.put_nowait(obj)
        finally:
            # Süreç öldü: bekleyen istekleri ve aktif turu serbest bırak.
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(RuntimeError("pi rpc process exited"))
            self._pending.clear()
            if self._turn_q is not None:
                self._turn_q.put_nowait(None)  # sentinel → tur bitir

    def _write(self, obj: dict) -> None:
        """Drain beklemeden yaz (abort/cancel yolları için best-effort)."""
        if self._proc is None or self._proc.stdin is None:
            return
        try:
            self._proc.stdin.write((json.dumps(obj) + "\n").encode())
        except Exception:
            pass

    async def send(self, obj: dict) -> None:
        self._write(obj)
        if self._proc is not None and self._proc.stdin is not None:
            try:
                await self._proc.stdin.drain()
            except Exception:
                pass

    async def request(self, cmd: dict, timeout: float = 60.0) -> dict:
        """Bir response bekleyen komut gönder (get_state gibi)."""
        if not self.started:
            await self.start()
        req_id = cmd.get("id") or uuid.uuid4().hex
        cmd = {**cmd, "id": req_id}
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[req_id] = fut
        await self.send(cmd)
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(req_id, None)

    async def stop(self) -> None:
        if self._proc is None:
            return
        proc = self._proc
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except (asyncio.TimeoutError, ProcessLookupError):
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        if self._reader_task is not None:
            self._reader_task.cancel()
        self._proc = None


# ---------------------------------------------------------------------------
# livekit-agents LLM adaptörü (opsiyonel import — smoke test livekit gerektirmez)
# ---------------------------------------------------------------------------
try:
    from livekit.agents import llm
    from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN

    _HAS_LIVEKIT = True
except Exception:  # pragma: no cover - livekit kurulu değilse smoke test yine çalışır
    _HAS_LIVEKIT = False


def _last_user_text(chat_ctx: Any) -> str:
    """chat_ctx içindeki son 'user' mesajının metnini çıkar (sürüm-toleranslı)."""
    items = getattr(chat_ctx, "items", None)
    if items is None:
        items = getattr(chat_ctx, "messages", []) or []
    for item in reversed(list(items)):
        if getattr(item, "role", None) != "user":
            continue
        tc = getattr(item, "text_content", None)
        if isinstance(tc, str) and tc.strip():
            return tc
        content = getattr(item, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [c for c in content if isinstance(c, str)]
            if parts:
                return "\n".join(parts)
    return ""


def _assistant_msg_text(message: Any) -> str:
    """pi AssistantMessage dict'inden tüm text content'i birleştir."""
    if not isinstance(message, dict):
        return ""
    parts = [
        c.get("text", "")
        for c in message.get("content", []) or []
        if isinstance(c, dict) and c.get("type") == "text"
    ]
    return "".join(parts)


# ── `mate.tool` — Candan'ın NE YAPTIĞI (tool çağrısı + sonucu) ───────────────
# pi'nin mesaj akışındaki iki şekli okuruz (sessions/*.jsonl ile birebir aynı):
#   assistant  → content[] içinde {"type":"toolCall","id":…,"name":…,"arguments":{…}}
#   toolResult → {"toolCallId":…,"toolName":…,"content":[{"type":"text","text":…}],"isError":…}
# Çıktı şeması (web/lib/tool-events.ts ile AYNI sözleşme):
#   {"type":"tool_call",   "id","name","args","ts"}
#   {"type":"tool_result", "id","name","result","isError","ts"}
# ts = epoch MİLİSANİYE (web transkript damgalarıyla aynı birim → tek kronolojik sıra).
def _tool_events(message: Any) -> list[dict]:
    """pi mesajından yayınlanacak tool olaylarını çıkar. Tool yoksa boş liste."""
    if not isinstance(message, dict):
        return []
    now_ms = int(time.time() * 1000)
    out: list[dict] = []
    role = message.get("role")
    if role == "assistant":
        for c in message.get("content", []) or []:
            if isinstance(c, dict) and c.get("type") == "toolCall":
                out.append({
                    "type": "tool_call",
                    "id": c.get("id") or "",
                    "name": c.get("name") or "",
                    "args": c.get("arguments") or {},
                    "ts": now_ms,
                })
    elif role == "toolResult":
        text = "".join(
            c.get("text", "")
            for c in message.get("content", []) or []
            if isinstance(c, dict) and c.get("type") == "text"
        )
        ts = message.get("timestamp")
        out.append({
            "type": "tool_result",
            "id": message.get("toolCallId") or "",
            "name": message.get("toolName") or "",
            "result": text,
            "isError": bool(message.get("isError")),
            "ts": int(ts) if isinstance(ts, (int, float)) else now_ms,
        })
    # id/name'i olmayan olay web'de eşleşemez → yayınlama.
    return [e for e in out if e["id"] and e["name"]]


if _HAS_LIVEKIT:

    class PiStream(llm.LLMStream):
        """Bir chat turu: prompt gönder, text_delta'ları ChatChunk olarak stream et."""

        def __init__(self, pi_llm: "PiBrain", *, chat_ctx, tools, conn_options):
            super().__init__(
                pi_llm, chat_ctx=chat_ctx, tools=tools, conn_options=conn_options
            )
            self._brain = pi_llm
            self._client = pi_llm._client  # _run başında güncel speaker'a göre çözülür

        async def _run(self) -> None:
            # Tur başında güncel konuşmacıyı çöz; kişi değiştiyse warm süreci swap et.
            self._client = await self._brain._current_client()
            await self._client.start()
            text = _last_user_text(self._chat_ctx)
            if not text:
                return
            turn_id = uuid.uuid4().hex

            def _emit(content: str) -> None:
                self._event_ch.send_nowait(
                    llm.ChatChunk(
                        id=turn_id,
                        delta=llm.ChoiceDelta(role="assistant", content=content),
                    )
                )

            # Wake gate (DIŞ kapı). Konuşmacı çözümünden SONRA, enrollment/pi'dan
            # ÖNCE. Uyurken + wake yok → sessiz (pi'ya GİTME, token yok). Wake ile
            # uyanınca enrollment/normal akış devam eder. Kapalıysa gate yok.
            action, payload = self._brain._wake_decide(text)
            if action == "silent":
                return
            if action == "scripted":
                _emit(payload)
                return
            text = payload  # 'process' → wake ayıklanmış / uyanık metin

            # Faz 3.1: sesli oto-enrollment. Bilinmeyen ses / akış ortası →
            # scripted TR satır döndür ve pi'ya GİTME (token harcanmaz). Kapalı /
            # tanınan / akışa girmeyen → None döner, normal pi akışı sürer.
            scripted = await self._brain._enrollment_line(text)
            if scripted is not None:
                _emit(scripted)
                return
            # Rol yükseltme komutu ("X'i aileye ekle") — yetki kontrolü BURADA
            # (LLM'de değil). adult değilse reddedilir; pi'ya gitmez.
            scripted = self._brain._role_command(text)
            if scripted is not None:
                _emit(scripted)
                return

            # Sohbet sıfırlama komutu ("candan, yeni sohbet başlat") — wake gibi
            # DETERMİNİSTİK ifade eşleşmesi, pi'ya GİTMEZ. Modele tool olarak
            # bırakılmaz: ölçüldü, model bu sınıf talimatı çağırmak yerine "tamam"
            # deyip geçiyor → sıfırlama hiç olmazdı. Guest dahil herkese SERBEST
            # (guest'in zaten hafızası yok; sıfırlanan sadece sohbet geçmişi).
            # Yakın-ıska (benziyor ama emin değil) → yürütmez, SORAR: bkz. _reset_line.
            scripted = await self._brain._reset_line(text)
            if scripted is not None:
                _emit(scripted)
                return

            # Tanınan kişinin bu bağlantıdaki İLK turu → pi'ya giden mesaja
            # ismiyle-selam direktifi ekle (pi doğal selamlasın).
            text = self._brain._maybe_greet(text)
            # ZAMAN: warm pi süreci GÜNLERCE yaşar → boot'ta enjekte edilen tarih BAYATLAR.
            # Her tura güncel saati (Europe/London) iliştir. Model yine de due_at HESAPLAMAZ
            # (onu reminder_add server-side çözer); bu satır "bugün/yarın/şu an" için.
            text = self._brain._now_note() + "\n\n" + text

            q: asyncio.Queue = asyncio.Queue()

            async with self._client._turn_lock:
                self._client._turn_q = q
                aborted = False
                got_delta = False
                stalled = False       # watchdog / pi error → turu erken kapat
                final_msg: Any = None  # son assistant mesajı (fallback/hata için)
                try:
                    await self._client.send({"type": "prompt", "message": text})
                    # Watchdog: her ilerlemede (text_delta / herhangi olay) sıfırlanan
                    # inaktivite sayacı. Tolerans boyunca HİÇ olay gelmezse (WebSocket
                    # 1000 gibi ~33-40s takılma) turu temiz kapat.
                    #
                    # SOĞUK İLK TUR: taze süreç henüz delta üretmediyse tolerans
                    # PI_FIRST_TURN_STALL_TIMEOUT (uzun) — prefill 9-17s sürebilir ve bu
                    # SAĞLIKLIDIR. İlk delta gelir gelmez PI_TURN_STALL_TIMEOUT'a (kısa)
                    # düşeriz: akış başladıysa duraklama artık gerçek takılmadır.
                    cold = not self._client.warmed_up
                    compacting = False   # compaction_start..end penceresi
                    def _budget() -> float:
                        # Compaction penceresi HER ŞEYİ ezer: bu sessizlik sağlıklı,
                        # takılma değil (bkz. PI_COMPACTION_STALL_TIMEOUT).
                        if compacting:
                            return PI_COMPACTION_STALL_TIMEOUT
                        return (
                            PI_FIRST_TURN_STALL_TIMEOUT
                            if (cold and not got_delta)
                            else PI_TURN_STALL_TIMEOUT
                        )

                    # Soğuk beklemede kullanıcıyı sessiz bırakma: tek kısa cümle söyle.
                    # notice_at None → ya kapalı ya da zaten söylendi/gerek kalmadı.
                    notice_at: Optional[float] = (
                        time.monotonic() + PI_COLD_NOTICE_DELAY
                        if (cold and PI_COLD_NOTICE_DELAY > 0)
                        else None
                    )
                    last_progress = time.monotonic()
                    while True:
                        now = time.monotonic()
                        # Tolerans her turda YENİDEN hesaplanır: ilk delta geldiği anda
                        # uzun → kısa geçiş burada yürürlüğe girer.
                        stall_at = last_progress + _budget()
                        # En yakın uyanma anına kadar bekle (stall ya da bildirim).
                        wake_at = stall_at if notice_at is None else min(stall_at, notice_at)
                        try:
                            obj = await asyncio.wait_for(
                                q.get(), timeout=max(0.05, wake_at - now)
                            )
                        except asyncio.TimeoutError:
                            if notice_at is not None and time.monotonic() >= notice_at:
                                # Soğuk yükleme uzuyor → TEK cümle, sonra beklemeye devam.
                                # got_delta'yı DEĞİŞTİRMEZ: bu pi'nın cevabı değil, bizim
                                # ara sözümüz; watchdog hâlâ uzun toleransta kalmalı.
                                notice_at = None
                                logger.info(
                                    "pi soğuk yükleme %.0fs+ → kullanıcıya ara söz",
                                    PI_COLD_NOTICE_DELAY,
                                )
                                _emit(PI_COLD_NOTICE_TEXT)
                                continue
                            logger.warning(
                                "pi tur stall: %.0fs ilerleme yok → tur kapatılıyor "
                                "(got_delta=%s cold=%s)", _budget(), got_delta, cold,
                            )
                            stalled = True
                            break
                        # İlerleme var → inaktivite sayacını sıfırla.
                        last_progress = time.monotonic()
                        if obj is None:  # süreç öldü
                            break
                        etype = obj.get("type")
                        # Tool çağrısı/sonucu → odaya yayınla (`mate.tool`). SADECE mesaj
                        # KESİNLEŞİNCE (message_end/turn_end): message_update akışında
                        # toolCall.arguments PARÇA PARÇA dolar (önce {}, sonra {"text":"Ç"}…).
                        # Erken yayınlarsak dedupe "ilk gelen kazanır" mantığıyla dolu hâli
                        # eler → kartta argümanlar boş görünürdü. Hem assistant (toolCall) hem
                        # toolResult mesajları message_end ile TAM gelir. Aynı olay
                        # message_end + turn_end ile iki kez gelebilir → id ile elenir;
                        # yayın hatası turu BOZMAZ (best-effort).
                        if etype in ("message_end", "turn_end"):
                            self._brain._publish_tool_msg(obj.get("message"))
                        # Dev tool sinyali (enter_dev_mode/exit_dev_mode) → mod isteği.
                        # Swap bu tur BİTİNCE (sonraki tur başında) uygulanır: komutu söyleyen
                        # pi cevabını ("geçiyorum") temiz verir, sonra süreç swap olur.
                        self._brain._detect_mode_signal(obj.get("message"))
                        if etype == "message_update":
                            ame = obj.get("assistantMessageEvent") or {}
                            if ame.get("type") == "text_delta":
                                delta = ame.get("delta") or ""
                                if delta:
                                    if not got_delta:
                                        # İlk delta: süreç ısındı (KV cache dolu) →
                                        # sonraki turlar kısa toleransla başlar. Bekleyen
                                        # ara söz varsa iptal (cevap zaten akmaya başladı).
                                        got_delta = True
                                        notice_at = None
                                        self._client.warmed_up = True
                                    _emit(delta)
                        elif etype in ("message_end", "turn_end"):
                            msg = obj.get("message")
                            if isinstance(msg, dict) and msg.get("role") == "assistant":
                                final_msg = msg
                                if msg.get("stopReason") == "error":
                                    logger.warning(
                                        "pi assistant error: %s",
                                        msg.get("errorMessage") or "(bilinmiyor)",
                                    )
                                    # WebSocket 1000 vb. → agent_settled'ı bekleme
                                    # (33s takılabilir); turu hemen kapat/fallback ver.
                                    stalled = True
                                    break
                        elif etype == "compaction_start":
                            # Bağlam doldu → pi geçmişi özetliyor. Pencere boyunca
                            # HİÇ olay gelmez → watchdog'u uzun toleransa al.
                            compacting = True
                            reason = obj.get("reason") or "?"
                            # Kullanıcı bu turda cevabın bir kısmını DUYDUYSA (got_delta)
                            # sıkıştırma onun için görünmez (cevap bitti, tur sonu işi) →
                            # SUSMAK doğru. Hiç duymadıysa cevabını bekliyor demektir →
                            # sessiz bırakma. Bekleyen soğuk-yükleme ara sözünü de iptal
                            # et: iki ara söz üst üste söylenmesin.
                            logger.info(
                                "pi compaction başladı (reason=%s got_delta=%s) → %s",
                                reason, got_delta,
                                "sessiz (cevap zaten akmıştı)" if got_delta else "ara söz",
                            )
                            if not got_delta:
                                notice_at = None
                                _emit(PI_COMPACTION_NOTICE_TEXT)
                        elif etype == "compaction_end":
                            compacting = False
                            logger.info(
                                "pi compaction bitti (aborted=%s willRetry=%s)",
                                obj.get("aborted"), obj.get("willRetry"),
                            )
                        elif etype == "agent_settled":
                            break
                    # Fallback: hiç delta gelmediyse ama tam-content varsa onu stream et.
                    if not got_delta:
                        full = _assistant_msg_text(final_msg)
                        if full:
                            _emit(full)
                        elif stalled:
                            # Hiç metin yok + stall/error → kullanıcı sessiz kalmasın.
                            if final_msg is not None and final_msg.get("stopReason") == "error":
                                logger.warning(
                                    "pi boş yanıt (error): %s",
                                    final_msg.get("errorMessage") or "(bilinmiyor)",
                                )
                            _emit("Bir saniye, tekrar dener misin?")
                        elif final_msg is not None and final_msg.get("stopReason") == "error":
                            logger.warning(
                                "pi boş yanıt (error): %s",
                                final_msg.get("errorMessage") or "(bilinmiyor)",
                            )
                    # Stall'da pi hâlâ arka planda çalışıyor olabilir → abort ile durdur.
                    if stalled:
                        self._client._write({"type": "abort"})
                except asyncio.CancelledError:
                    # Barge-in / interrupt: pi'ya abort gönder.
                    aborted = True
                    self._client._write({"type": "abort"})
                    raise
                finally:
                    if aborted:
                        self._client._write({"type": "abort"})
                    self._client._turn_q = None

    class PiBrain(llm.LLM):
        """livekit-agents LLM: warm `pi --mode rpc` beyni. openai.LLM(...) yerine geçer."""

        def __init__(
            self,
            *,
            persona: str = PI_DEFAULT_PERSONA,
            session_id: Optional[str] = None,
            speaker_state: Any = None,
            speaker_id: Any = None,
            speaker_store: Any = None,
            brain: Optional[str] = None,
        ):
            super().__init__()
            # _default_persona TAHMİNDEN ETKİLENMEZ: tanınmayan/guest konuşmacının
            # hedefi (bkz. _target) hep bu kalmalı. Tahmin sadece hangi süreci
            # ISITTIĞIMIZI değiştirir, kimin kim olduğunu DEĞİL.
            self._default_persona = persona
            self._persona = persona
            self._session_id = session_id or persona
            # PREWARM TAHMİNİ: son bilinen konuşmacıyı ısıt (bkz. read_last_speaker).
            # SADECE speaker-ID açıkken (speaker_state) ve çağıran açıkça bir session_id
            # dayatmadıysa. speaker_state None iken _current_client swap YAPMAZ → yanlış
            # tahmin oturum boyunca YAPIŞIR; o yüzden guard şart.
            # _last_noted: işaret dosyasına EN SON yazılan slug (tekrar yazımı eler).
            # Tahmin tuttuysa dosya zaten o değerde → gereksiz yazım hiç olmaz.
            self._last_noted = ""
            if speaker_state is not None and session_id is None:
                # speaker_id PARAMETREDEN geçer: self._speaker_id bu noktada henüz
                # atanmadı (aşağıda) — self'ten okumak sessizce None tahmin üretirdi.
                guess = self._prewarm_guess(speaker_id)
                if guess:
                    self._persona, self._session_id = guess
                    self._last_noted = guess[1]
            # Beyin seçimi (oturum başı; web dispatch metadata'sı → agent.py). Geçersiz/
            # yoksa .env varsayılanına düşer. Konuşmacı değişiminde (pi swap) de KORUNUR.
            self._model, self._thinking = resolve_brain(brain)
            logger.info(
                "beyin: %s → model=%s thinking=%s",
                (brain or "").strip().lower() or "varsayılan (worker/.env)",
                self._model, self._thinking or "default",
            )
            # speaker_state: `.current` alanı olan paylaşılan durum (None = kapalı).
            # Kapalıyken davranış Faz 2 ile AYNI: tek persona, tek warm süreç.
            self._speaker_state = speaker_state
            self._client = PiRpcClient(
                self._persona, self._session_id, self._model, self._thinking
            )
            self._swap_lock = asyncio.Lock()
            # SESLE GELİŞTİRME modu durumu. _mode: aktif mod ("normal"|"dev"). _pending_mode:
            # dev tool sinyali (enter/exit) ile istenen mod; bir sonraki tur başında uygulanır
            # (mevcut tur, komutu söyleyen pi'da temiz biter). _saved_normal: dev'e geçerken
            # normal (persona, session) yedeği → çıkışta bire bir geri dönmek için.
            self._mode = "normal"
            self._pending_mode: Optional[str] = None
            self._saved_normal: Optional[tuple[str, str]] = None
            # `mate.tool` yayıncısı (agent.py bağlar; None → yayın YOK, eski davranış).
            # Tool çağrısı/sonucu olayları buradan odaya gider; hata konuşmayı BOZMAZ.
            self._tool_publisher: Optional[Callable[[dict], None]] = None
            self._tool_seen: set[str] = set()   # aynı olay iki kez yayınlanmasın
            # Faz 3.1: sesli oto-enrollment bağımlılıkları. Üçü de varsa etkin;
            # yoksa (SPEAKER_ID_ENABLED kapalı vb.) enrollment TAMAMEN devre dışı.
            self._speaker_id = speaker_id
            self._speaker_store = speaker_store
            self._enroll_ok = bool(speaker_state and speaker_id and speaker_store)
            # Enrollment state machine (bağlantı ömrü boyunca yaşar).
            # verify_existing: ses mevcut bir kişiye "belirsiz bant"ta benziyor →
            # "Sen X misin?" diye sorup onayı bekliyoruz (kimlik bölünmesi koruması).
            self._enroll_stage: Optional[str] = None      # None|"ask_name"|"confirm"|"verify_existing"
            self._enroll_name: Optional[str] = None
            self._enroll_emb: Any = None                  # tetikleyen sözün embed'i
            self._enroll_name_emb: Any = None             # ismi söylerkenki embed
            self._enroll_retried = 0                      # isim kaç kez tekrar soruldu
            self._enroll_match: Optional[str] = None      # sese benzeyen mevcut kişi
            self._onboarding_asked = False                # bu bağlantıda soruldu mu
            self._greeted: set[str] = set()               # ismiyle selamlanan kişiler
            self._enroll_lock = asyncio.Lock()
            # Sıfırlama yakın-ıska onayı bekleniyor mu (TEK tur yaşar; bkz. _reset_line).
            self._reset_pending = False
            # Wake word gate (konuşma penceresi). Kapalıysa gate yok (mevcut davranış).
            self._wake = WakeGate()
            self._wake_task: Optional[asyncio.Task] = None
            # Konsolidasyon: dosya başına son çalıştırma (günde en çok 1 → LLM turu yakma).
            self._consolidated: dict[str, float] = {}

        # ── `mate.tool` yayını (Candan ne yapıyor) ───────────────────────────
        def set_tool_publisher(self, cb: Optional[Callable[[dict], None]]) -> None:
            """Tool olaylarını odaya basacak callback'i bağla (agent.py kurar; sync,
            içinde task açar). None → yayın yok (eski davranış)."""
            self._tool_publisher = cb

        def _publish_tool_msg(self, message: Any) -> None:
            """pi mesajındaki tool çağrısı/sonucunu web'e yayınla — BEST-EFFORT.

            Hiçbir koşulda turu bozmaz: yayıncı yoksa/patlarsa sessizce (en fazla warning)
            geçilir. Aynı olay iki kez gelebilir (message_end + turn_end aynı mesajı
            taşıyabilir) → id ile elenir.

            DİKKAT: yalnızca KESİNLEŞMİŞ mesajla çağır (message_end/turn_end). message_update
            akışındaki mesaj yarımdır (toolCall.arguments henüz dolmamış olabilir) ve
            "ilk gelen kazanır" dedupe'u yüzünden boş argüman kalıcı olur."""
            cb = self._tool_publisher
            if cb is None:
                return
            try:
                events = _tool_events(message)
            except Exception:  # noqa: BLE001 — ayrıştırma hatası konuşmayı BOZMAZ
                logger.warning("mate.tool: olay ayrıştırılamadı", exc_info=True)
                return
            for ev in events:
                key = f"{ev['type']}:{ev['id']}"
                if key in self._tool_seen:
                    continue
                if len(self._tool_seen) > 500:   # uzun oturumda sınırsız büyümesin
                    self._tool_seen.clear()
                self._tool_seen.add(key)
                try:
                    cb(ev)
                except Exception:  # noqa: BLE001 — yayın hatası konuşmayı BOZMAZ
                    logger.warning("mate.tool yayını başarısız (%s)", ev["name"], exc_info=True)

        # ── Wake word gate (konuşma penceresi) ───────────────────────────────
        def set_wake_change(self, cb: Optional[Callable[[bool], None]]) -> None:
            """Uyku↔uyanık geçişinde çağrılacak callback'i bağla (entrypoint kullanır).
            cb(True)=uyandı, cb(False)=uyudu. Yalnızca wake açıkken anlamlı."""
            self._wake.on_change = cb

        def _wake_decide(self, text: str) -> tuple[str, Optional[str]]:
            """Gate kararı + arka plan uyku zamanlayıcısını (ilk çağrıda) başlat."""
            self._ensure_wake_timer()
            return self._wake.decide(text)

        def wake_now(self, text: str = "") -> bool:
            """Erken uyandırma kancası (agent user_input_transcribed'den). enabled +
            transcript'te wake word varsa PiBrain turu işlenmeden ÖNCE uyan → on_change
            (candan.awake=true) → çan HEMEN. Idempotent (zaten uyanıksa çift çan yok).
            Wake yok / kapalı → no-op. Yeni uyandıysa True. `wake_match` yeniden kullanılır
            (izole yanlış-transkripsiyonlarda da erken çan)."""
            matched, _rem = wake_match(text, self._wake.wake_norm, self._wake.wake_variants)
            if not self._wake.enabled or not matched:
                return False
            self._ensure_wake_timer()
            return self._wake.wake_now()

        def wake_touch(self) -> None:
            """Kullanıcı aktivitesi (STT partial/final) → uyku sayacını tazele.
            Uyandırmaz; sadece 'son konuşma anı'nı ileri taşır."""
            if self._wake.enabled:
                self._wake.touch()

        def wake_user_speaking(self, speaking: bool) -> None:
            """VAD kancası (agent.py user_state_changed). Kullanıcı konuşurken sayaç
            durur; bitince 15sn TAM o andan başlar."""
            if self._wake.enabled:
                self._wake.set_user_speaking(speaking)

        def wake_agent_busy(self, busy: bool) -> None:
            """Asistan cevabı kancası (agent.py agent_state_changed: thinking/speaking).
            Cevap sürerken uyunmaz; cevap bitince sayaç yeniden başlar."""
            if self._wake.enabled:
                self._wake.set_agent_busy(busy)

        def _ensure_wake_timer(self) -> None:
            if self._wake.enabled and self._wake_task is None:
                try:
                    self._wake_task = asyncio.create_task(self._wake_sleep_loop())
                except RuntimeError:  # loop yok (test) → zamanlayıcısız çalış
                    pass

        async def _wake_sleep_loop(self) -> None:
            """Son etkileşimden WAKE_WINDOW_SECONDS geçince awake=False (uyu)."""
            try:
                while True:
                    await asyncio.sleep(1.0)
                    if self._wake.expire():
                        logger.info("wake: %.0fs sessizlik → uyku", self._wake.window)
            except asyncio.CancelledError:
                pass

        # ── Proaktif ajan kancaları (worker/reminders.py bunları kullanır) ────
        def proactive_wake(self) -> bool:
            """Konuşma penceresini KOŞULSUZ aç (Candan KENDİ seslendi → konuşma başladı).

            `wake_now(text)` KULLANILAMAZ: o, metinde wake word ARAR (kullanıcının bizi
            çağırdığı yol) — boş metinle çağrılınca sessizce no-op'tur. Biz seslendiğimizde
            kullanıcıdan "candan" demesini beklemek yanlış: cevabı ('efendim') normal akışta
            alınabilmeli. Yeni uyandıysa True (cevapsız kalırsa geri uyutmak için)."""
            if not self._wake.enabled:
                return False
            self._ensure_wake_timer()
            return self._wake.wake_now()

        def proactive_sleep(self) -> bool:
            """Seslendik, cevap YOK → uyandırmayı geri al (pencere kapansın)."""
            return self._wake.sleep_now()

        def proactive_hold(self, v: bool) -> None:
            """Proaktif seslenme sürerken kullanıcının onay sözü ('efendim') pi'ya
            GİTMESİN — hatırlatmayı BİZ iletiyoruz; pi ayrıca cevap verirse çift konuşma
            olur. Deterministik kapı (yarış yok)."""
            self._wake.hold = bool(v)

        def busy(self) -> bool:
            """Kullanıcı konuşuyor ya da asistan cevap veriyor → proaktif seslenme ERTELE."""
            return self._wake.busy()

        def current_user(self) -> str:
            """Hafıza kimliği (guest/unknown → ''). Olaylar bu kullanıcıya ait."""
            return _mem_user(self._session_id)

        def display_name(self, user: str = "") -> str:
            """Sesli seslenmede kullanılacak ad ('ayhan' → 'Ayhan')."""
            name = getattr(self._speaker_state, "current", None) if self._speaker_state else None
            return (name or (user or self._session_id) or "").strip().capitalize()

        def _now_note(self) -> str:
            """Modele HER TURDA verilen güncel saat satırı. Warm süreç günlerce yaşadığı
            için boot'ta enjekte edilen tarih bayatlar; bu satır taze kalır."""
            try:
                from zoneinfo import ZoneInfo

                now = datetime.now(ZoneInfo(CANDAN_TZ))
            except Exception:  # noqa: BLE001 — tz verisi yoksa yerel saat
                now = datetime.now()
            days = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar")
            return (f"(Sistem: şu an {now:%d.%m.%Y} {days[now.weekday()]}, saat {now:%H:%M} "
                    f"[{CANDAN_TZ}].)")

        # ── Sessiz pi turu (TTS'e GİTMEZ; AgentSession'ı hiç görmez) ──────────
        async def _silent_turn(self, prompt: str, timeout: float = 30.0) -> bool:
            """pi'ya arka planda bir prompt gönder ve tur bitene kadar bekle. Çıktı sesli
            OKUNMAZ (LLMStream değil, doğrudan RPC). Hata/timeout → False (akış bloklanmaz)."""
            client = self._client
            if client is None or not client.started or not client._mem_user:
                return False
            q: asyncio.Queue = asyncio.Queue()
            try:
                async with client._turn_lock:
                    client._turn_q = q
                    try:
                        await client.send({"type": "prompt", "message": prompt})

                        async def _drain() -> None:
                            while True:
                                obj = await q.get()
                                if obj is None or obj.get("type") == "agent_settled":
                                    break

                        await asyncio.wait_for(_drain(), timeout=timeout)
                        return True
                    except Exception as e:  # noqa: BLE001 — arka plan turu akışı bloklamaz
                        logger.info("sessiz tur atlandı/timeout: %r", e)
                        client._write({"type": "abort"})
                        return False
                    finally:
                        client._turn_q = None
            except Exception:  # noqa: BLE001
                return False

        # ── PARÇA B: konsolidasyon (bağlam şişmesi) ───────────────────────────
        def _context_files(self, user: str) -> list[tuple[str, Path]]:
            mem = REPO_ROOT / MEMORY_DIR
            return [("profile", mem / "users" / user / "profile.md"),
                    ("family", mem / "family.md")]

        async def consolidate_if_needed(self, now: Optional[float] = None) -> Optional[str]:
            """profile.md / family.md HER TURDA bağlama enjekte ediliyor (ölçüm: ~2.4 ms/KB).
            2 KB'ı aşan dosya varsa pi'ya SESSİZ bir tur açıp memory_consolidate çağırtır:
            kalıcı gerçekler dosyada kalır, olaylar notes/'a iner (kayıp yok).

            Ne zaman: yalnız kullanıcı SESSİZKEN (busy DEĞİL + uyku gate'i uyanık değil) —
            konuşmayı bölmez. Dosya başına günde 1 (gereksiz LLM turu yakma).
            Boyutlar LOGLANIR (önce/sonra) → büyüme hızı ölçülebilsin."""
            user = self.current_user()
            if not user:
                return None
            if self._wake.busy():
                return None                      # konuşma sürüyor → ASLA
            if self._wake.enabled and self._wake.awake:
                return None                      # konuşma penceresi açık → bekle (gece/sessizlik)
            now = time.time() if now is None else now
            for which, path in self._context_files(user):
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if size <= MEM_CONTEXT_LIMIT:
                    continue
                if now - self._consolidated.get(which, 0.0) < CONSOLIDATE_COOLDOWN:
                    continue
                self._consolidated[which] = now
                try:
                    content = path.read_text()
                except OSError:
                    continue
                logger.info("konsolidasyon: %s %d bayt > %d sınır → pi turu açılıyor",
                            which, size, MEM_CONTEXT_LIMIT)
                prompt = (
                    f"[sistem] Bağlam dosyan '{which}' {size} bayt; sert sınır "
                    f"{MEM_CONTEXT_LIMIT} bayt. Bu dosya HER TURDA bağlamına yükleniyor, "
                    f"şişmesi gecikme demek. memory_consolidate tool'unu çağır: "
                    f"file='{which}', text=<sınırın ALTINDA yeni özet>, demoted=<özetten "
                    f"çıkardığın satırlar>. KALICI gerçekleri (kim, nerede, aile, kalıcı "
                    f"tercihler) KORU; tarihli/olay içeriğini demoted ile notes'a indir — "
                    f"hiçbir şey kaybolmasın. Sesli yanıt verme, sadece tool'u çağır.\n\n"
                    f"--- {which} (mevcut içerik) ---\n{content}"
                )
                await self._silent_turn(prompt, timeout=90.0)
                try:
                    after = path.stat().st_size
                except OSError:
                    after = 0
                logger.info("konsolidasyon bitti: %s %d → %d bayt (sınır %d)",
                            which, size, after, MEM_CONTEXT_LIMIT)
                return which
            return None

        # ── Faz 3.1: sesli oto-enrollment state machine ──────────────────────
        def _reset_enroll(self) -> None:
            self._enroll_stage = None
            self._enroll_name = None
            self._enroll_emb = None
            self._enroll_name_emb = None
            self._enroll_retried = 0
            self._enroll_match = None

        async def _enrollment_line(self, text: str) -> Optional[str]:
            """Enrollment kararı. Scripted TR satır döndürürse pi ATLANIR; None →
            normal pi akışı. Kapalıysa hep None (Faz 2 davranışı)."""
            if not self._enroll_ok:
                return None
            async with self._enroll_lock:
                if self._enroll_stage is not None:
                    return await self._continue_enrollment(text)
                # Tetik: bilinmeyen ses (current None) + birikmiş embedding +
                # bu bağlantıda henüz sorulmadı.
                current = getattr(self._speaker_state, "current", None)
                emb = getattr(self._speaker_state, "last_embedding", None)
                if current is None and emb is not None and not self._onboarding_asked:
                    self._onboarding_asked = True
                    self._enroll_stage = "ask_name"
                    self._enroll_emb = emb
                    logger.info("enrollment: bilinmeyen ses → isim soruluyor")
                    return "Seni tanıyamadım, adını söyler misin?"
                return None

        async def _continue_enrollment(self, text: str) -> Optional[str]:
            """ask_name → confirm → (verify_existing) → finish akışı.
            _enroll_lock altında çağrılır."""
            if self._enroll_stage == "verify_existing":
                match = self._enroll_match or ""
                if is_affirmative_reply(text):
                    # Aynı kişi: yeni kimlik AÇMA, mevcut kişiye örnek ekle.
                    return await self._merge_into(match)
                logger.info("enrollment: %r değilmiş → yeni kişi açılıyor", match)
                return await self._enroll_new(self._enroll_name or "")
            if self._enroll_stage == "confirm":
                if is_affirmative_reply(text):
                    return await self._finish_enrollment()
                self._reset_enroll()
                logger.info("enrollment: onaylanmadı (%r) → iptal", text[:40])
                return "Tamam, kaydetmedim."
            # ask_name aşaması
            if _is_decline_enroll(text):
                self._reset_enroll()
                logger.info("enrollment: reddedildi (%r) → sessiz guest", text[:40])
                return "Peki, gerek yok."
            name = parse_spoken_name(text)
            if not name:
                # Başarısız METNİ logla — canlıda evin annesi kaydedilemedi ve
                # log yalnız "anlaşılamadı" yazdığı için ne dediğini transkriptten
                # çıkarmak zorunda kaldık. Bir daha kör kalmayalım.
                if self._enroll_retried < 2:
                    self._enroll_retried += 1
                    logger.info("enrollment: isim anlaşılamadı (%d. kez): %r",
                                self._enroll_retried, text[:60])
                    # 2. deneme daha DAR bir soru sorar: kullanıcı ilk seferde
                    # genelde adını bir cümlenin içinde söylüyor (canlı hata:
                    # "Havi adım. Az önce kocam sana söyledi...").
                    return ("Adını anlayamadım, tekrar söyler misin?"
                            if self._enroll_retried == 1
                            else "Sadece adını söyler misin?")
                # Üçüncü kez de anlaşılmadı → vazgeç, sözü normal akışa bırak.
                # Sessizce guest'e düşme: kullanıcı kaydolduğunu sanıyordu.
                self._reset_enroll()
                logger.info("enrollment: isim anlaşılamadı (3. kez) → guest: %r",
                            text[:60])
                return "Adını anlayamadım, seni kaydedemedim. İstersen sonra 'beni kaydet' de."
            # En güncel ham embedding'i (ismi söylerkenki) örnek olarak sakla.
            self._enroll_name = name
            self._enroll_name_emb = getattr(self._speaker_state, "last_embedding", None)
            self._enroll_stage = "confirm"
            return f"Seni {name} olarak kaydedeyim mi?"

        def _best_existing(self) -> tuple[Optional[str], float]:
            """Enroll embedding'lerini MEVCUT tüm centroid'lere karşı ölç; en yüksek
            skoru döndür (eşik/marj uygulanmaz). Kimse/emb yoksa (None, 0.0)."""
            best_name, best_score = None, 0.0
            for emb in (self._enroll_name_emb, self._enroll_emb):
                if emb is None:
                    continue
                try:
                    name, score = self._speaker_id.best_match(emb)
                except Exception as e:  # noqa: BLE001
                    logger.debug("best_match hata: %s", e)
                    continue
                if name and score > best_score:
                    best_name, best_score = name, score
            return best_name, best_score

        async def _finish_enrollment(self) -> str:
            """Onay alındı. YENİ KİMLİK AÇMADAN ÖNCE ses-benzerlik koruması:
              skor >= threshold → zaten kayıtlı kişi, sessizce ona örnek ekle
              merge_low <= skor < threshold → belirsiz → "Sen X misin?" diye sor
              skor < merge_low → gerçekten yeni kişi → normal enroll
            (Aynı kişinin iki kimliğe bölünmesini engeller.)"""
            from speaker_id import name_key

            name = self._enroll_name or ""
            match, score = self._best_existing()
            if match and name_key(match) != name_key(name):
                thr = float(getattr(self._speaker_id, "threshold", 0.45))
                low = float(getattr(self._speaker_id, "merge_low", 0.35))
                if score >= thr:
                    logger.info(
                        "enrollment: ses zaten %r'a ait gibi (skor=%.3f >= %.2f) → yeni kişi AÇILMIYOR",
                        match, score, thr,
                    )
                    return await self._merge_into(match)
                if score >= low:
                    logger.info(
                        "enrollment: belirsiz bant (%r skor=%.3f, %.2f–%.2f) → onay soruluyor",
                        match, score, low, thr,
                    )
                    self._enroll_match = match
                    self._enroll_stage = "verify_existing"
                    return f"Sen {match} misin?"
                logger.info(
                    "enrollment: en yakın %r skor=%.3f < %.2f → gerçekten yeni kişi",
                    match, score, low,
                )
            return await self._enroll_new(name)

        async def _store_samples(self, sid: int, source: str) -> None:
            from speaker_id import emb_to_bytes

            mid, dim = self._speaker_id.model_id, self._speaker_id.dim
            for emb in (self._enroll_emb, self._enroll_name_emb):
                if emb is not None:
                    await self._speaker_store.add_speaker_sample(
                        sid, emb_to_bytes(emb), dim, mid, source=source
                    )
            # Değişiklik hemen etkili olsun: centroid'leri DB'den yeniden kur.
            self._speaker_id.reload(await self._speaker_store.all_speaker_embeddings())

        async def _enroll_new(self, name: str) -> str:
            """Kişi oluştur (isim eşleşiyorsa mevcut kaydı kullanır) + örnek yaz + swap.
            Kimlik açılırken policy.json'a da ROL yazılır: ilk kişi → adult (ev sahibi),
            sonrakiler → guest. (Aksi hâlde yeni kişi guest kalır → hafızası olmaz.)"""
            role = "guest"
            try:
                rec = await self._speaker_store.create_speaker(name)
                sid = rec["id"]
                await self._store_samples(sid, "voice-enroll")
                # Bu bağlantıda konuşmacı artık bu kişi (sonraki tur persona swap eder).
                self._speaker_state.current = rec.get("name") or name
                self._greeted.add(self._speaker_state.current)  # kimliği onayladık
                # policy anahtarı = _slug(isim) = session_id = MEM_USER = users/<user>/
                role = _policy_set(_slug(self._speaker_state.current)) or "guest"
                logger.info("enrollment: %r kaydedildi (id=%s, rol=%s)", name, sid, role)
            except Exception as e:  # noqa: BLE001
                logger.warning("enrollment başarısız (%s)", e)
                self._reset_enroll()
                return "Şu anda seni kaydedemedim, sonra tekrar deneyelim."
            self._reset_enroll()
            if role == "guest":
                return (f"Memnun oldum {name}! Seni misafir olarak kaydettim; "
                        f"ailenin hafızasına erişemem. Evin yetişkini istersen "
                        f"seni aileye ekleyebilir.")
            return f"Memnun oldum {name}!"

        # ── Rol yükseltme (sözle, SADECE adult) ──────────────────────────────
        def _known_name(self, tok: str) -> Optional[str]:
            """Söylenen ismi kayıtlı kişilerle eşle (ek düşürerek: 'ayhanı' → 'ayhan')."""
            names = []
            if self._speaker_id is not None:
                try:
                    names = self._speaker_id.names()
                except Exception:  # noqa: BLE001
                    names = []
            known = {_slug(n): n for n in names}
            cand = _slug(tok)
            if cand in known:
                return known[cand]
            for suf in ("yi", "yı", "yu", "yü", "nu", "nü", "i", "ı", "u", "ü"):
                if cand.endswith(suf) and cand[: -len(suf)] in known:
                    return known[cand[: -len(suf)]]
            return None

        def _role_command(self, text: str) -> Optional[str]:
            """"X'i yetişkin yap" / "X'i aileye ekle" → policy.json'da X = adult.

            GÜVENLİK SINIRI: yetki, LLM'e değil BU koda ait. Aktör = speaker-ID ile
            çözülen konuşmacı; rolü policy.json'dan okunur. adult DEĞİLSE yazma yoluna
            HİÇ girilmez → guest kendini (ya da başkasını) yükseltemez. Komut değilse
            None (normal pi akışı)."""
            tok = parse_promote(text)
            if not tok:
                return None
            speaker = getattr(self._speaker_state, "current", None) if self._speaker_state else None
            actor = _slug(speaker or "")
            arole = _role(actor)
            if arole != "adult":
                logger.info("rol yükseltme REDDEDİLDİ: aktör=%r rol=%s", actor, arole)
                return "Bunu ancak evin yetişkini yapabilir."
            target = self._known_name(tok) or tok
            slug = _slug(target)
            if not slug:
                return None
            if _role(slug) == "adult":
                return f"{target} zaten aile üyesi."
            if _policy_set(slug, "adult") is None:
                return "Şu anda yapamadım, sonra tekrar deneyelim."
            logger.info("rol yükseltme: %r → adult (aktör=%r)", slug, actor)
            return f"Tamam, {target} artık aile üyesi. Hafızaya erişebilir."

        async def _merge_into(self, match: str) -> str:
            """Ses mevcut kişiye ait → YENİ kişi açma; örnekleri o kişiye ekle
            (centroid güçlenir, hafıza bölünmez)."""
            try:
                sid = self._speaker_id.id_for(match)
                if sid is None:
                    rec = await self._speaker_store.create_speaker(match)
                    sid = rec["id"]
                await self._store_samples(sid, "voice-enroll-merge")
                self._speaker_state.current = match
                self._greeted.add(match)
                logger.info("enrollment: örnekler mevcut kişi %r'a eklendi (id=%s)", match, sid)
            except Exception as e:  # noqa: BLE001
                logger.warning("enrollment merge başarısız (%s)", e)
                self._reset_enroll()
                return "Şu anda seni kaydedemedim, sonra tekrar deneyelim."
            self._reset_enroll()
            return f"Tamam {match}, sesini daha iyi tanıyacağım artık."

        def _maybe_greet(self, text: str) -> str:
            """Tanınan kişinin bu bağlantıdaki İLK turunda pi'ya ismiyle-selam
            direktifi ekle. Kapalı / bilinmeyen / zaten selamlandı → değişmez."""
            if not self._speaker_state:
                return text
            name = getattr(self._speaker_state, "current", None)
            if not name or name in self._greeted:
                return text
            self._greeted.add(name)
            h = time.localtime().tm_hour
            part = ("sabah" if 5 <= h < 12 else "öğleden sonra" if 12 <= h < 18
                    else "akşam" if 18 <= h < 22 else "gece")
            note = (
                f"(Sistem notu: {name} az önce bağlandı (~{h:02d}:00, {part}); bu, bu "
                f"oturumdaki ilk mesajı. Yanıtlamadan önce ona ismiyle KISA ve doğal bir "
                f"selam ver, sonra mesajını yanıtla.)"
            )
            return note + "\n\n" + text

        def _target(self) -> tuple[str, str]:
            """Güncel konuşmacıya göre (persona, session_id). Tanınan isim →
            persona `<isim>.md` (yoksa default) + kişiye-özel session `<isim>`.
            Unknown/kapalı → default persona + default session."""
            name = getattr(self._speaker_state, "current", None) if self._speaker_state else None
            return self._target_for(name)

        def _target_for(self, name: Optional[str]) -> tuple[str, str]:
            """İsimden (persona, session_id). _target ile prewarm tahmini AYNI kuralı
            paylaşsın diye ayrıldı → ısıttığımız süreç ile turda hedeflenen süreç
            birebir aynı isimleri üretir (yoksa tahmin hep ıskalar)."""
            slug = _slug(name) if name else ""
            if not slug:
                return self._default_persona, self._default_persona
            persona = slug if _persona_exists(slug) else self._default_persona
            return persona, slug  # session hep kişiye özel (memory ayrışsın)

        def _prewarm_guess(self, speaker_id: Any = None) -> Optional[tuple[str, str]]:
            """Isıtılacak (persona, session_id) tahmini; tahmin yoksa None (varsayılan).

            Sıra: (1) işaret dosyası — son TANINAN konuşmacı; (2) dosya yoksa ve
            kayıtlı TEK kişi varsa o (ev senaryosunda en olası konuşan odur, ilk
            oturumda da tutar). Birden çok kişi + işaret yok → tahmin YOK, varsayılan.
            Best-effort: hata → None (davranış bugünküyle aynı)."""
            try:
                slug = read_last_speaker()
                src = "işaret dosyası"
                if not slug and speaker_id is not None:
                    names = speaker_id.names() or []
                    if len(names) == 1:
                        slug, src = _slug(names[0]), "tek kayıtlı kişi"
                if not slug:
                    return None
                target = self._target_for(slug)
                logger.info("prewarm tahmini: %s/%s (%s)", target[0], target[1], src)
                return target
            except Exception:  # noqa: BLE001 — tahmin ASLA başlatmayı bozmasın
                logger.debug("prewarm tahmini başarısız", exc_info=True)
                return None

        def _note_last_speaker(self, name: Optional[str]) -> None:
            """Tanınan konuşmacıyı bir sonraki oturumun prewarm'ı için işaretle.
            Sadece DEĞİŞİNCE yazar (her tur disk'e vurmasın) ve sadece GERÇEK kişi
            için (tanınmayan/guest → varsayılana düşmeli, işaret bozulmasın)."""
            slug = _slug(name) if name else ""
            if not slug or slug == self._last_noted:
                return
            self._last_noted = slug
            write_last_speaker(slug)

        def _detect_mode_signal(self, message: Any) -> None:
            """pi mesajındaki enter_dev_mode/exit_dev_mode toolCall'ını yakala → mod isteği.
            Best-effort; ayrıştırma hatası turu BOZMAZ."""
            if not DEV_MODE_ENABLED or not isinstance(message, dict):
                return
            if message.get("role") != "assistant":
                return
            for c in message.get("content", []) or []:
                if isinstance(c, dict) and c.get("type") == "toolCall":
                    name = c.get("name") or ""
                    if name == "enter_dev_mode":
                        self.request_mode("dev")
                    elif name == "exit_dev_mode":
                        self.request_mode("normal")

        def request_mode(self, mode: str) -> None:
            """Dev tool sinyalini kaydet ("dev" | "normal"). Event akışından (PiStream)
            çağrılır; swap BİR SONRAKİ tur başında _current_client'ta uygulanır. Idempotent."""
            if not DEV_MODE_ENABLED:
                return
            if mode not in ("dev", "normal"):
                return
            if mode != self._mode:
                self._pending_mode = mode
                logger.info("mod isteği alındı: %s (aktif=%s)", mode, self._mode)

        async def _switch_mode(self, target: str) -> None:
            """Warm pi sürecini mod'lar arasında swap et. _swap_lock TUTULUYORKEN çağrılır."""
            old = self._client
            if target == "dev":
                await asyncio.to_thread(_ensure_dev_worktree)
                self._saved_normal = (self._persona, self._session_id)
                persona, session_id = DEV_PERSONA, DEV_SESSION_ID
                new = PiRpcClient(
                    persona, session_id, DEV_MODEL, DEV_THINKING,
                    cwd=DEV_WORKTREE, dev=True,
                )
            else:  # → normal: oturum başı persona/session'a bire bir dön
                persona, session_id = self._saved_normal or (
                    self._default_persona, self._default_persona
                )
                new = PiRpcClient(persona, session_id, self._model, self._thinking)
            logger.info(
                "mod swap: %s → %s (persona=%s session=%s)",
                self._mode, target, persona, session_id,
            )
            self._client = new
            self._persona, self._session_id = persona, session_id
            self._mode = target
            self._pending_mode = None
            await new.start()
            await old.stop()

        async def _current_client(self) -> "PiRpcClient":
            """Turluk çözüm: (1) bekleyen mod geçişi varsa uygula (dev↔normal); (2) dev
            modunda konuşmacı-swap YOK (tek dev oturumu); (3) normalde konuşmacı değiştiyse
            warm pi sürecini swap et; aynıysa mevcut warm süreci koru."""
            # (1) Mod geçişi — konuşmacı çözümünden ÖNCE (dev tool sinyali önceliklidir).
            if self._pending_mode is not None and self._pending_mode != self._mode:
                async with self._swap_lock:
                    if self._pending_mode is not None and self._pending_mode != self._mode:
                        await self._switch_mode(self._pending_mode)
            # (2) Dev modunda konuşmacıya göre swap ETME → dev oturumu tek ve izole.
            if self._mode == "dev":
                return self._client
            if self._speaker_state is None:
                return self._client
            # Kim konuşuyorsa bir sonraki oturumun prewarm'ı için işaretle (sadece
            # değişince yazar). Swap'tan ÖNCE: tahmin tuttuğunda swap HİÇ olmaz ama
            # işaret yine de güncel kalmalı.
            self._note_last_speaker(
                getattr(self._speaker_state, "current", None)
            )
            persona, session_id = self._target()
            if persona == self._persona and session_id == self._session_id:
                return self._client  # aynı kişi sürüyor → warm kalsın
            async with self._swap_lock:
                if persona == self._persona and session_id == self._session_id:
                    return self._client
                logger.info(
                    "pi swap: %s/%s → %s/%s (konuşmacı değişti)",
                    self._persona, self._session_id, persona, session_id,
                )
                old = self._client
                self._persona, self._session_id = persona, session_id
                # Beyin seçimi kişi değişse de AYNI kalır (oturum başında sabitlendi).
                self._client = PiRpcClient(persona, session_id, self._model, self._thinking)
                await self._client.start()
                await old.stop()
                return self._client

        # ── Sohbet sıfırlama (TEK yol: sesli komut + web butonu buraya iner) ──
        async def new_session(self) -> bool:
            """Sohbet geçmişini sıfırla, YENİ oturum başlat. Başarılıysa True.

            İki tetikleyici de (sesli komut → PiStream, web butonu → agent.py RPC)
            AYNI bu metoda iner → davranış tek yerde.

            Ne yapar: (1) warm pi sürecini durdur; (2) eski jsonl'in header id'sini
            döndür (_rotate_session_id — SİLMEZ, dosya kalır); (3) AYNI persona/
            session-id ile taze pi süreci doğur → pi o id'yi bulamaz, sıfırdan açar.

            Ne yapmaz: hafızaya (memory/) DOKUNMAZ — memory_add/soul_add ile
            kaydedilenler kalıcıdır; sıfırlanan yalnız SOHBET geçmişidir. Wake
            durumu, konuşmacı/persona ve mod (dev/normal) da KORUNUR."""
            async with self._swap_lock:
                old = self._client
                persona, session_id = self._persona, self._session_id
                dev = self._mode == "dev"
                # Dev modunda session dizini ana repo'ya sabitlenir (_build_pi_args ile
                # AYNI kural) → dev oturumu da doğru dosyada sıfırlanır.
                session_dir = REPO_ROOT / PI_SESSION_DIR if dev else Path(PI_SESSION_DIR)
                if not session_dir.is_absolute():
                    session_dir = REPO_ROOT / session_dir
                logger.info(
                    "sohbet sıfırlama: persona=%s session=%s mod=%s", persona, session_id, self._mode
                )
                # Sıra önemli: ÖNCE süreci durdur (dosyayı bırakmalı), SONRA döndür,
                # en son taze süreci doğur.
                await old.stop()
                try:
                    archived = await asyncio.to_thread(_rotate_session_id, session_id, session_dir)
                except Exception:  # noqa: BLE001 — döndürme patlarsa oturumu YARIDA bırakma
                    logger.warning("oturum döndürme başarısız", exc_info=True)
                    archived = None
                    ok = False
                else:
                    ok = True
                    logger.info(
                        "eski geçmiş korundu: %s",
                        archived.name if archived else "(dosya yok — zaten temizdi)",
                    )
                # Süreci HER KOŞULDA geri getir: döndürme başarısız olsa bile beyinsiz
                # kalmayalım (kullanıcı konuşmaya devam edebilsin).
                if dev:
                    new = PiRpcClient(
                        persona, session_id, DEV_MODEL, DEV_THINKING,
                        cwd=DEV_WORKTREE, dev=True,
                    )
                else:
                    new = PiRpcClient(persona, session_id, self._model, self._thinking)
                self._client = new
                await new.start()
                # Taze oturum = pi bu bağlantıda kimseyi selamlamadı → ismiyle-selam
                # direktifi tekrar verilebilsin (yoksa yeni sohbet selamsız başlar).
                self._greeted.discard(
                    getattr(self._speaker_state, "current", None) or ""
                )
                return ok

        def is_reset_command(self, text: str) -> bool:
            """Metin sohbet-sıfırlama komutu mu (deterministik, LLM YOK). Bkz. reset_match."""
            return reset_match(text)

        async def _reset_line(self, text: str) -> Optional[str]:
            """Sıfırlama kararı. Scripted TR satır döndürürse pi ATLANIR; None →
            normal pi akışı (mevcut davranış). Üç yol:
              tam eşleşme  → SORMADAN sıfırla (bugünkü davranış, korunur)
              yakın-ıska   → sıfırlama, SOR; onayı bir sonraki turda bekle
              onay bekliyor→ evet → sıfırla | hayır → vazgeç | başka → durumu düşür

            Onay durumu TURA ÖZEL: bir sonraki söz onay/ret değilse durum düşer ve
            metin normal prompt olarak işlenir (kullanıcı konuyu değiştirmiş olabilir)
            — kullanıcı "evet" demeye mecbur bırakılmaz."""
            if not RESET_ENABLED:
                return None
            if self._reset_pending:
                self._reset_pending = False  # her koşulda düşür (tek tur yaşar)
                if is_affirmative_reply(text):
                    logger.info("sıfırlama onaylandı (%r) → yürütülüyor", text[:40])
                    return await self.new_session_spoken()
                if _is_decline_enroll(text):
                    logger.info("sıfırlama reddedildi (%r) → vazgeçildi", text[:40])
                    return RESET_CONFIRM_NO
                # Onay/ret DEĞİL → düşür ve aşağıdaki normal kontrollere devam et
                # (söz taze bir sıfırlama komutu OLABİLİR).
                logger.info("sıfırlama onayı gelmedi (%r) → normal akış", text[:40])
            if reset_match(text):
                return await self.new_session_spoken()
            if reset_near_match(text):
                self._reset_pending = True
                logger.info("sıfırlamaya yakın-ıska (%r) → onay soruluyor", text[:60])
                return RESET_CONFIRM_ASK
            return None

        async def new_session_spoken(self) -> str:
            """Sesli komut yolu: sıfırla + söylenecek KISA onay satırını döndür."""
            try:
                ok = await self.new_session()
            except Exception:  # noqa: BLE001 — sıfırlama hatası turu/oturumu BOZMASIN
                logger.warning("sohbet sıfırlama başarısız", exc_info=True)
                return RESET_FAIL
            return RESET_ACK if ok else RESET_FAIL

        async def start(self) -> None:
            """Pre-warm: participant katılınca çağrılabilir (isteğe bağlı)."""
            await self._client.start()

        async def finalize(self) -> None:
            """Oturum kapanışı: pi'yı öldürmeden ÖNCE tek best-effort tur — kalıcı
            maddeler varsa memory_add ile kaydettir. 30 sn timeout; kapanışı ASLA
            bloklamaz, hata yutulur. Guest / süreç ölü / hafıza yok → hiçbir şey yapma."""
            await self._silent_turn(
                "Oturum bitiyor. Bu konuşmadan hatırlanmaya değer kalıcı 3-5 madde "
                "varsa memory_add ile kaydet; yoksa sadece 'yok' de. Sesli yanıt verme.",
                timeout=30.0,
            )

        def chat(
            self,
            *,
            chat_ctx,
            tools=None,
            conn_options=DEFAULT_API_CONNECT_OPTIONS,
            parallel_tool_calls=NOT_GIVEN,
            tool_choice=NOT_GIVEN,
            **kwargs,
        ) -> "PiStream":
            return PiStream(
                self,
                chat_ctx=chat_ctx,
                tools=tools or [],
                conn_options=conn_options,
            )

        async def aclose(self) -> None:
            if self._wake_task is not None:
                self._wake_task.cancel()
                self._wake_task = None
            await self._client.stop()


# ---------------------------------------------------------------------------
# Smoke test: alt-süreci spawn et, get_state gönder, yanıtı gör, kapat.
# Model token HARCAMAZ — sadece protokol bağlantısını doğrular.
# ---------------------------------------------------------------------------
async def _smoke() -> int:
    client = PiRpcClient(PI_DEFAULT_PERSONA, "smoke-test")
    print(f"[smoke] spawn: {' '.join(client._args)}  (cwd={REPO_ROOT})")
    try:
        resp = await client.request({"type": "get_state"}, timeout=60.0)
        ok = resp.get("type") == "response" and resp.get("command") == "get_state"
        print(f"[smoke] get_state response: {json.dumps(resp)[:400]}")
        print(f"[smoke] RESULT: {'PASS' if ok else 'FAIL'}")
        return 0 if ok else 1
    except Exception as e:  # noqa: BLE001
        err = ""
        if client._proc is not None and client._proc.stderr is not None:
            try:
                err = (await client._proc.stderr.read()).decode()[-800:]
            except Exception:
                pass
        print(f"[smoke] ERROR: {e!r}\n[smoke] stderr tail:\n{err}")
        return 1
    finally:
        await client.stop()


async def _prompt_test(text: str) -> int:
    """Tek prompt gerçek testi: DELTA_TEXT dolu mu, error var mı? (Minimal token.)"""
    client = PiRpcClient(PI_DEFAULT_PERSONA, "prompt-test")
    print(f"[prompt] model={PI_MODEL}  prompt={text!r}")
    await client.start()
    q: asyncio.Queue = asyncio.Queue()
    client._turn_q = q
    delta_text = ""
    error: Optional[str] = None
    final_msg: Any = None
    try:
        await client.send({"type": "prompt", "message": text})
        while True:
            obj = await asyncio.wait_for(q.get(), timeout=90.0)
            if obj is None:
                break
            t = obj.get("type")
            if t == "message_update":
                ame = obj.get("assistantMessageEvent") or {}
                if ame.get("type") == "text_delta":
                    delta_text += ame.get("delta") or ""
            elif t in ("message_end", "turn_end"):
                msg = obj.get("message")
                if isinstance(msg, dict) and msg.get("role") == "assistant":
                    final_msg = msg
                    if msg.get("stopReason") == "error":
                        error = msg.get("errorMessage") or "(bilinmiyor)"
            elif t == "agent_settled":
                break
        if not delta_text:
            delta_text = _assistant_msg_text(final_msg)
        ok = bool(delta_text) and error is None
        print(f"[prompt] DELTA_TEXT={delta_text!r}")
        print(f"[prompt] error={error}")
        print(f"[prompt] RESULT: {'PASS' if ok else 'FAIL'}")
        return 0 if ok else 1
    finally:
        await client.stop()


def _wake_test() -> int:
    """wake_match birim testi (token harcamaz). İzole/cümle × pozitif/negatif."""
    wn = _wake_norm(WAKE_WORD)
    vs = _wake_variants()
    # (metin, beklenen_wake, beklenen_kalan|None=umursama)
    cases = [
        # İZOLE pozitif (yanlış-transkripsiyonlar dahil)
        ("candan", True, ""), ("Candan.", True, ""), ("Can dan.", True, ""),
        ("John Don.", True, ""), ("John Donne.", True, ""), ("Kandan.", True, ""),
        ("CANDAN", True, ""),
        # İZOLE negatif
        ("merhaba", False, None), ("nasılsın", False, None), ("teşekkürler", False, None),
        # CÜMLE pozitif (gerçek candan → strip korunur)
        ("candan şu an saat kaç", True, "şu an saat kaç"),
        ("Candan hava nasıl", True, "hava nasıl"),
        # CÜMLE negatif (fuzzy cümlede UYGULANMAZ)
        ("kandan geldi haber", False, None), ("aradan zaman geçti", False, None),
        ("bir john don filmi", False, None),
    ]
    print(f"[wake] WAKE_WORD={WAKE_WORD!r} variants={sorted(vs)}")
    print(f"{'text':<26} {'wake':<6} {'strip':<18} result")
    ok = True
    for text, exp_wake, exp_rem in cases:
        got_wake, got_rem = wake_match(text, wn, vs)
        good = (got_wake == exp_wake) and (exp_rem is None or got_rem == exp_rem)
        ok = ok and good
        print(f"{text!r:<26} {str(got_wake):<6} {got_rem!r:<18} {'PASS' if good else 'FAIL'}")
    print(f"[wake] RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def _reset_test() -> int:
    """reset_match birim testi (token harcamaz). Kasıtlı komut × yanlış-pozitif.

    Kritik olan NEGATİF sütun: sıfırlama yıkıcı-hissi bir işlem, cümle içinde geçen
    aynı kelimeler ("dün sana yeni sohbet başlat demiştim") TETİKLEMEMELİ."""
    ps = _reset_phrases()
    cases = [
        # Kasıtlı komut (wake ayıklandıktan SONRAKİ metin) → sıfırla
        ("yeni sohbet başlat", True), ("Yeni sohbet başlat.", True),
        ("YENİ SOHBET BAŞLAT", True), ("yeni sohbete başla", True),
        ("sohbeti sıfırla", True), ("Sohbeti sıfırla!", True),
        ("geçmişi temizle", True), ("yeni konuşma başlat", True),
        ("sohbeti resetle", True),
        # Listeden ÇIKTI (muğlak): yürütülmemeli.
        ("baştan başla", False), ("yeni sayfa aç", False), ("yeni sohbet", False),
        # STT'nin ASCII yazımı (noktasız-ı yerine düz i) → aynı komut sayılmalı
        ("sohbeti sifirla", True), ("gecmisi sifirla", True),
        ("yeni sohbet baslat", True), ("bastan basla", False),  # "baştan başla" listeden çıktı
        # STT'nin geçmiş-zaman kayması: "başlat" → "başladı" (mesafe 2). Türkçe'de
        # emir kipi ile geçmiş zaman tek-iki harfle ayrışır → tolerans 1 ıskalıyordu.
        # CANLI HATA: bu ıska yüzünden metin LLM'e gitti, model sıfırlamadan
        # "yaptım" dedi, kullanıcı sıfırladım sandı (iki tur üst üste).
        ("yeni sohbet başladı", True), ("yeni sohbete başladı", True),
        # Sonradan eklenen ifadeler (kullanıcı "oturum"/"sayfa" da diyor)
        ("yeni oturum aç", True), ("yeni oturum başlat", True),
        ("oturumu yenile", True), ("oturumu sıfırla", True),
        # Yanlış-pozitif koruması: uzun cümle / alakasız söz → sıfırlama YOK
        ("hava nasıl", False), ("sohbet", False), ("başlat", False),
        ("dün sana yeni sohbet başlat demiştim ama olmadı", False),
        ("dün yeni sohbet başlat demiştim", False),
        ("yeni bir sohbet uygulaması yazalım mı acaba", False),
        ("bugün yeni sayfa açtık galiba", False),
        ("bana yeni bir şarkı çal", False), ("", False),
    ]
    # Yakın-ıska bandı: SORULACAK (True) / sorulmayacak (False). Tam eşleşen bir
    # komut yakın-ıska DEĞİLDİR (zaten sormadan yürütülür) → bantlar ÇAKIŞMAZ.
    near_cases = [
        # Benziyor ama tam değil → sor (sessizce LLM'e gitmesin)
        ("yeni sohbet başlasın", True), ("yeni oturum açalım", True),
        # Tam eşleşme → sorma, yürüt
        ("yeni sohbet başlat", False), ("yeni sohbet başladı", False),
        ("sohbeti sıfırla", False),
        # Alakasız / uzun → ne yürüt ne sor
        ("hava nasıl", False), ("bana yeni bir şarkı çal", False),
        ("bugün yeni sayfa açtık galiba", False),
        ("dün sana yeni sohbet başlat demiştim ama olmadı", False), ("", False),
    ]
    print(f"[reset] phrases={sorted(ps)}")
    print(f"{'text':<48} {'reset':<6} result")
    ok = True
    for text, exp in cases:
        got = reset_match(text)
        good = got == exp
        ok = ok and good
        print(f"{text!r:<48} {str(got):<6} {'PASS' if good else 'FAIL'}")
    print(f"\n{'text':<48} {'near':<6} result")
    for text, exp in near_cases:
        got = reset_near_match(text)
        good = got == exp
        ok = ok and good
        print(f"{text!r:<48} {str(got):<6} {'PASS' if good else 'FAIL'}")
        # Bantlar ayrık olmalı: aynı söz hem yürüt hem sor OLAMAZ.
        if got and reset_match(text):
            ok = False
            print(f"  !! BANT ÇAKIŞMASI: {text!r} hem tam hem yakın eşleşiyor")
    print(f"[reset] RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def _compaction_test() -> int:
    """Compaction ara sözü + watchdog toleransı testi. SAHTE pi olayları — süreç
    doğmaz, token harcanmaz.

    Doğrular:
      (a) bağlam taşıp cevap ÜRETİLEMEDEN compaction → kullanıcı sessiz beklemesin,
          ara söz söylensin;
      (b) cevap zaten aktıysa (acil olmayan, tur sonu compaction) → SUSSUN;
      (c) compaction penceresi normal stall toleransından UZUN sürse bile tur
          ABORT EDİLMESİN. (c) şart: compaction_start ile compaction_end arasında pi
          HİÇ olay yaymaz → tolerans yükseltilmezse watchdog sağlıklı sıkıştırmayı
          keser, kullanıcı cevabını kaybederdi."""
    if not _HAS_LIVEKIT:
        print("[compaction] SKIP: livekit yok")
        return 0

    class FakeClient:
        """PiRpcClient yerine: send() çağrılınca scripted olayları kuyruğa akıtır."""
        def __init__(self, script):
            self._turn_q = None
            self._turn_lock = asyncio.Lock()
            self.warmed_up = True   # sıcak → soğuk-yükleme ara sözü karışmasın
            self._script = script
            self.writes: list[dict] = []

        async def start(self) -> None:
            pass

        async def send(self, obj) -> None:
            async def feed():
                for delay, ev in self._script:
                    await asyncio.sleep(delay)
                    if self._turn_q is not None:
                        self._turn_q.put_nowait(ev)
            asyncio.create_task(feed())

        def _write(self, obj) -> None:
            self.writes.append(obj)

    def _delta(t: str) -> dict:
        return {"type": "message_update",
                "assistantMessageEvent": {"type": "text_delta", "delta": t}}

    def _cstart(reason: str) -> dict:
        return {"type": "compaction_start", "reason": reason}

    _CEND = {"type": "compaction_end", "aborted": False, "willRetry": True}
    _SETTLED = {"type": "agent_settled"}

    async def drive(script) -> tuple[str, list[dict]]:
        brain = PiBrain(persona=PI_DEFAULT_PERSONA)
        fc = FakeClient(script)
        brain._client = fc
        async def _cc():
            return fc
        brain._current_client = _cc      # swap/spawn yolunu devre dışı bırak
        ctx = llm.ChatContext.empty()
        ctx.add_message(role="user", content=f"{WAKE_WORD} merhaba")  # wake gate'i geç
        st = PiStream(brain, chat_ctx=ctx, tools=[],
                      conn_options=DEFAULT_API_CONNECT_OPTIONS)
        out = []
        async for ch in st:
            d = ch.delta
            if d and d.content:
                out.append(d.content)
        return "".join(out), fc.writes

    async def run() -> bool:
        ok = True
        notice = PI_COMPACTION_NOTICE_TEXT

        # (a) overflow: cevap YOK → ara söz söylensin, sonra cevap gelsin
        txt, _ = await drive([
            (0.02, _cstart("overflow")), (0.05, _CEND),
            (0.02, _delta("Merhaba!")), (0.02, _SETTLED),
        ])
        good = txt == notice + "Merhaba!"
        ok = ok and good
        print(f"(a) overflow, cevap YOK  → ara söz   {txt!r:<46} {'PASS' if good else 'FAIL'}")

        # (b) threshold: cevap AKMIŞ → sus (pi zaten tur sonuna saklamış)
        txt, _ = await drive([
            (0.02, _delta("Merhaba!")), (0.02, _cstart("threshold")),
            (0.05, _CEND), (0.02, _SETTLED),
        ])
        good = txt == "Merhaba!"
        ok = ok and good
        print(f"(b) threshold, cevap VAR → sessiz    {txt!r:<46} {'PASS' if good else 'FAIL'}")

        # (c) uzun compaction → watchdog KESMESİN. Testi hızlandırmak için normal
        # toleransı 0.3s'e indiriyoruz; compaction 1.0s (3 katı) sürüyor.
        global PI_TURN_STALL_TIMEOUT, PI_FIRST_TURN_STALL_TIMEOUT
        old = (PI_TURN_STALL_TIMEOUT, PI_FIRST_TURN_STALL_TIMEOUT)
        PI_TURN_STALL_TIMEOUT = PI_FIRST_TURN_STALL_TIMEOUT = 0.3
        try:
            txt, writes = await drive([
                (0.02, _delta("Merhaba!")), (0.02, _cstart("threshold")),
                (1.0, _CEND), (0.02, _delta(" Devam.")), (0.02, _SETTLED),
            ])
        finally:
            PI_TURN_STALL_TIMEOUT, PI_FIRST_TURN_STALL_TIMEOUT = old
        good = txt == "Merhaba! Devam." and not writes  # writes boş = abort YOK
        ok = ok and good
        print(f"(c) uzun compaction      → kesilmez  {txt!r:<46} {'PASS' if good else 'FAIL'}")
        return ok

    ok = asyncio.run(run())
    print(f"[compaction] RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def _rotate_test() -> int:
    """_rotate_session_id birim testi — GEÇİCİ dizinde, gerçek sessions/'a DOKUNMAZ.

    Doğrular: (1) eski dosya SİLİNMEZ, yerinde kalır; (2) mesaj satırları AYNEN durur
    (geçmiş kaybolmaz); (3) header id döner → pi artık o slug'ı bulamaz (taze oturum);
    (4) slug'a ait dosya yoksa None (çökme yok)."""
    import tempfile
    ok = True
    with tempfile.TemporaryDirectory() as d:
        sd = Path(d)
        p = sd / "2026-07-16T11-11-30-090Z_ayhan.jsonl"
        hdr = {"type": "session", "version": 1, "id": "ayhan", "cwd": "/x"}
        msg = {"type": "message", "id": "m1", "role": "user", "content": "merhaba"}
        p.write_text(json.dumps(hdr) + "\n" + json.dumps(msg) + "\n", encoding="utf-8")

        got = _rotate_session_id("ayhan", sd)
        lines = p.read_text(encoding="utf-8").splitlines()
        new_hdr = json.loads(lines[0])
        checks = [
            ("dosya yerinde kalır", p.is_file()),
            ("döndürülen dosya doğru", got == p),
            ("header id değişti", new_hdr["id"] != "ayhan"),
            ("id slug ile başlar", new_hdr["id"].startswith("ayhan-eski-")),
            ("id pi kuralına uyar", bool(re.fullmatch(
                r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", new_hdr["id"]))),
            ("geçmiş korundu", json.loads(lines[1]) == msg),
            ("başka alan bozulmadı", new_hdr["cwd"] == "/x" and new_hdr["version"] == 1),
            ("slug artık bulunmaz", _find_session_file("ayhan", sd) is None),
            ("eski id ile bulunur", _find_session_file(new_hdr["id"], sd) == p),
            ("tmp dosya kalmadı", not list(sd.glob("*.tmp"))),
            ("dosya yoksa None", _rotate_session_id("yok-boyle-biri", sd) is None),
        ]
        for name, good in checks:
            ok = ok and good
            print(f"{name:<28} {'PASS' if good else 'FAIL'}")
    print(f"[rotate] RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def _wake_timer_test() -> int:
    """Uyku sayacı birim testi — SAHTE SAAT (livekit/ses/token YOK).

    Kural: 15sn'lik uyku penceresi KULLANICININ SON konuşmasından (ya da asistanın
    cevabı daha sonra bittiyse ondan) itibaren sayılır; konuşma sürerken uyunmaz."""
    W = 15.0
    results: list[tuple[str, bool, str]] = []

    def gate() -> WakeGate:
        g = WakeGate(enabled=True, word="candan", window=W)
        g.wake_now(now=0.0)          # wake word ile uyandı (t=0)
        return g

    def tick(g: WakeGate, t0: float, t1: float) -> Optional[float]:
        """t0→t1 arası 0.5sn adımlarla uyku döngüsünü simüle et; uyuduğu anı döner."""
        t = t0
        while t <= t1 + 1e-9:
            if g.expire(now=t):
                return t
            t += 0.5
        return None

    # (a) uyanık + kullanıcı 20sn boyunca ARALIKLI konuşuyor → UYKUYA GEÇMEMELİ
    g = gate()
    slept_at = None
    t = 0.0
    for start in (1.0, 8.0, 15.0):          # 3 söz; her biri 4sn sürsün
        slept_at = slept_at or tick(g, t, start)
        g.set_user_speaking(True, now=start)          # VAD: konuşma başladı
        slept_at = slept_at or tick(g, start, start + 4.0)   # konuşurken sayaç durur
        g.set_user_speaking(False, now=start + 4.0)   # VAD: konuşma bitti
        t = start + 4.0
    slept_at = slept_at or tick(g, t, 20.0)  # t=19 → son sözden 1sn sonra
    ok = (slept_at is None) and g.awake
    results.append(("(a) 20sn aralıklı konuşma → uyanık kalır", ok,
                    f"slept_at={slept_at} awake={g.awake}"))

    # (b) kullanıcı konuşmayı bitirdi (t=10) → TAM 10+15=25'te uyu, ÖNCE DEĞİL
    g = gate()
    g.set_user_speaking(True, now=5.0)
    tick(g, 5.0, 10.0)
    g.set_user_speaking(False, now=10.0)    # SON konuşma anı
    early = tick(g, 10.0, 24.5)             # 24.5'e kadar UYUMAMALI
    slept_at = tick(g, 25.0, 30.0)
    ok = (early is None) and (slept_at == 25.0)
    results.append(("(b) son sözden 15sn sonra uyur (önce değil)", ok,
                    f"early={early} slept_at={slept_at} (beklenen 25.0)"))

    # (c) asistan UZUN cevap (t=2→30, 28sn) → cevap sırasında uyumaz; sayaç cevabın
    #     bitişinden başlar → 30+15=45'te uyur.
    g = gate()
    g.set_agent_busy(True, now=2.0)         # thinking/speaking
    during = tick(g, 2.0, 30.0)             # 28sn cevap → uyku YOK
    g.set_agent_busy(False, now=30.0)       # cevap bitti → sayaç burada başlar
    early = tick(g, 30.0, 44.5)
    slept_at = tick(g, 45.0, 50.0)
    ok = (during is None) and (early is None) and (slept_at == 45.0)
    results.append(("(c) uzun asistan cevabı → cevap bitince sayaç başlar", ok,
                    f"during={during} early={early} slept_at={slept_at} (beklenen 45.0)"))

    # (d) wake word → uyanma ve uyuduktan sonra tekrar uyanma hâlâ çalışıyor
    g = WakeGate(enabled=True, word="candan", window=W)
    a1, _ = g.decide("candan", now=0.0)          # sadece wake → silent + uyan
    awake1 = g.awake
    a2, p2 = g.decide("hava nasıl", now=3.0)     # uyanıkken normal söz → process
    slept_at = tick(g, 3.0, 25.0)                # 3+15=18'de uyu
    a3, _ = g.decide("merhaba", now=26.0)        # uykuda + wake yok → silent
    a4, p4 = g.decide("candan saat kaç", now=27.0)   # wake + kalan → process
    ok = (a1 == "silent" and awake1 and a2 == "process" and p2 == "hava nasıl"
          and slept_at == 18.0 and a3 == "silent" and a4 == "process"
          and p4 == "saat kaç" and g.awake)
    results.append(("(d) wake word uyandırma + fuzzy/strip korunuyor", ok,
                    f"{a1}/{a2}/{a3}/{a4} slept_at={slept_at}"))

    print(f"[waketimer] WAKE_WINDOW_SECONDS={W}")
    all_ok = True
    for name, ok, detail in results:
        all_ok = all_ok and ok
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  [{detail}]")
    print(f"[waketimer] RESULT: {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


def _policy_test() -> int:
    """Enroll → policy.json rol yazımı + rol yükseltme birim testi.

    GEÇİCİ policy dosyası + GEÇİCİ speakers.db kullanır (gerçek memory/policy.json ve
    worker/data/speakers.db'ye DOKUNMAZ). Sherpa/onnx/model/token GEREKMEZ (sahte
    SpeakerID). Senaryolar: (a) ilk enroll=adult (b) ikinci enroll=guest
    (c) ses-benzerlik merge → yeni policy girdisi YOK (d) adult yükseltir
    (e) guest kendini yükseltemez."""
    global MEMORY_DIR  # noqa: PLW0602 — atama globals()[..] ile yapılıyor; bu satır
    # gereksiz DEĞİL: ileride düz `MEMORY_DIR = x` yazılırsa yerelleşmesini önler.
    import shutil
    import tempfile

    if not _HAS_LIVEKIT:
        print("[policy] SKIP: livekit yok (worker/.venv ile çalıştır)")
        return 1

    import numpy as np
    from speaker_id import SpeakerStore

    tmp = tempfile.mkdtemp(prefix="candan-policy-test-")
    old_mem = MEMORY_DIR
    results: list[tuple[str, bool, str]] = []

    class FakeSpeakerID:
        """Sahte embed modeli: best_match'i test sürer."""
        model_id, dim, threshold, merge_low = "fake-v1", 4, 0.45, 0.35

        def __init__(self):
            self._names: list[str] = []
            self._ids: dict[str, int] = {}
            self.match: tuple[Optional[str], float] = (None, 0.0)

        def best_match(self, emb):
            return self.match

        def id_for(self, name):
            return self._ids.get(name)

        def names(self):
            return list(self._names)

        def reload(self, speakers):
            self._names = [s["name"] for s in speakers]
            self._ids = {s["name"]: s["id"] for s in speakers}

    class FakeState:
        current = None
        last_embedding = None

    def policy() -> dict:
        return _read_policy()

    async def run() -> None:
        nonlocal results
        MEM = Path(tmp) / "memory"
        MEM.mkdir(parents=True, exist_ok=True)
        globals()["MEMORY_DIR"] = str(MEM)  # mutlak → REPO_ROOT / MEMORY_DIR = MEM
        store = SpeakerStore(str(Path(tmp) / "speakers.db"))
        sid = FakeSpeakerID()
        state = FakeState()
        state.last_embedding = np.array([1, 0, 0, 0], dtype=np.float32)
        brain = PiBrain(speaker_state=state, speaker_id=sid, speaker_store=store)

        # (a) policy BOŞ + ilk enroll → adult
        brain._enroll_name = "Ayhan"
        brain._enroll_emb = state.last_embedding
        brain._enroll_name_emb = state.last_embedding
        sid.match = (None, 0.0)  # kayıtlı kimse yok
        line_a = await brain._finish_enrollment()
        pol = policy()
        ok = pol == {"ayhan": "adult"}
        results.append(("(a) policy boş + ilk enroll → adult", ok,
                        f"policy={pol} line={line_a!r}"))

        # (b) policy DOLU + ikinci (farklı) kişi → guest
        state.current = None
        brain._enroll_name = "Zeynep"
        brain._enroll_emb = brain._enroll_name_emb = np.array([0, 1, 0, 0], dtype=np.float32)
        sid.match = ("Ayhan", 0.10)  # benzemiyor → gerçekten yeni kişi
        line_b = await brain._finish_enrollment()
        pol = policy()
        ok = pol == {"ayhan": "adult", "zeynep": "guest"} and "misafir" in line_b
        results.append(("(b) policy dolu + 2. enroll → guest (+ sınır bildirildi)", ok,
                        f"policy={pol} line={line_b!r}"))

        # (c) ses-benzerlik kapısı MEVCUT kişiye merge etti → YENİ policy girdisi YOK
        before = dict(policy())
        state.current = None
        brain._enroll_name = "Ahmet"
        brain._enroll_emb = brain._enroll_name_emb = np.array([1, 0, 0, 0], dtype=np.float32)
        sid.match = ("Ayhan", 0.90)  # >= threshold → merge
        line_c = await brain._finish_enrollment()
        pol = policy()
        ok = (pol == before and "ahmet" not in pol and state.current == "Ayhan")
        results.append(("(c) benzerlik merge → policy'ye YENİ girdi eklenmiyor", ok,
                        f"policy={pol} current={state.current!r} line={line_c!r}"))

        # (d) adult "Zeynep'i yetişkin yap" → zeynep = adult
        state.current = "Ayhan"  # aktör: adult
        line_d = brain._role_command("Zeynep'i yetişkin yap")
        pol = policy()
        ok = pol.get("zeynep") == "adult" and _mem_user("zeynep") == "zeynep"
        results.append(("(d) adult → 'Zeynep'i yetişkin yap' → adult + MEM_USER dolu", ok,
                        f"policy={pol} line={line_d!r}"))
        # aynı komut "aileye ekle" biçimiyle de tanınıyor mu (parser)
        ok2 = parse_promote("Ayşe'yi aileye ekle") == "Ayşe" and parse_promote("hava nasıl") is None
        results.append(("(d2) parser: 'Ayşe'yi aileye ekle' → Ayşe; normal cümle → None", ok2,
                        f"{parse_promote('Ayşe’yi aileye ekle')!r}"))

        # (e) GUEST kendini (ve başkasını) yükseltemez → policy DEĞİŞMEZ
        _policy_set("zeynep", "guest")  # geri düşür (guest aktör kuralım)
        state.current = "Zeynep"
        before = dict(policy())
        lines = [brain._role_command("Zeynep'i yetişkin yap"),
                 brain._role_command("beni aileye ekle"),
                 brain._role_command("Zeynep'i aile üyesi yap")]
        pol = policy()
        ok = pol == before and all(l == "Bunu ancak evin yetişkini yapabilir." for l in lines)
        results.append(("(e) guest kendini yükseltemez → REDDEDİLDİ, policy sabit", ok,
                        f"policy={pol} lines={lines}"))

        # slug tutarlılığı: policy anahtarı == session_id == MEM_USER == users/<user>
        state.current = "Ayhan"
        persona, session_id = brain._target()
        ok = (session_id == _slug("Ayhan") == "ayhan" and _mem_user(session_id) == "ayhan"
              and "ayhan" in policy())
        results.append(("(f) slug: policy anahtarı == session_id == MEM_USER", ok,
                        f"session_id={session_id!r} mem_user={_mem_user(session_id)!r}"))

        # atomiklik: policy.json her zaman geçerli JSON (tmp + os.replace)
        raw = _policy_path().read_text()
        ok = isinstance(json.loads(raw), dict)
        results.append(("(g) policy.json geçerli JSON (atomik yazım)", ok, raw.replace("\n", " ")))

    try:
        asyncio.run(run())
    finally:
        globals()["MEMORY_DIR"] = old_mem
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"[policy] geçici kök: {tmp} (silindi)")
    all_ok = True
    for name, ok, detail in results:
        all_ok = all_ok and ok
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  [{detail}]")
    print(f"[policy] RESULT: {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


def _proactive_test() -> int:
    """Proaktif ajan + konsolidasyon senaryoları — SAHTE SAAT/IO (ses, token, livekit YOK).

    Senaryolar (kullanıcının istediği 7):
      (a) 23:50'de "saat 1" → due YARIN 01:00   [events.ts selftest — gerçek TZ aritmetiği]
      (b) vakti gelen pending → seslenme tetikleniyor
      (c) cevap yok → 1 kez daha → pending'e dönüyor (attempts++)
      (d) kullanıcı konuşuyorken → seslenme ERTELENİYOR
      (e) kullanıcı odada yok → seslenme YOK; bağlanınca gecikmiş iletiliyor (>12sa "geçmiş")
      (f) UYKUDAYKEN → seslenme YAPILIYOR (uyku susturmuyor) + GİRİŞ penceresi de açılıyor
          (kullanıcı 'candan' demeden cevap verebilir; ama cevabı pi'ya gitmez) — canlı bug
      (g) profile 2 KB'ı aşınca → konsolidasyon tetikleniyor (kayıpsızlık: events.ts (9))
    """
    global MEMORY_DIR  # noqa: PLW0602 — atama globals()[..] ile yapılıyor; bu satır
    # gereksiz DEĞİL: ileride düz `MEMORY_DIR = x` yazılırsa yerelleşmesini önler.
    import shutil
    import subprocess
    import tempfile

    if not _HAS_LIVEKIT:
        print("[proactive] SKIP: livekit yok (worker/.venv ile çalıştır)")
        return 1

    from reminders import AckTracker, Deliverer, EventStore

    results: list[tuple[str, bool, str]] = []
    tmp = tempfile.mkdtemp(prefix="candan-proactive-")
    old_mem, old_env = MEMORY_DIR, dict(os.environ)
    T0 = 1_000_000.0  # sahte "şimdi" (monotonic değil; Deliverer'a enjekte edilir)

    class FakeIO:
        """ProactiveIO sahtesi. GERÇEK WakeGate kullanır → uyku/kesme etkileşimi de test edilir."""

        def __init__(self, gate: WakeGate):
            self.gate = gate
            self.said: list[str] = []
            self.interruptible: list[bool] = []   # her say() kesilebilir miydi
            self.replies: list[bool] = []   # wait_reply'ın sırayla döneceği cevaplar
            self.here = True
            self.holds: list[bool] = []
            self.slept = 0                  # cevapsız kalınca uyandırma geri alındı mı
            # Cevap beklenirken (kullanıcı "efendim" derken) dünyanın hali:
            self.awake_at_reply: list[bool] = []   # GİRİŞ penceresi açık mıydı?
            self.decide_at_reply: list[str] = []   # o söz pi'ya gider miydi?

        def present(self) -> bool:
            return self.here

        def busy(self) -> bool:
            return self.gate.busy()

        def display_name(self, user: str) -> str:
            return (user or "").capitalize()

        def set_busy(self, v: bool) -> None:
            self.gate.set_agent_busy(v)

        def hold(self, v: bool) -> None:
            self.gate.hold = v
            self.holds.append(v)

        def wake(self) -> bool:
            return self.gate.wake_now()     # agent.py: brain.proactive_wake() (KOŞULSUZ)

        def sleep(self) -> None:
            self.slept += 1
            self.gate.sleep_now()           # agent.py: brain.proactive_sleep()

        async def say(self, text: str, interruptible: bool = True) -> bool:
            self.said.append(text)
            self.interruptible.append(interruptible)
            return True                     # kesilmedi (barge-in senaryosu: LiveIO/TurnIO)

        async def wait_reply(self, timeout: float) -> bool:
            # Kullanıcı tam BURADA cevap veriyor ("efendim"): o an giriş penceresi açık
            # mıydı ve o söz pi'ya gider miydi? (canlı bug'ın tam noktası)
            self.awake_at_reply.append(self.gate.awake)
            self.decide_at_reply.append(self.gate.decide("efendim")[0])
            return self.replies.pop(0) if self.replies else False

    def fresh(asleep: bool = True) -> tuple[EventStore, FakeIO, Deliverer, WakeGate]:
        store = EventStore(Path(tmp) / f"ev-{uuid.uuid4().hex}.db")
        gate = WakeGate(enabled=True, word="candan", window=15.0)
        if not asleep:
            gate.wake_now()
        io = FakeIO(gate)
        d = Deliverer(store, io, reply_timeout=0.01, retry_after=300.0,
                      late_hours=12.0, now_fn=lambda: T0)
        return store, io, d, gate

    async def run() -> None:
        # (b) vakti gelmiş pending → seslen + onay + ilet
        store, io, d, gate = fresh()
        eid = store.add("reminder", "ayhan", "yatma vakti", due_ts=T0 - 60, now=T0 - 3600)
        io.replies = [True]                       # kullanıcı "efendim" dedi
        n = await d.tick("ayhan")
        ev = store.get(eid)
        ok = (n == 1 and io.said[0] == "Ayhan, bir hatırlatmam var."
              and "yatma vakti" in io.said[1]
              and ev.status == "delivered" and ev.attempts == 1)
        results.append(("(b) vakti gelen pending → seslendi, onay alındı, iletildi", ok,
                        f"said={io.said} status={ev.status} attempts={ev.attempts}"))
        # (b3) seslenme metni: İSİM + KONU var, ama hatırlatmanın İÇERİĞİ SIZMIYOR
        ok = ("Ayhan" in io.said[0] and "hatırlat" in io.said[0].lower()
              and "yatma vakti" not in io.said[0])
        results.append(("(b3) seslenme: isim + konu; içerik onaydan ÖNCE sızmıyor", ok,
                        f"call={io.said[0]!r}"))
        # (b4) task_done başka bir konu bildirir + varyantlar dönüyor (robotik tekrar yok)
        store, io, d, gate = fresh()
        store.add("task_done", "ayhan", "çamaşır", due_ts=T0 - 60, now=T0 - 120)
        store.add("reminder", "ayhan", "ilaç", due_ts=T0 - 50, now=T0 - 120)
        io.replies = [True, True]
        await d.tick("ayhan")
        calls = [s for s in io.said if s.startswith("Ayhan,")]
        ok = (len(calls) == 2 and "işin bitti" in calls[0] and calls[1] != calls[0]
              and "çamaşır" not in calls[0] and "ilaç" not in calls[1])
        results.append(("(b4) task_done → 'bir işin bitti'; varyant rotasyonu (tekrar yok)",
                        ok, f"calls={calls}"))
        # üç zaman da kayıtlı mı (determinizm şartı)
        ok = bool(ev.requested_at and ev.due_at and store.get(eid).status == "delivered")
        results.append(("(b2) requested_at / due_at / status ÜÇÜ DE kayıtlı", ok,
                        f"requested={ev.requested_at} due={ev.due_at} status={ev.status}"))

        # (c) cevap YOK → bir kez daha seslen → pending'e dön (attempts++), hemen tekrarlama
        store, io, d, gate = fresh()
        eid = store.add("reminder", "ayhan", "su iç", due_ts=T0 - 60, now=T0 - 120)
        io.replies = [False, False]
        n = await d.tick("ayhan")
        ev = store.get(eid)
        again = await d.tick("ayhan")             # backoff → hemen ısrar ETMEZ
        ok = (n == 0 and len(io.said) == 2 and io.said[0] != io.said[1]
              and all(s.startswith("Ayhan,") for s in io.said)
              and ev.status == "pending" and ev.attempts == 1 and again == 0)
        results.append(("(c) cevap yok → 1 kez daha → pending (attempts++), ısrar yok", ok,
                        f"said={io.said} status={ev.status} attempts={ev.attempts}"))
        # (c2) cevapsız kalınca UYANMA GERİ ALINIR: seslenmek için uyandırdık, karşılık
        # yoksa pencereyi açık bırakmak boşuna dinleme/token demek → tekrar uyku.
        ok = (io.awake_at_reply == [True, True]   # ama seslenirken pencere AÇIKTI
              and io.slept == 1 and not gate.awake)
        results.append(("(c2) cevapsızsa uyandırma geri alındı (tekrar uyku)", ok,
                        f"awake_at_reply={io.awake_at_reply} slept={io.slept} "
                        f"awake={gate.awake}"))
        # (c3) ZATEN UYANIKKEN cevapsız kalırsa uyutMA (bizim açmadığımızı kapatmayız)
        store, io, d, gate = fresh(asleep=False)
        store.add("reminder", "ayhan", "su iç", due_ts=T0 - 60, now=T0 - 120)
        io.replies = [False, False]
        await d.tick("ayhan")
        ok = (io.slept == 0 and gate.awake)
        results.append(("(c3) uyanıkken cevapsız → pencere KAPATILMAZ (bozmuyoruz)", ok,
                        f"slept={io.slept} awake={gate.awake}"))

        # (d) kullanıcı KONUŞUYOR → seslenme ERTELENİR (kesme koruması)
        store, io, d, gate = fresh(asleep=False)
        eid = store.add("reminder", "ayhan", "ilaç", due_ts=T0 - 60, now=T0 - 120)
        gate.set_user_speaking(True)              # VAD: kullanıcı konuşuyor
        io.replies = [True]
        n = await d.tick("ayhan")
        ok = (n == 0 and io.said == [] and store.get(eid).status == "pending"
              and any("defer" in x for x in d.log))
        results.append(("(d) kullanıcı konuşuyorken → seslenme ERTELENDİ (pending kaldı)", ok,
                        f"said={io.said} log={d.log[-1:]}"))
        gate.set_user_speaking(False)             # konuşma bitti → sıradaki tick iletir
        io.replies = [True]
        n = await d.tick("ayhan")
        ok = (n == 1 and store.get(eid).status == "delivered")
        results.append(("(d2) konuşma bitince → sırası gelince iletildi", ok, f"said={io.said}"))

        # (e) kullanıcı ODADA YOK → seslenme YOK, pending kalır; bağlanınca GECİKMİŞ iletilir
        store, io, d, gate = fresh()
        eid = store.add("reminder", "ayhan", "yatma vakti",
                        due_ts=T0 - 20 * 3600, now=T0 - 30 * 3600)   # 20 saat gecikmiş
        io.here = False
        n = await d.tick("ayhan")
        ok = (n == 0 and io.said == [] and store.get(eid).status == "pending")
        results.append(("(e) kullanıcı odada yok → seslenme YOK, pending korundu", ok,
                        f"said={io.said} status={store.get(eid).status}"))
        io.here = True                            # kullanıcı bağlandı
        io.replies = [True]
        n = await d.tick("ayhan")
        msg = io.said[1] if len(io.said) > 1 else ""
        ok = (n == 1 and "geç kaldım" in msg.lower() and "vakti geçmiş" in msg.lower()
              and store.get(eid).status == "delivered")
        results.append(("(e2) bağlanınca gecikmiş (>12sa) → 'geç kaldım/geçmiş' diye iletildi",
                        ok, f"msg={msg!r}"))

        # (f) UYKUDAYKEN → seslenme YAPILIYOR (uyku susturmuyor) + sayaç dondu + pencere açıldı
        store, io, d, gate = fresh(asleep=True)
        assert not gate.awake
        eid = store.add("reminder", "ayhan", "yatma vakti", due_ts=T0 - 60, now=T0 - 120)
        io.replies = [True]
        # Uykudayken kullanıcı sözü normalde pi'ya gitmez; seslenme sırasında hold=True
        # olduğu için ONAY sözü de pi'ya GİTMEZ (çift cevap yok) → decide() 'silent'.
        n = await d.tick("ayhan")
        ev = store.get(eid)
        ok = (n == 1 and io.said[0].startswith("Ayhan,") and ev.status == "delivered"
              and io.holds == [True, False]      # hold açıldı ve KAPANDI
              and gate.awake                     # onay sonrası konuşma penceresi AÇIK
              and not gate.agent_busy)           # meşgul bayrağı geri bırakıldı
        results.append(("(f) UYKUDAYKEN seslendi (susturulmadı); hold aç/kapa, pencere açıldı",
                        ok, f"said={io.said[0]!r} awake={gate.awake} holds={io.holds}"))
        # (f1) CANLI BUG REGRESYONU: uykudayken seslendik → kullanıcı "efendim" derken
        # GİRİŞ penceresi de AÇIK olmalı. Eskiden çıkış (say) uykuyu delerdi ama giriş
        # hâlâ wake word beklerdi → cevap ONAY sayılmaz, önce "candan" demek gerekirdi.
        ok = (io.awake_at_reply == [True] and io.slept == 0)
        results.append(("(f1) uykudayken seslenince GİRİŞ penceresi de açılıyor "
                        "('candan' demeden onay)", ok,
                        f"awake_at_reply={io.awake_at_reply} slept={io.slept}"))
        # (f2) ...ama o onay sözü pi'ya GİTMEZ (hold) → çift konuşma yok
        ok = io.decide_at_reply == ["silent"]
        results.append(("(f2) onay sözü ('efendim') pi'ya GİTMİYOR (çift cevap yok)", ok,
                        f"decide_at_reply={io.decide_at_reply}"))
        # (f3) ÜRETİM KABLOLAMASI (bug'ın kök sebebi): agent.py _LiveKitIO.wake() eskiden
        # brain.wake_now() çağırıyordu — o METİNDE wake word ARAR → boş metinle SESSİZ
        # NO-OP → pencere hiç açılmazdı. proactive_wake() koşulsuz açar, proactive_sleep()
        # geri alır. (FakeIO gate'i doğrudan kullandığı için bu ayrım SADECE burada görünür.)
        pb = PiBrain(session_id="ayhan")
        pb._wake.enabled = True
        pb._wake.awake = False
        noop = pb.wake_now("")                    # eski yol: wake word yok → no-op
        opened = pb.proactive_wake()              # yeni yol: KOŞULSUZ aç
        closed = pb.proactive_sleep()
        ok = (noop is False and opened is True and pb._wake.awake is False and closed is True)
        results.append(("(f3) wake_now('') no-op (kök sebep); proactive_wake/sleep çalışıyor",
                        ok, f"wake_now('')={noop} proactive_wake={opened} sleep={closed}"))

        # ── (h) CANLI BUG (olaylar #11 "çay" / #12 "timer", 14.07.2026) ──────────────
        # "Candan seslendi, kullanıcı 'Dinliyorum.' dedi, Candan hatırlatmayı HİÇ
        # söylemedi — o cümleye yeni bir soruymuş gibi cevap verdi."
        # Kök sebep: wait_reply sözün BAŞLAMASINI (VAD 'speaking') onay sayıyordu; final
        # transkript SANİYELER sonra geliyor. Aradaki boşlukta:
        #   (a) hatırlatma kullanıcı konuşurken söylenip barge-in ile KESİLİYOR,
        #   (b) `_deliver` bitip `hold` KAPANIYOR → geciken transkript kapıdan geçip
        #       pi'ya düşüyor → pi onu yeni soru sanıp cevaplıyor.
        # (h0) AckTracker sözleşmesi: VAD TEK BAŞINA "bitti" DEMEK DEĞİLDİR.
        ack = AckTracker(settle=0.05)
        ack.arm()
        ack.on_speaking(True)                     # kullanıcı konuşmaya başladı
        started = ack.seen.is_set() and not ack.done.is_set()
        ack.on_transcript(is_final=False)         # partial → hâlâ "bitti" değil
        mid = not ack.done.is_set()
        ack.on_transcript(is_final=True)          # final geldi ama VAD hâlâ konuşuyor
        still = not ack.done.is_set()
        ack.on_speaking(False)                    # ...ve sustu → İŞTE ŞİMDİ bitti
        ok = started and mid and still and ack.done.is_set()
        results.append(("(h0) AckTracker: VAD 'konuşuyor' ≠ söz bitti (final + sessizlik "
                        "şart)", ok, f"started={started} mid={mid} still={still} "
                                     f"done={ack.done.is_set()}"))
        # (h0b) hiç karşılık yok → wait() False (mevcut davranış: 2. kez seslen)
        no_reply = await AckTracker(settle=0.05).wait(0.02)
        results.append(("(h0b) karşılık yok → wait() False (2. seslenme yolu bozulmadı)",
                        no_reply is False, f"wait={no_reply}"))

        class LiveIO(FakeIO):
            """ÜRETİM KABLOLAMASI: agent.py'deki gerçek AckTracker + gerçek WakeGate.
            Kullanıcı seslenmeyi duyunca cevap verir; VAD ÖNCE, final transkript SONRA
            gelir (Whisper endpointing) — canlıdaki zamanlamanın aynısı."""

            def __init__(self, gate: WakeGate, *, speech: float = 0.05,
                         playout: float = 0.05, transcript: bool = True):
                super().__init__(gate)
                self.ack = AckTracker(settle=0.15)
                self.speech = speech          # kullanıcının cümlesi ne kadar sürüyor
                self.playout = playout        # bizim sözümüzün çalma süresi
                self.transcript = transcript  # final transkript hiç gelir mi (gürültü?)
                self.answer = True            # ilk seslenmeye cevap verecek mi
                self.speaking = False         # kullanıcı ŞU AN konuşuyor mu (barge-in)
                self.cut: list[bool] = []     # her say() kesildi mi
                self.at_transcript: list[tuple] = []   # transkript ANINDA (hold, decide)

            async def _answers(self) -> None:
                self.speaking = True
                self.ack.on_speaking(True)                  # agent.py: user_state_changed
                await asyncio.sleep(self.speech)
                if not self.transcript:
                    return                                  # gürültü: VAD var, söz yok
                self.speaking = False
                self.ack.on_transcript(is_final=True)       # agent.py: user_input_transcribed
                self.ack.on_speaking(False)
                await asyncio.sleep(0.01)                   # pi turu birazdan başlar...
                # ...ve transkript TAM BURADA kapıya çarpar. Kapı kapalı mı?
                self.at_transcript.append((self.gate.hold, self.gate.decide("dinliyorum")[0]))

            async def say(self, text: str, interruptible: bool = True) -> bool:
                cut = self.speaking            # kullanıcı konuşurken konuşursak KESİLİRİZ
                self.said.append(text)
                self.interruptible.append(interruptible)
                self.cut.append(cut)
                if self.answer and text.startswith("Ayhan,"):
                    self.answer = False
                    self._t = asyncio.create_task(self._answers())   # seslenmeyi duydu
                # KESİLEN söz ERKEN biter (SpeechHandle.wait_for_playout interrupt'ta
                # hemen döner) — canlıda `hold`u transkriptten SANİYELER önce kapatan
                # şey tam olarak buydu. Kesilmeyen söz sonuna kadar çalar.
                await asyncio.sleep(0.0 if cut else self.playout)
                return not cut

            async def wait_reply(self, timeout: float) -> bool:
                self.awake_at_reply.append(self.gate.awake)
                return await self.ack.wait(timeout)

            async def spoke_out(self) -> None:
                """Kullanıcının sözü/transkripti sonuna kadar aksın (test senkronu):
                bug'lı halde transkript `tick` BİTTİKTEN SONRA gelir — asıl mesele o."""
                t = getattr(self, "_t", None)
                if t is not None:
                    await t

        # (h1) Onay geldi → hatırlatma KESİLMEDEN söylendi VE onay sözü pi'ya GİTMEDİ.
        store = EventStore(Path(tmp) / f"ev-{uuid.uuid4().hex}.db")
        gate = WakeGate(enabled=True, word="candan", window=15.0)
        io = LiveIO(gate)
        d = Deliverer(store, io, reply_timeout=1.0, retry_after=300.0, late_hours=12.0,
                      now_fn=lambda: T0)
        eid = store.add("reminder", "ayhan", "çay içmeyi hatırla",
                        due_ts=T0 - 60, now=T0 - 120)
        n = await d.tick("ayhan")
        await io.spoke_out()          # kullanıcının cümlesi kapıya çarpsın (geç de olsa)
        ev = store.get(eid)
        ok = (n == 1 and len(io.said) == 2 and io.cut == [False, False]
              and "çay içmeyi hatırla" in io.said[1]     # hatırlatma GERÇEKTEN söylendi
              and ev.status == "delivered")
        results.append(("(h1) onaydan sonra konuşuluyor → hatırlatma barge-in ile "
                        "KESİLMİYOR", ok, f"cut={io.cut} said={io.said} status={ev.status}"))
        # (h1b) ...ve o onay cümlesi ("Dinliyorum.") kapıya çarptığında hold HÂLÂ AÇIK →
        # pi'ya GİTMİYOR. Canlıda tam tersi olmuştu: hold kapanmış, cümle pi'ya düşmüştü.
        ok = io.at_transcript == [(True, "silent")]
        results.append(("(h1b) onay transkripti hold AÇIKKEN geliyor → pi'ya gitmiyor "
                        "(canlı bug)", ok, f"at_transcript={io.at_transcript}"))

        # (h2) GÜVENLİ BAŞARISIZLIK: VAD tetiklendi ama final transkript HİÇ gelmedi
        # (gürültü) ve kullanıcı susmuyor → settle penceresi dolar, konuşuruz, barge-in
        # KESER → teslim SAYILMAZ: olay pending kalır, sonra tekrar denenir. KAYBOLMAZ.
        store = EventStore(Path(tmp) / f"ev-{uuid.uuid4().hex}.db")
        gate = WakeGate(enabled=True, word="candan", window=15.0)
        io = LiveIO(gate, speech=5.0, transcript=False)   # sürekli gürültü, söz yok
        d = Deliverer(store, io, reply_timeout=1.0, retry_after=300.0, late_hours=12.0,
                      now_fn=lambda: T0)
        eid = store.add("reminder", "ayhan", "ilacını al", due_ts=T0 - 60, now=T0 - 120)
        n = await d.tick("ayhan")
        ev = store.get(eid)
        ok = (n == 0 and io.cut[-1] is True and ev.status == "pending" and ev.attempts == 1
              and any("cut#" in x for x in d.log))
        results.append(("(h2) hatırlatma kesildiyse teslim SAYILMAZ → pending kalır "
                        "(kaybolmaz)", ok,
                        f"cut={io.cut} status={ev.status} attempts={ev.attempts} "
                        f"log={d.log[-1:]}"))

        # ── (h3) AYNI CANLI BUG, İKİNCİ TUR: aaabb94'ten SONRA da sürdü ─────────────
        # h1/h1b'nin GÖRMEDİĞİ şey: barge-in'in tek kaynağı kullanıcının VAD'ı DEĞİL.
        # livekit-agents, kullanıcının turu BİTİNCE (voice/agent_activity.py,
        # `_user_turn_completed_task`) o an çalan sözü KENDİSİ keser, sonra o cümleye
        # cevap üretir:
        #     if (current := self._current_speech) is not None:
        #         if not current.allow_interruptions: ... return   # cevabı HİÇ ÜRETME
        #         await current.interrupt()                        # KES → sonra cevap üret
        # Onay ("efendim, dinliyorum") bir turu BİTİRİR; hatırlatma tam o sırada çalmaya
        # başlamıştır → KESİLİR. `_deliver` erken çıkıp `hold`u kapatır, hemen ardından
        # pi turu açılır ve onay cümlesini YENİ SORU sanıp cevaplar. Kullanıcının gördüğü:
        # "hatırlatmam var" → "efendim" → alakasız cevap, hatırlatma YOK.
        # TurnIO SADECE bu semantiği modeller (kullanıcı seslenmeyi dinler, sonra cevap
        # verir; EOU tespiti final transkriptten biraz SONRA gelir — canlıdaki sıra).
        class TurnIO(FakeIO):
            class _Speech:
                def __init__(self, text: str, interruptible: bool):
                    self.text = text
                    self.interruptible = interruptible
                    self.interrupted = False
                    self.stop = asyncio.Event()   # kesildi → playout erken biter
                    self.over = asyncio.Event()   # söz gerçekten bitti (say() döndü)

            def __init__(self, gate: WakeGate, *, speech: float = 0.02,
                         playout: float = 0.30, eou_delay: float = 0.02):
                super().__init__(gate)
                self.ack = AckTracker(settle=0.15)
                self.speech = speech            # onay cümlesinin süresi
                self.playout = playout          # bizim sözümüzün çalma süresi
                self.eou_delay = eou_delay      # final transkript → EOU tespiti gecikmesi
                self.answered = False
                self.current: object = None     # çalan söz (livekit: _current_speech)
                self.heard: list[str] = []      # kullanıcının GERÇEKTEN duyduğu sözler
                self.pi_turns: list[str] = []   # pi'ya DÜŞEN cümleler (olmamalı!)

            async def _user_answers(self, callout: "TurnIO._Speech") -> None:
                await callout.over.wait()               # önce seslenmeyi dinler
                self.ack.on_speaking(True)              # agent.py: user_state_changed
                await asyncio.sleep(self.speech)
                self.ack.on_transcript(is_final=True)   # agent.py: user_input_transcribed
                self.ack.on_speaking(False)             # ...ve sustu → onay TAMAM
                await asyncio.sleep(self.eou_delay)     # EOU tespiti biraz SONRA gelir
                await self._end_of_turn("efendim, dinliyorum")

            async def _end_of_turn(self, text: str) -> None:
                """livekit `_user_turn_completed_task`ın DAVRANIŞI (birebir)."""
                sp = self.current
                if sp is not None and not sp.stop.is_set():
                    if not sp.interruptible:
                        return              # söz kesilemez → bu turu CEVAPLAMA (skip)
                    sp.interrupted = True
                    sp.stop.set()
                    await sp.over.wait()    # `await current_speech.interrupt()`
                if self.gate.decide(text)[0] == "process":   # pi turu → wake/hold kapısı
                    self.pi_turns.append(text)

            async def say(self, text: str, interruptible: bool = True) -> bool:
                sp = TurnIO._Speech(text, interruptible)
                self.current = sp
                self.said.append(text)
                self.interruptible.append(interruptible)
                if not self.answered and text.startswith("Ayhan,"):
                    self.answered = True                    # seslenmeyi duydu
                    self._t = asyncio.create_task(self._user_answers(sp))
                try:
                    await asyncio.wait_for(sp.stop.wait(), self.playout)
                except asyncio.TimeoutError:
                    pass                                    # sonuna kadar çaldı
                if not sp.interrupted:
                    self.heard.append(text)                 # kullanıcı bunu DUYDU
                self.current = None
                sp.over.set()
                return not sp.interrupted

            async def wait_reply(self, timeout: float) -> bool:
                self.awake_at_reply.append(self.gate.awake)
                return await self.ack.wait(timeout)

            async def spoke_out(self) -> None:
                t = getattr(self, "_t", None)
                if t is not None:
                    await t

        store = EventStore(Path(tmp) / f"ev-{uuid.uuid4().hex}.db")
        gate = WakeGate(enabled=True, word="candan", window=15.0)
        io = TurnIO(gate)
        d = Deliverer(store, io, reply_timeout=1.0, retry_after=300.0, late_hours=12.0,
                      now_fn=lambda: T0)
        eid = store.add("reminder", "ayhan", "saat üçte doktor randevun var",
                        due_ts=T0 - 60, now=T0 - 120)
        n = await d.tick("ayhan")
        await io.spoke_out()
        ev = store.get(eid)
        ok = (n == 1 and any("doktor randevun" in s for s in io.heard)   # DUYULDU
              and io.pi_turns == []                                      # pi'ya düşmedi
              and ev.status == "delivered")
        results.append(("(h3) onay TURU BİTİRİNCE hatırlatma KESİLMİYOR + onay cümlesi "
                        "pi'ya DÜŞMÜYOR (canlı bug, 2. tur)", ok,
                        f"heard={io.heard} pi_turns={io.pi_turns} status={ev.status}"))
        # (h3b) sebebi: HATIRLATMA kesilemez söyleniyor; SESLENME kesilebilir kalıyor
        # (kesilemez sözde livekit STT'ye sessizlik besler → onay hiç transkript olmazdı).
        ok = io.interruptible == [True, False]
        results.append(("(h3b) seslenme kesilebilir, HATIRLATMA kesilemez", ok,
                        f"interruptible={io.interruptible}"))

        # (g) profile 2KB'ı aşınca → konsolidasyon TETİKLENİYOR (sessizken; busy'de ASLA)
        MEM = Path(tmp) / "memory"
        (MEM / "users" / "ayhan" / "notes").mkdir(parents=True, exist_ok=True)
        (MEM / "policy.json").write_text('{"ayhan": "adult"}')
        globals()["MEMORY_DIR"] = str(MEM)
        prof = MEM / "users" / "ayhan" / "profile.md"
        prof.write_text("# Profil\n" + "".join(
            f"- [2026-07-{(i % 28) + 1:02d}] Olay {i}: uzun bir gün özeti satırı.\n"
            for i in range(60)))
        size_before = prof.stat().st_size

        brain = PiBrain(session_id="ayhan")
        captured: list[str] = []

        async def fake_turn(prompt: str, timeout: float = 30.0) -> bool:
            captured.append(prompt)               # pi'ya giden sessiz tur (LLM YOK)
            return True

        brain._silent_turn = fake_turn            # type: ignore[assignment]
        brain._wake.enabled = True
        brain._wake.awake = False                 # kullanıcı sessiz / uyku → uygun an
        which = await brain.consolidate_if_needed(now=T0)
        ok = (size_before > MEM_CONTEXT_LIMIT and which == "profile" and len(captured) == 1
              and "memory_consolidate" in captured[0] and "Olay 59" in captured[0])
        results.append((f"(g) profile {size_before}B > {MEM_CONTEXT_LIMIT}B → konsolidasyon "
                        f"turu AÇILDI (içerik prompt'ta)", ok,
                        f"which={which} prompt_len={len(captured[0]) if captured else 0}"))

        # (g2) konuşma sürerken ASLA konsolide etme + günde 1 kez
        brain2 = PiBrain(session_id="ayhan")
        c2: list[str] = []
        brain2._silent_turn = (lambda p, timeout=30.0: c2.append(p) or True)  # type: ignore
        brain2._wake.set_user_speaking(True)      # busy
        w = await brain2.consolidate_if_needed(now=T0)
        busy_skip = (w is None and not c2)
        brain2._wake.set_user_speaking(False)
        brain2._wake.awake = True                 # konuşma penceresi açık → yine bekle
        w = await brain2.consolidate_if_needed(now=T0)
        awake_skip = (w is None and not c2)
        again = await brain.consolidate_if_needed(now=T0 + 60)   # aynı gün 2. kez → HAYIR
        ok = busy_skip and awake_skip and again is None and len(captured) == 1
        results.append(("(g2) busy/uyanıkken konsolidasyon YOK; günde en fazla 1 kez", ok,
                        f"busy_skip={busy_skip} awake_skip={awake_skip} again={again}"))

    try:
        asyncio.run(run())
    finally:
        globals()["MEMORY_DIR"] = old_mem
        os.environ.clear()
        os.environ.update(old_env)
        shutil.rmtree(tmp, ignore_errors=True)

    # (a) + kayıpsız konsolidasyon mekanizması: events.ts selftest (gerçek TZ aritmetiği)
    print("[proactive] events.ts selftest (zaman + events.db + konsolidasyon):")
    ts = subprocess.run(
        ["node", "--experimental-strip-types",
         str(REPO_ROOT / "pi" / "extensions" / "family-memory" / "events.ts"), "selftest"],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    ts_out = "\n".join(l for l in ts.stdout.splitlines() if l.strip())
    print(ts_out)
    ts_ok = ts.returncode == 0

    print(f"[proactive] geçici kök: {tmp} (silindi)")
    all_ok = ts_ok
    for name, ok, detail in results:
        all_ok = all_ok and ok
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  [{detail}]")
    print(f"[proactive] RESULT: {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


async def _reminder_e2e() -> int:
    """UÇTAN UCA (GERÇEK pi turu, token harcar): "10 dakika sonra ... hatırlat" →
    reminder_add FIRE ediyor mu, due_at DOĞRU mu? GEÇİCİ memory kökü kullanır —
    gerçek memory/ KİRLENMEZ."""
    global MEMORY_DIR  # noqa: PLW0602 — atama globals()[..] ile yapılıyor; bu satır
    # gereksiz DEĞİL: ileride düz `MEMORY_DIR = x` yazılırsa yerelleşmesini önler.
    import shutil
    import tempfile

    from reminders import EventStore

    tmp = Path(tempfile.mkdtemp(prefix="candan-e2e-"))
    mem = tmp / "memory"
    (mem / "users" / "ayhan" / "notes").mkdir(parents=True, exist_ok=True)
    (mem / "policy.json").write_text('{"ayhan": "adult"}')
    old_mem = MEMORY_DIR
    globals()["MEMORY_DIR"] = str(mem)
    os.environ["MEM_DIR"] = str(mem)                       # extension'ın kökü
    os.environ["EVENTS_DB"] = str(mem / "events.db")       # olay deposu (izole)

    client = PiRpcClient(PI_DEFAULT_PERSONA, "ayhan")
    brain_now = None
    try:
        from zoneinfo import ZoneInfo
        brain_now = datetime.now(ZoneInfo(CANDAN_TZ))
    except Exception:  # noqa: BLE001
        brain_now = datetime.now()
    t0 = time.time()
    prompt = ("(Sistem: şu an " + brain_now.strftime("%d.%m.%Y %H:%M") + f" [{CANDAN_TZ}].)\n\n"
              "bana 10 dakika sonra su içmemi hatırlat")
    print(f"[e2e] MEM_DIR={mem}\n[e2e] prompt={prompt!r}")
    await client.start()
    q: asyncio.Queue = asyncio.Queue()
    client._turn_q = q
    tools: list[str] = []
    text = ""
    try:
        await client.send({"type": "prompt", "message": prompt})
        while True:
            obj = await asyncio.wait_for(q.get(), timeout=120.0)
            if obj is None:
                break
            t = obj.get("type")
            if t and "tool" in t:
                name = (obj.get("toolCall") or obj.get("tool") or {})
                nm = name.get("name") if isinstance(name, dict) else None
                if nm:
                    tools.append(nm)
            if t == "message_update":
                ame = obj.get("assistantMessageEvent") or {}
                if ame.get("type") == "text_delta":
                    text += ame.get("delta") or ""
            elif t == "agent_settled":
                break
    finally:
        await client.stop()

    store = EventStore(mem / "events.db")
    rows = store.due("ayhan", now=time.time() + 3600)   # 1 saat sonrası → 10dk'lık görünür
    ok = False
    detail = "kayıt YOK"
    if rows:
        ev = rows[0]
        delta_min = (ev.due_ts - t0) / 60.0
        ok = 9.0 <= delta_min <= 11.5                   # ~10 dakika (model/tur gecikmesi payı)
        detail = (f"text={ev.text!r} requested={ev.requested_at} due={ev.due_at} "
                  f"(+{delta_min:.1f} dk) status={ev.status}")
    store.close()
    print(f"[e2e] tool çağrıları: {tools}")
    print(f"[e2e] asistan: {text.strip()!r}")
    print(f"[e2e] events.db: {detail}")
    print(f"[e2e] RESULT: {'PASS' if ok else 'FAIL'}")

    globals()["MEMORY_DIR"] = old_mem
    os.environ.pop("MEM_DIR", None)
    os.environ.pop("EVENTS_DB", None)
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if ok else 1


def _soul_test() -> int:
    """soul.md yükleme dalları — GEÇİCİ memory kökü (gerçek memory/ KİRLENMEZ).

    _build_pi_args'ın system-prompt argümanlarında soul dosyaları doğru mu:
      (a) hiç soul yok → ne ortak ne kişisel yüklenir (bugünkü davranış)
      (b) ortak memory/soul.md → HERKESE yüklenir (tanınan + guest)
      (c) kişisel memory/users/<u>/soul.md → SADECE tanınan kullanıcıya; guest'e YOK
      (d) ikisi de → ortak ÖNCE, kişisel SONRA (sonra = öncelikli)."""
    import shutil
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="candan-soul-test-"))
    old_mem = MEMORY_DIR
    MEM = tmp / "memory"
    (MEM / "users" / "ayhan").mkdir(parents=True, exist_ok=True)
    (MEM / "policy.json").write_text('{"ayhan": "adult"}')
    globals()["MEMORY_DIR"] = str(MEM)
    results: list[tuple[str, bool, str]] = []

    def souls(session_id: str) -> list[str]:
        """_build_pi_args çıktısındaki --append-system-prompt değerlerinden soul yolları."""
        args = _build_pi_args("candan", session_id)
        vals = [args[i + 1] for i, a in enumerate(args) if a == "--append-system-prompt"
                and i + 1 < len(args)]
        return [v for v in vals if v.endswith("soul.md")]

    common = str(MEM / "soul.md")
    personal = str(MEM / "users" / "ayhan" / "soul.md")

    try:
        # (a) hiç soul yok
        ok = souls("ayhan") == [] and souls("candan") == []
        results.append(("(a) soul yok → hiçbir soul yüklenmez", ok, f"{souls('ayhan')}"))

        # (b) ortak soul → tanınan + guest ikisine de
        (MEM / "soul.md").write_text("- [2026-07-14] kısa konuş\n")
        ok = souls("ayhan") == [common] and souls("candan") == [common]
        results.append(("(b) ortak soul → tanınan + guest'e yüklendi", ok,
                        f"ayhan={souls('ayhan')} guest={souls('candan')}"))

        # (c)+(d) kişisel soul → tanınana eklenir (ortaktan SONRA); guest'e GİRMEZ
        Path(personal).write_text("- [2026-07-14] bana Ayhan Bey de\n")
        got_user = souls("ayhan")
        got_guest = souls("candan")
        ok = (got_user == [common, personal]     # sıra: ortak ÖNCE, kişisel SONRA
              and got_guest == [common])          # guest kişiseli ALMAZ
        results.append(("(c/d) kişisel soul: tanınana ortaktan SONRA; guest'e YOK", ok,
                        f"user={got_user} guest={got_guest}"))
    finally:
        globals()["MEMORY_DIR"] = old_mem
        shutil.rmtree(tmp, ignore_errors=True)

    all_ok = True
    for name, ok, detail in results:
        all_ok = all_ok and ok
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  [{detail}]")
    print(f"[soul] RESULT: {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


def _name_test() -> int:
    """parse_spoken_name birim testi — gerçek Türkçe konuşma + reddedilecekler.

    Canlı hata (16:53): evin annesi "Havi adım. Az önce kocam sana söyledi..."
    dedi, parser None döndü → guest → aile hafızası açılmadı. Aynı parser
    "Efendim"/"Anlamadım" gibi cevapları İSİM sanıyordu. Hem katı hem geçirgendi.
    Sherpa/model/token GEREKMEZ — saf fonksiyon testi."""
    cases: list[tuple[str, Optional[str]]] = [
        # ── isim ÇIKARILMALI (doğal konuşma) ──
        ("Havi adım", "Havi"),                       # canlı hata: "X adım" biçimi
        ("Havva ben", "Havva"),
        ("Adım Havva", "Havva"),
        ("Adım Havva, evin annesiyim", "Havva"),
        ("Ben Havva, Ayhan'ın eşiyim", "Havva"),
        ("Havva adım. Az önce kocam sana söyledi benim kim olduğumu. Evin annesiyim.",
         "Havva"),                                   # canlı hata, birebir
        ("Havva'yım", "Havva"),                      # koşaç eki + kesme
        ("Havvayım", "Havva"),                       # koşaç eki, kesmesiz
        ("Ben Havva'yım", "Havva"),
        ("Zeynep'im", "Zeynep"),
        ("Benim adım Ayhan", "Ayhan"),
        ("Ayhan", "Ayhan"),
        ("Ayhan Karakuş", "Ayhan Karakuş"),
        ("İsmim Zeynep", "Zeynep"),
        ("Bana Zeynep de", "Zeynep"),
        ("Havva diyebilirsin", "Havva"),
        ("Ben Ayşe, kızıyım", "Ayşe"),
        ("Adım Mehmet ama bana Memo derler", "Mehmet"),
        ("Ben Ayhan.", "Ayhan"),
        ("Ben evin annesiyim, adım Havva", "Havva"),
        ("my name is Sarah", "Sarah"),
        ("I'm John", "John"),
        # ── REDDEDİLMELİ (eskiden İSİM sanılıyordu → yanlış kişi kaydı) ──
        ("Efendim", None),
        ("Anlamadım", None),
        ("Ne dedin", None),
        ("Bilmiyorum", None),
        ("Hava nasıl", None),
        ("Tamam", None),
        ("Bir dakika", None),
        ("Ne diyorsun sen", None),
        ("Adını anlayamadım", None),
        ("Söylemek istemiyorum", None),
        # ── ECHO: asistanın KENDİ sözü mikrofona girerse isim sanılmamalı ──
        ("Adını anlayamadım. Abi.", None),           # canlı hata: birebir echo
        ("Seni tanıyamadım, adını söyler misin?", None),
        ("Sadece adını söyler misin?", None),
    ]
    all_ok = True
    for text, expected in cases:
        got = parse_spoken_name(text)
        ok = got == expected
        all_ok = all_ok and ok
        print(f"  {'PASS' if ok else 'FAIL'}  {text!r} → {got!r} (beklenen {expected!r})")
    print(f"[name] RESULT: {'PASS' if all_ok else 'FAIL'}  ({len(cases)} örnek)")
    return 0 if all_ok else 1


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "name":
        raise SystemExit(_name_test())
    if cmd == "soul":
        raise SystemExit(_soul_test())
    if cmd == "proactive":
        raise SystemExit(_proactive_test())
    if cmd == "e2e":
        raise SystemExit(asyncio.run(_reminder_e2e()))
    if cmd == "policy":
        raise SystemExit(_policy_test())
    if cmd == "waketimer":
        raise SystemExit(_wake_timer_test())
    if cmd == "wake":
        raise SystemExit(_wake_test())
    if cmd == "reset":
        raise SystemExit(_reset_test())
    if cmd == "compaction":
        raise SystemExit(_compaction_test())
    if cmd == "rotate":
        raise SystemExit(_rotate_test())
    if cmd == "smoke":
        raise SystemExit(asyncio.run(_smoke()))
    if cmd == "prompt":
        msg = sys.argv[2] if len(sys.argv) > 2 else "merhaba de"
        raise SystemExit(asyncio.run(_prompt_test(msg)))
    print("usage: python pi_brain.py "
          "[smoke|prompt <text>|wake|waketimer|reset|compaction|rotate|policy|"
          "proactive|soul|e2e|name]")
