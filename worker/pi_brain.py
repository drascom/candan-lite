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
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from name_parser import (
    parse_spoken_name,
    is_affirmative_reply,
    _is_decline_enroll,
)

logger = logging.getLogger("pi_brain")

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


def _slug(name: str) -> str:
    """İsmi dosya/oturum-güvenli slug'a çevir (persona dosyası + session-id için)."""
    s = "".join(c if (c.isalnum() or c in "-_") else "-" for c in (name or "").strip().lower())
    return "-".join(p for p in s.split("-") if p) or ""


def _persona_exists(persona: str) -> bool:
    return (REPO_ROOT / PI_PERSONA_DIR / f"{persona}.md").is_file()


def _build_pi_args(persona: str, session_id: str) -> list[str]:
    """pi --mode rpc bayrakları (docs/pi-brain-design.md)."""
    args = [PI_BIN, "--mode", "rpc", "--approve", "--model", PI_MODEL]
    # Ortak taban + kişilik overlay'i sistem prompt'una ekle.
    agents_md = REPO_ROOT / PI_AGENTS_MD
    if agents_md.is_file():
        args += ["--append-system-prompt", str(agents_md)]
    persona_file = REPO_ROOT / PI_PERSONA_DIR / f"{persona}.md"
    if persona_file.is_file():
        args += ["--append-system-prompt", str(persona_file)]
    skills = REPO_ROOT / PI_SKILLS_DIR
    if skills.exists():
        args += ["--skill", str(skills)]
    args += ["--session-dir", PI_SESSION_DIR, "--session-id", session_id]
    return args


class PiRpcClient:
    """Kalıcı `pi --mode rpc` alt-süreci. stdin JSON-line yaz, stdout JSON-line oku.

    - `response` tipli satırlar id ile korelasyon için `_pending`'e gider.
    - Diğer tüm satırlar (AgentSessionEvent) aktif turun kuyruğuna (`_turn_q`) gider.
    """

    def __init__(self, persona: str, session_id: str):
        self._args = _build_pi_args(persona, session_id)
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

            # Faz 3.1: sesli oto-enrollment. Bilinmeyen ses / akış ortası →
            # scripted TR satır döndür ve pi'ya GİTME (token harcanmaz). Kapalı /
            # tanınan / akışa girmeyen → None döner, normal pi akışı sürer.
            scripted = await self._brain._enrollment_line(text)
            if scripted is not None:
                _emit(scripted)
                return
            # Tanınan kişinin bu bağlantıdaki İLK turu → pi'ya giden mesaja
            # ismiyle-selam direktifi ekle (pi doğal selamlasın).
            text = self._brain._maybe_greet(text)

            q: asyncio.Queue = asyncio.Queue()

            async with self._client._turn_lock:
                self._client._turn_q = q
                aborted = False
                got_delta = False
                final_msg: Any = None  # son assistant mesajı (fallback/hata için)
                try:
                    await self._client.send({"type": "prompt", "message": text})
                    while True:
                        obj = await q.get()
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
                        elif etype == "agent_settled":
                            break
                    # Fallback: hiç delta gelmediyse ama tam-content varsa onu stream et.
                    if not got_delta:
                        full = _assistant_msg_text(final_msg)
                        if full:
                            _emit(full)
                        elif final_msg is not None and final_msg.get("stopReason") == "error":
                            logger.warning(
                                "pi boş yanıt (error): %s",
                                final_msg.get("errorMessage") or "(bilinmiyor)",
                            )
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
            self._enroll_stage: Optional[str] = None      # None | "ask_name" | "confirm"
            self._enroll_name: Optional[str] = None
            self._enroll_emb: Any = None                  # tetikleyen sözün embed'i
            self._enroll_name_emb: Any = None             # ismi söylerkenki embed
            self._enroll_retried = False                  # isim bir kez tekrar soruldu mu
            self._onboarding_asked = False                # bu bağlantıda soruldu mu
            self._greeted: set[str] = set()               # ismiyle selamlanan kişiler
            self._enroll_lock = asyncio.Lock()

        # ── Faz 3.1: sesli oto-enrollment state machine ──────────────────────
        def _reset_enroll(self) -> None:
            self._enroll_stage = None
            self._enroll_name = None
            self._enroll_emb = None
            self._enroll_name_emb = None
            self._enroll_retried = False

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
            """ask_name → confirm → finish akışı. _enroll_lock altında çağrılır."""
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

        async def _finish_enrollment(self) -> str:
            """Onay alındı → kişi oluştur + ses örneklerini yaz + reload + swap."""
            from speaker_id import emb_to_bytes

            name = self._enroll_name or ""
            try:
                rec = await self._speaker_store.create_speaker(name)
                sid = rec["id"]
                mid, dim = self._speaker_id.model_id, self._speaker_id.dim
                for emb in (self._enroll_emb, self._enroll_name_emb):
                    if emb is not None:
                        await self._speaker_store.add_speaker_sample(
                            sid, emb_to_bytes(emb), dim, mid, source="voice-enroll"
                        )
                # Yeni kişi hemen tanınsın: DB'den yeniden yükle.
                self._speaker_id.reload(await self._speaker_store.all_speaker_embeddings())
                # Bu bağlantıda konuşmacı artık bu kişi (sonraki tur persona swap eder).
                self._speaker_state.current = name
                self._greeted.add(name)  # kimliği onayladık → tekrar selam gerekmez
                logger.info("enrollment: %r kaydedildi (id=%s)", name, sid)
            except Exception as e:  # noqa: BLE001
                logger.warning("enrollment başarısız (%s)", e)
                self._reset_enroll()
                return "Şu anda seni kaydedemedim, sonra tekrar deneyelim."
            self._reset_enroll()
            return f"Memnun oldum {name}!"

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


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "smoke":
        raise SystemExit(asyncio.run(_smoke()))
    if cmd == "prompt":
        msg = sys.argv[2] if len(sys.argv) > 2 else "merhaba de"
        raise SystemExit(asyncio.run(_prompt_test(msg)))
    print("usage: python pi_brain.py [smoke|prompt <text>]")
