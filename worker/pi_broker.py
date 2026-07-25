"""LiveKit job'larından bağımsız, sıcak Pi RPC süreçleri için Unix-socket broker.

Her LiveKit job'u kısa ömürlüdür; bu servis ise systemd altında yaşamaya devam eder.
Bir job bittiğinde socket bağlantısı kapanır, fakat Pi alt-süreci ve onun yüklediği
extension'lar korunur. Aynı persona/oturum yeniden bağlandığında doğrudan o sıcak
sürece bağlanır.

Protokol deliberately küçüktür:
  client -> {"type":"broker_connect", persona, session_id, model, thinking, dev}
  broker -> {"type":"broker_ready"}
  sonra iki yönde ham `pi --mode rpc` JSON-lines akar.

`python pi_broker.py --reload` tüm Pi alt-süreçlerini kontrollü öldürür. Sonraki
bağlantı güncel extension koduyla taze süreç açar; broker kendisi ayakta kalır.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Broker da agent gibi worker/.env'i bizzat yükler. systemd EnvironmentFile
# kullanılmıyor; boşluk içeren Türkçe değerler için güvenilir yol bu.
load_dotenv(Path(__file__).resolve().parent / ".env")

from pi_brain import (
    DEV_WORKTREE,
    PI_BROKER_SOCKET,
    REPO_ROOT,
    _build_pi_args,
    _mem_user,
    resolve_brain,
)


log = logging.getLogger("pi_broker")
_SAFE_PART = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")


@dataclass(frozen=True)
class BrokerKey:
    persona: str
    session_id: str
    model: str
    thinking: str
    dev: bool

    @property
    def label(self) -> str:
        return f"{self.persona}/{self.session_id} ({self.model})"


class PiProcess:
    """Bir key için broker'ın sahip olduğu tek Pi alt-süreci."""

    def __init__(self, key: BrokerKey):
        self.key = key
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._stdout_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._client: Optional[asyncio.StreamWriter] = None
        self._state_lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    @property
    def busy(self) -> bool:
        return self._client is not None and not self._client.is_closing()

    async def start(self) -> None:
        async with self._state_lock:
            if self.running:
                return
            args = _build_pi_args(
                self.key.persona,
                self.key.session_id,
                self.key.model,
                self.key.thinking,
                dev=self.key.dev,
            )
            cwd = DEV_WORKTREE if self.key.dev else REPO_ROOT
            # Dev modundaki çalışma dizini _build_pi_args tarafından session için
            # sabitlenir; burada da mevcut işlevdeki varsayılanı koruyoruz.
            self.proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=str(cwd),
                env={**os.environ, "MEM_USER": "" if self.key.dev else _mem_user(self.key.session_id)},
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._stdout_task = asyncio.create_task(self._pump_stdout())
            self._stderr_task = asyncio.create_task(self._pump_stderr())
            log.info("pi açıldı: %s (pid=%s)", self.key.label, self.proc.pid)

    async def attach(self, writer: asyncio.StreamWriter) -> bool:
        async with self._state_lock:
            if self.busy:
                return False
            self._client = writer
            return True

    async def detach(self, writer: asyncio.StreamWriter) -> None:
        async with self._state_lock:
            if self._client is writer:
                self._client = None

    async def send_raw(self, raw: bytes) -> None:
        proc = self.proc
        if proc is None or proc.stdin is None or proc.returncode is not None:
            raise RuntimeError("pi süreci çalışmıyor")
        proc.stdin.write(raw)
        await proc.stdin.drain()

    async def _pump_stdout(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        try:
            while line := await self.proc.stdout.readline():
                client = self._client
                if client is None or client.is_closing():
                    # Kullanıcı ayrılmış olabilir; satırı tüket ama Pi'yi öldürme.
                    continue
                try:
                    client.write(line)
                    await client.drain()
                except (ConnectionError, OSError):
                    await self.detach(client)
        finally:
            client = self._client
            if client is not None and not client.is_closing():
                with contextlib.suppress(Exception):
                    client.write(b'{"type":"broker_error","error":"pi process exited"}\n')
                    await client.drain()
            log.warning("pi kapandı: %s", self.key.label)

    async def _pump_stderr(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        while line := await self.proc.stderr.readline():
            text = line.decode(errors="replace").strip()
            if text:
                log.info("pi[%s]: %s", self.key.session_id, text)

    async def stop(self, *, reloaded: bool = False) -> None:
        async with self._state_lock:
            proc = self.proc
            client = self._client
            self._client = None
            if client is not None and not client.is_closing():
                kind = "broker_reloaded" if reloaded else "broker_error"
                with contextlib.suppress(Exception):
                    client.write((json.dumps({"type": kind}) + "\n").encode())
                    await client.drain()
            if proc is None:
                return
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
            for task in (self._stdout_task, self._stderr_task):
                if task is not None:
                    task.cancel()
            self.proc = None


class PiBroker:
    def __init__(self, socket_path: Path):
        self.socket_path = socket_path
        self._processes: dict[BrokerKey, PiProcess] = {}
        self._lock = asyncio.Lock()
        self._server: Optional[asyncio.AbstractServer] = None

    async def start(self) -> None:
        if self.socket_path.exists():
            mode = self.socket_path.stat().st_mode
            if not stat.S_ISSOCK(mode):
                raise RuntimeError(f"broker socket yolu normal dosya: {self.socket_path}")
            self.socket_path.unlink()
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._server = await asyncio.start_unix_server(self._handle_client, path=str(self.socket_path))
        os.chmod(self.socket_path, 0o660)
        log.info("pi broker hazır: %s", self.socket_path)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        await self.reload()
        if self.socket_path.exists() and stat.S_ISSOCK(self.socket_path.stat().st_mode):
            self.socket_path.unlink()

    async def process_for(self, key: BrokerKey) -> PiProcess:
        async with self._lock:
            process = self._processes.get(key)
            if process is None:
                process = PiProcess(key)
                self._processes[key] = process
        await process.start()
        return process

    async def reload(self) -> None:
        async with self._lock:
            processes = list(self._processes.values())
            self._processes.clear()
        await asyncio.gather(*(p.stop(reloaded=True) for p in processes), return_exceptions=True)
        log.info("pi broker reload tamamlandı (%d süreç)", len(processes))

    async def prewarm_defaults(self) -> None:
        """Servis açılışı/reload sonrası varsayılan Pi süreçlerini tekrar sıcak tut."""
        for key in _prewarm_specs():
            try:
                await self.process_for(key)
            except Exception:  # noqa: BLE001 — tek prewarm servisi düşürmesin
                log.exception("prewarm başarısız: %s", key.label)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        process: Optional[PiProcess] = None
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=10.0)
            hello = json.loads(raw) if raw else {}
            if hello.get("type") == "broker_admin":
                if hello.get("action") != "reload":
                    raise RuntimeError("bilinmeyen broker yönetim komutu")
                await self.reload()
                await self.prewarm_defaults()
                writer.write(b'{"type":"broker_reloaded"}\n')
                await writer.drain()
                return
            key = _key_from_hello(hello)
            process = await self.process_for(key)
            if not await process.attach(writer):
                writer.write(
                    json.dumps(
                        {"type": "broker_error", "error": "pi oturumu zaten kullanımda"}
                    ).encode()
                    + b"\n"
                )
                await writer.drain()
                return
            writer.write((json.dumps({"type": "broker_ready", "key": key.label}) + "\n").encode())
            await writer.drain()
            while raw := await reader.readline():
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("type") == "broker_release":
                    break
                if msg.get("type") == "broker_discard":
                    # `yeni sohbet` yalnız client bağlantısını değil Pi'nin kendi
                    # RAM geçmişini de sıfırlamak ister. Süreç hemen ölür; aynı key
                    # ile bir sonraki bağlantı onu güncel session dosyasından taze açar.
                    await process.stop()
                    break
                await process.send_raw(raw)
        except Exception as exc:  # noqa: BLE001 — broker tek client hatasıyla ölmesin
            log.warning("broker client hatası: %s", exc)
            if not writer.is_closing():
                with contextlib.suppress(Exception):
                    writer.write((json.dumps({"type": "broker_error", "error": str(exc)}) + "\n").encode())
                    await writer.drain()
        finally:
            if process is not None:
                await process.detach(writer)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


def _key_from_hello(hello: dict) -> BrokerKey:
    if hello.get("type") != "broker_connect":
        raise RuntimeError("broker_connect bekleniyordu")
    persona = str(hello.get("persona") or "")
    session_id = str(hello.get("session_id") or "")
    model = str(hello.get("model") or "")
    thinking = str(hello.get("thinking") or "")
    # Persona/session dosya isimlerine gider; model de process anahtarıdır. Sadece
    # sade parçalar kabul ederek Unix socket'i yanlışlıkla genel process launcher'a
    # dönüştürmeyiz. Model adı slash içerebilir, fakat shell'e ASLA verilmez.
    if not _SAFE_PART.fullmatch(persona) or not _SAFE_PART.fullmatch(session_id):
        raise RuntimeError("geçersiz persona veya session_id")
    if not model or len(model) > 200 or "\x00" in model:
        raise RuntimeError("geçersiz model")
    if thinking and (len(thinking) > 64 or "\x00" in thinking):
        raise RuntimeError("geçersiz thinking")
    return BrokerKey(persona, session_id, model, thinking, bool(hello.get("dev")))


def _prewarm_specs() -> list[BrokerKey]:
    """`PI_BROKER_PREWARM=local:candan:candan,...` biçimini çöz.

    Aynı Pi süreci ilk bağlantıdan önce doğar. Prompt gönderilmez; amaç pi'nin
    kendisini, extension'larını ve oturum dosyasını önceden açmaktır.
    """
    specs = os.environ.get("PI_BROKER_PREWARM", "local:candan:candan")
    out: list[BrokerKey] = []
    for spec in specs.split(","):
        if not spec.strip():
            continue
        try:
            brain, persona, session_id = (part.strip() for part in spec.split(":", 2))
            model, thinking = resolve_brain(brain)
            out.append(BrokerKey(persona, session_id, model, thinking, False))
        except ValueError:
            log.warning("geçersiz PI_BROKER_PREWARM girdisi: %r", spec)
    return out


async def _run(socket_path: Path) -> None:
    broker = PiBroker(socket_path)
    await broker.start()
    await broker.prewarm_defaults()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in ("SIGINT", "SIGTERM"):
        signal = getattr(__import__("signal"), signal_name)
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signal, stop.set)
    await stop.wait()
    await broker.close()


async def _request_reload(socket_path: Path) -> int:
    try:
        reader, writer = await asyncio.open_unix_connection(str(socket_path))
        writer.write(b'{"type":"broker_admin","action":"reload"}\n')
        await writer.drain()
        reply = json.loads((await asyncio.wait_for(reader.readline(), timeout=15)).decode())
        writer.close()
        await writer.wait_closed()
    except Exception as exc:  # noqa: BLE001
        print(f"pi broker reload başarısız: {exc}")
        return 1
    if reply.get("type") != "broker_reloaded":
        print(f"pi broker reload başarısız: {reply.get('error') or reply}")
        return 1
    print("pi broker reload tamamlandı; sonraki tur güncel extension'larla başlayacak.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", default=PI_BROKER_SOCKET or "/run/candan/pi-broker.sock")
    parser.add_argument("--reload", action="store_true", help="yaşayan Pi süreçlerini yenile")
    args = parser.parse_args()
    logging.basicConfig(level=os.environ.get("PI_BROKER_LOG_LEVEL", "INFO"))
    socket_path = Path(args.socket)
    if args.reload:
        return asyncio.run(_request_reload(socket_path))
    try:
        asyncio.run(_run(socket_path))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
