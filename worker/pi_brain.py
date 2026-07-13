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
# Tur stall watchdog: son ilerlemeden (text_delta / başlangıç) bu kadar saniye HİÇ
# olay gelmezse turu temiz kapat (WebSocket 1000 gibi ~33-40s takılmalara karşı).
PI_TURN_STALL_TIMEOUT = float(os.environ.get("PI_TURN_STALL_TIMEOUT", "12") or 12)
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
PI_TOOLS_ALLOWLIST = os.environ.get(
    "PI_TOOLS_ALLOWLIST",
    "memory_add,memory_search,web_search,"
    "reminder_add,reminder_list,reminder_cancel,memory_consolidate",
)

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


def _build_pi_args(persona: str, session_id: str) -> list[str]:
    """pi --mode rpc bayrakları (docs/pi-brain-design.md)."""
    args = [PI_BIN, "--mode", "rpc", "--approve", "--model", PI_MODEL]
    # İzolasyon: global (~/.pi/agent) extension/skill/prompt/theme/context keşfini kapat.
    # Aşağıdaki explicit `-e` / `--skill` / `--append-system-prompt` yolları etkilenmez.
    if PI_ISOLATED:
        args += ["--no-extensions", "--no-skills", "--no-prompt-templates",
                 "--no-themes", "--no-context-files"]
    # Gecikme: thinking seviyesi (minimal en hızlı). Boş/"default" → pi varsayılanı.
    if PI_THINKING and PI_THINKING.lower() != "default":
        args += ["--thinking", PI_THINKING]
    # Tool politikası: built-in'leri (read/edit/bash/grep/web_search…) kapat; lokal mem
    # extension'ı (memory_add/memory_search) yaşasın. İsteğe bağlı allowlist ile tek tek
    # tool geri açılabilir (ör. web_search).
    if PI_NO_BUILTIN_TOOLS:
        args += ["--no-builtin-tools"]
    allowlist = ",".join(t.strip() for t in PI_TOOLS_ALLOWLIST.split(",") if t.strip())
    if allowlist:
        args += ["--tools", allowlist]
    # Ortak taban + kişilik overlay'i sistem prompt'una ekle.
    agents_md = REPO_ROOT / PI_AGENTS_MD
    if agents_md.is_file():
        args += ["--append-system-prompt", str(agents_md)]
    persona_file = REPO_ROOT / PI_PERSONA_DIR / f"{persona}.md"
    if persona_file.is_file():
        args += ["--append-system-prompt", str(persona_file)]
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
    mem_ext = REPO_ROOT / "pi" / "extensions" / "family-memory" / "index.ts"
    if mem_ext.is_file():
        args += ["-e", str(mem_ext)]
    # web_search: LOKAL extension (anahtarsız Qwant). `web_search` pi'nin built-in'i
    # DEĞİL — global npm:pi-web-access'ten geliyordu; PI_ISOLATED onu kapattığı için
    # yetenek kaybolmuştu. Projeye-lokal → VPS'te de çalışır (global pi gerekmez).
    # `-e` explicit yol olduğu için --no-extensions bunu BOZMAZ.
    # WEB_SEARCH_ENABLED=false → extension tool'u kaydetmez (allowlist girişi zararsız).
    web_ext = REPO_ROOT / "pi" / "extensions" / "websearch" / "index.ts"
    if web_ext.is_file():
        args += ["-e", str(web_ext)]
    args += ["--session-dir", PI_SESSION_DIR, "--session-id", session_id]
    return args


class PiRpcClient:
    """Kalıcı `pi --mode rpc` alt-süreci. stdin JSON-line yaz, stdout JSON-line oku.

    - `response` tipli satırlar id ile korelasyon için `_pending`'e gider.
    - Diğer tüm satırlar (AgentSessionEvent) aktif turun kuyruğuna (`_turn_q`) gider.
    """

    def __init__(self, persona: str, session_id: str):
        self._args = _build_pi_args(persona, session_id)
        # Alt-sürece geçecek hafıza kimliği (guest → ""). memory-skill $MEM_USER'ı okur.
        self._mem_user = _mem_user(session_id)
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._pending: dict[str, asyncio.Future] = {}
        self._turn_q: Optional[asyncio.Queue] = None
        self._turn_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()

    @property
    def started(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        async with self._start_lock:
            if self.started:
                return
            self._proc = await asyncio.create_subprocess_exec(
                *self._args,
                cwd=str(REPO_ROOT),
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
                    # inaktivite sayacı. PI_TURN_STALL_TIMEOUT boyunca HİÇ olay gelmezse
                    # (WebSocket 1000 gibi ~33-40s takılma) turu temiz kapat.
                    while True:
                        try:
                            obj = await asyncio.wait_for(
                                q.get(), timeout=PI_TURN_STALL_TIMEOUT
                            )
                        except asyncio.TimeoutError:
                            logger.warning(
                                "pi tur stall: %.0fs ilerleme yok → tur kapatılıyor "
                                "(got_delta=%s)", PI_TURN_STALL_TIMEOUT, got_delta,
                            )
                            stalled = True
                            break
                        if obj is None:  # süreç öldü
                            break
                        etype = obj.get("type")
                        if etype == "message_update":
                            ame = obj.get("assistantMessageEvent") or {}
                            if ame.get("type") == "text_delta":
                                delta = ame.get("delta") or ""
                                if delta:
                                    got_delta = True
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
        ):
            super().__init__()
            self._default_persona = persona
            self._persona = persona
            self._session_id = session_id or persona
            # speaker_state: `.current` alanı olan paylaşılan durum (None = kapalı).
            # Kapalıyken davranış Faz 2 ile AYNI: tek persona, tek warm süreç.
            self._speaker_state = speaker_state
            self._client = PiRpcClient(self._persona, self._session_id)
            self._swap_lock = asyncio.Lock()
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
            self._enroll_retried = False                  # isim bir kez tekrar soruldu mu
            self._enroll_match: Optional[str] = None      # sese benzeyen mevcut kişi
            self._onboarding_asked = False                # bu bağlantıda soruldu mu
            self._greeted: set[str] = set()               # ismiyle selamlanan kişiler
            self._enroll_lock = asyncio.Lock()
            # Wake word gate (konuşma penceresi). Kapalıysa gate yok (mevcut davranış).
            self._wake = WakeGate()
            self._wake_task: Optional[asyncio.Task] = None
            # Konsolidasyon: dosya başına son çalıştırma (günde en çok 1 → LLM turu yakma).
            self._consolidated: dict[str, float] = {}

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
            self._enroll_retried = False
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
                if not self._enroll_retried:
                    self._enroll_retried = True
                    return "Adını anlayamadım, tekrar söyler misin?"
                # İkinci kez de anlaşılmadı → vazgeç, sözü normal akışa bırak.
                self._reset_enroll()
                logger.info("enrollment: isim anlaşılamadı (2. kez) → guest")
                return None
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
            slug = _slug(name) if name else ""
            if not slug:
                return self._default_persona, self._default_persona
            persona = slug if _persona_exists(slug) else self._default_persona
            return persona, slug  # session hep kişiye özel (memory ayrışsın)

        async def _current_client(self) -> "PiRpcClient":
            """Turluk çözüm: konuşmacı değiştiyse warm pi sürecini swap et; aynıysa
            mevcut warm süreci koru (her tur spawn etme)."""
            if self._speaker_state is None:
                return self._client
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
                self._client = PiRpcClient(persona, session_id)
                await self._client.start()
                await old.stop()
                return self._client

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

    from reminders import Deliverer, EventStore

    results: list[tuple[str, bool, str]] = []
    tmp = tempfile.mkdtemp(prefix="candan-proactive-")
    old_mem, old_env = MEMORY_DIR, dict(os.environ)
    T0 = 1_000_000.0  # sahte "şimdi" (monotonic değil; Deliverer'a enjekte edilir)

    class FakeIO:
        """ProactiveIO sahtesi. GERÇEK WakeGate kullanır → uyku/kesme etkileşimi de test edilir."""

        def __init__(self, gate: WakeGate):
            self.gate = gate
            self.said: list[str] = []
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

        async def say(self, text: str) -> None:
            self.said.append(text)

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


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
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
    if cmd == "smoke":
        raise SystemExit(asyncio.run(_smoke()))
    if cmd == "prompt":
        msg = sys.argv[2] if len(sys.argv) > 2 else "merhaba de"
        raise SystemExit(asyncio.run(_prompt_test(msg)))
    print("usage: python pi_brain.py "
          "[smoke|prompt <text>|wake|waketimer|policy|proactive|e2e]")
