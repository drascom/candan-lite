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
import uuid
from pathlib import Path
from typing import Any, Optional

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
            self._client = pi_llm._client

        async def _run(self) -> None:
            await self._client.start()
            text = _last_user_text(self._chat_ctx)
            if not text:
                return
            turn_id = uuid.uuid4().hex
            q: asyncio.Queue = asyncio.Queue()

            def _emit(content: str) -> None:
                self._event_ch.send_nowait(
                    llm.ChatChunk(
                        id=turn_id,
                        delta=llm.ChoiceDelta(role="assistant", content=content),
                    )
                )

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
        ):
            super().__init__()
            self._persona = persona
            self._session_id = session_id or persona
            self._client = PiRpcClient(persona, self._session_id)

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
