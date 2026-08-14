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
import hashlib
import json
import logging
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Normal broker agent ile aynı worker/.env'i yükler. Dev broker ise ayrı OS
# kullanıcısıdır ve LiveKit/üretim sırlarını görmemelidir; systemd ona yalnız
# seçilmiş Pi ayarlarını içeren ayrı bir dosya verir.
_broker_env_file = os.environ.get("PI_BROKER_ENV_FILE")
load_dotenv(
    Path(_broker_env_file)
    if _broker_env_file
    else Path(__file__).resolve().parent / ".env"
)

from pi_brain import (
    DEV_WORKTREE,
    PI_BROKER_SOCKET,
    PI_DEV_SESSION_DIR,
    PI_RPC_STREAM_LIMIT,
    PI_SESSION_DIR,
    REPO_ROOT,
    _build_pi_args,
    _envflag,
    _find_session_file,
    pi_mem_env,
    resolve_brain,
)


log = logging.getLogger("pi_broker")
_SAFE_PART = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")

# ── ISITMA (warm-up) ─────────────────────────────────────────────────────────
# Prewarm süreci doğuruyordu ama prompt GÖNDERMİYORDU: pi ayakta, slot ayrık,
# fakat `candan-brain`'in (llama-server, --parallel 1 = TEK slot) KV önbelleği
# BOŞ. İlk gerçek tur 15-30 bin token'lık sistem prompt'u + oturum geçmişini
# sıfırdan prefill ediyor → 9-17 sn (~1120 tok/s ölçüldü, bkz. DEVIR §4).
#
# Isıtma = SICAK sürece kısa bir prompt yollamak. Aynı süreç olmak ZORUNDA:
# önbellek ön eki tam olarak o oturumun geçmişidir; başka bir oturumla ısıtmak
# tek slotu YANLIŞ ön ekle doldurup işi kötüleştirirdi.
#
# ⚠️ Isıtma turu pi'nın geçmişine SIZAMAZ. pi'da "turu geri al" RPC'si yok →
# tur bittikten sonra süreç ÖLDÜRÜLÜR, oturum jsonl'i ısıtma öncesi bayt
# uzunluğuna geri alınır, süreç TEMİZ geçmişle yeniden doğar. llama-server'ın
# KV önbelleği bu sırada dokunulmadan kalır: ısıtılmış ön ek (geçmiş) orada,
# ısıtma kuyruğu (birkaç on token) sıradaki gerçek turda sadece uyuşmayan
# son parça olur. Kazanç ön ekte, kirlilik diskte SIFIR.
_WARMUP_PROMPT_DEFAULT = (
    "Isınma kontrolü. Araç çağırma, hiçbir şey kaydetme; tek kelimeyle yanıtla: tamam."
)


def _warmup_enabled() -> bool:
    return _envflag("PI_BROKER_WARMUP", True)


def _warmup_prompt() -> str:
    return os.environ.get("PI_BROKER_WARMUP_PROMPT") or _WARMUP_PROMPT_DEFAULT


def _warmup_timeout() -> float:
    return float(os.environ.get("PI_BROKER_WARMUP_TIMEOUT") or 60)


def _warmup_interval() -> float:
    """Periyodik ısıtma aralığı (sn). 0/negatif → keepalive KAPALI.

    llama-server KV'yi ZAMANLA düşürmez; önbelleği yabancı bir istek EZER
    (compaction, sınıflandırıcı — DEVIR §4). Yani süre ölçülebilir bir sabit
    değil, emniyet ağı: muhafazakâr TAHMİN olarak 15 dk."""
    raw = os.environ.get("PI_BROKER_WARMUP_INTERVAL")
    return float(raw) if raw not in (None, "") else 900.0


def _probe_timeout() -> float:
    """Kiralamadan önce Pi RPC sağlık sorgusuna verilecek kısa süre."""
    try:
        return max(0.5, float(os.environ.get("PI_BROKER_PROBE_TIMEOUT") or 3.0))
    except ValueError:
        return 3.0


def _session_dir() -> Path:
    """Normal (dev olmayan) oturumların jsonl dizini — `_build_pi_args` ile aynı kural."""
    path = Path(PI_SESSION_DIR)
    return path if path.is_absolute() else REPO_ROOT / path


def _session_entries(
    session_id: str,
    session_dir: Path,
    since: Optional[str] = None,
) -> tuple[list[dict], Optional[str]]:
    """Pi'nin append-only JSONL'inden etkin dalı ve leaf cursor'ını oku.

    Bazı eski/uzun oturumlarda Pi'nin kendi `get_entries` RPC komutu süresiz
    bekleyebiliyor. Broker aynı dosyaya zaten salt-okunur eriştiği için cursor
    sorgusunu model sürecine sokmadan burada cevaplar. Son yazılan entry etkin
    leaf'tir; parentId zincirini geriye izlemek dallanmış geçmişte yalnız etkin
    dalı döndürür.
    """
    path = _find_session_file(session_id, session_dir)
    if path is None:
        return [], None
    ordered: list[dict] = []
    by_id: dict[str, dict] = {}
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            entry_id = str(entry["id"])
            ordered.append(entry)
            by_id[entry_id] = entry
    if not ordered:
        return [], None
    leaf = str(ordered[-1]["id"])
    branch_rev: list[dict] = []
    seen: set[str] = set()
    cursor: Optional[str] = leaf
    while cursor and cursor not in seen:
        seen.add(cursor)
        entry = by_id.get(cursor)
        if entry is None:
            break
        branch_rev.append(entry)
        parent = entry.get("parentId")
        cursor = str(parent) if parent else None
    branch = list(reversed(branch_rev))
    if since is not None:
        for index, entry in enumerate(branch):
            if str(entry.get("id") or "") == since:
                return branch[index + 1 :], leaf
        raise RuntimeError(f"Entry not found: {since}")
    return branch, leaf


@dataclass(frozen=True)
class HistorySnapshot:
    """Isıtma öncesi oturum dosyasının parmak izi (bayt uzunluğu + sha256)."""

    path: Optional[Path]
    size: int
    digest: str

    @property
    def existed(self) -> bool:
        return self.path is not None


def snapshot_history(session_id: str, session_dir: Optional[Path] = None) -> HistorySnapshot:
    directory = session_dir or _session_dir()
    path = _find_session_file(session_id, directory)
    if path is None or not path.is_file():
        return HistorySnapshot(None, 0, "")
    data = path.read_bytes()
    return HistorySnapshot(path, len(data), hashlib.sha256(data).hexdigest())


def restore_history(
    session_id: str, snap: HistorySnapshot, session_dir: Optional[Path] = None
) -> bool:
    """Isıtma turunun geçmişe yazdığını geri al. Döner: geçmiş ısıtma ÖNCESİ hâlinde mi.

    İki yol da kapatılır: pi ısıtma satırlarını ya var olan dosyaya EKLER (→ eski
    bayt uzunluğuna truncate) ya da YENİ bir dosya açar (→ o dosya silinir; eskisi
    zaten dokunulmamıştır). Ön ek beklenmedik şekilde değişmişse (ör. araya
    compaction girdiyse) HİÇBİR ŞEY yapılmaz — gerçek geçmişi bozmak, ısıtma
    kirliliğinden beterdir; yüksek sesle loglanır."""
    directory = session_dir or _session_dir()
    current = _find_session_file(session_id, directory)
    if not snap.existed:
        # Isıtma öncesi geçmiş YOKTU → ısıtmanın doğurduğu dosya tamamen gider.
        if current is not None:
            with contextlib.suppress(OSError):
                current.unlink()
        return True
    if current is not None and current != snap.path:
        with contextlib.suppress(OSError):
            current.unlink()
    path = snap.path
    assert path is not None
    if not path.is_file():
        log.error("ısıtma sonrası oturum dosyası kayıp: %s", path)
        return False
    data = path.read_bytes()
    if len(data) == snap.size and hashlib.sha256(data).hexdigest() == snap.digest:
        return True  # pi hiç yazmadı (ya da ayrı dosyaya yazdı) — iş bitti
    if len(data) > snap.size and hashlib.sha256(data[: snap.size]).hexdigest() == snap.digest:
        with path.open("r+b") as fh:
            fh.truncate(snap.size)
        return True
    log.error("ısıtma turu geri alınamadı, geçmiş beklenmedik şekilde değişti: %s", path)
    return False


@dataclass(frozen=True)
class BrokerKey:
    persona: str
    session_id: str
    model: str
    thinking: str
    dev: bool
    # Dev personasının hafıza kimliği (yalnız dev'de dolar). Anahtarın parçası:
    # hafızası AÇIK bir dev süreci, hafızası KAPALI bir bağlantıyla paylaşılmasın.
    # Normal yolda hep "" → mevcut anahtarlar (prewarm dahil) aynen eşleşir.
    mem_user: str = ""

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
        # Isıtma turu: client YOKKEN stdout buraya akar (çıktı YUTULUR, kimseye
        # gitmez). `_warm_lock` ısıtma ile gerçek kiralamayı sıraya sokar.
        self._warm_q: Optional[asyncio.Queue] = None
        self._warm_lock = asyncio.Lock()
        self._warm_cancel: Optional[asyncio.Event] = None
        self.generation = 0
        self._planned_pids: set[int] = set()

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
            # Hafıza kimliği: normal → session_id'den çözülür (bugünkü davranış).
            # Dev → client'ın çözdüğü dev kimliği (kimlik kapısı worker'da, bkz.
            # pi_brain._dev_mem_user); dev alanı AYRI kök (MEM_DIR) ile gelir.
            mem_user = self.key.mem_user if self.key.dev else None
            args = _build_pi_args(
                self.key.persona,
                self.key.session_id,
                self.key.model,
                self.key.thinking,
                dev=self.key.dev,
                mem_user=mem_user,
            )
            # Pi session header'ı cwd'yi proje kimliğinin parçası sayar. Normal
            # süreci başka dizinde başlatmak mevcut `candan` geçmişini bulamaz;
            # `--no-approve` proje ayar/paketlerini zaten kapattığı için burada
            # repo cwd'sini korumak güvenli ve geçmiş uyumluluğu için zorunludur.
            cwd = DEV_WORKTREE if self.key.dev else REPO_ROOT
            # Dev modundaki çalışma dizini _build_pi_args tarafından session için
            # sabitlenir; burada da mevcut işlevdeki varsayılanı koruyoruz.
            proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=str(cwd),
                env={
                    **os.environ,
                    **pi_mem_env(self.key.session_id, dev=self.key.dev, mem_user=mem_user),
                },
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=PI_RPC_STREAM_LIMIT,
            )
            self.proc = proc
            self.generation += 1
            # Pump görevleri süreci yerel değişkende taşır. `self.proc` yeniden
            # spawn sırasında değişirse eski görev yeni sürecin pipe'ına atlamaz.
            self._stdout_task = asyncio.create_task(self._pump_stdout(proc))
            self._stderr_task = asyncio.create_task(self._pump_stderr(proc))
            log.info(
                "pi açıldı: %s (pid=%s generation=%d)",
                self.key.label,
                proc.pid,
                self.generation,
            )

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

    async def probe(self, timeout: float) -> bool:
        """Model çağırmadan Pi RPC döngüsünün stdin/stdout yanıt verdiğini sınar.

        Uzun süre boşta kalan bir Node/Pi süreci işletim sistemi açısından canlı
        kalabildiği hâlde RPC komutlarını tüketmeyi bırakabiliyor. Böyle bir süreci
        gerçek kullanıcıya kiralamak her turu cursor zaman aşımına sokar. Client henüz
        bağlı değilken `get_state` gönderip eşleşen response'u beklemek bu yarı-canlı
        durumu ucuz ve yan etkisiz biçimde yakalar.
        """
        if self.busy:
            return False
        request_id = f"broker-probe-{os.urandom(8).hex()}"
        q: asyncio.Queue = asyncio.Queue()
        self._warm_q = q
        deadline = asyncio.get_running_loop().time() + timeout
        try:
            await self.send_raw(
                (json.dumps({"type": "get_state", "id": request_id}) + "\n").encode()
            )
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return False
                try:
                    line = await asyncio.wait_for(q.get(), timeout=remaining)
                except asyncio.TimeoutError:
                    return False
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if obj.get("type") == "response" and obj.get("id") == request_id:
                    return bool(obj.get("success"))
        except Exception:  # noqa: BLE001 — çağıran taze süreçle yeniden deneyecek
            return False
        finally:
            self._warm_q = None

    async def _pump_stdout(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None
        try:
            while line := await proc.stdout.readline():
                client = self._client
                if client is None or client.is_closing():
                    # Kullanıcı ayrılmış olabilir; satırı tüket ama Pi'yi öldürme.
                    warm_q = self._warm_q
                    if warm_q is not None:
                        warm_q.put_nowait(line)
                    continue
                try:
                    client.write(line)
                    await client.drain()
                except (ConnectionError, OSError):
                    await self.detach(client)
        finally:
            with contextlib.suppress(Exception):
                await proc.wait()
            current = self.proc is proc
            # Eski sürecin geç kalan finally'si, yeni spawn'a bağlı client'ı
            # düşürmesin. Yalnız hâlâ güncel süreç buysa hata gönder.
            if current:
                client = self._client
                if client is not None and not client.is_closing():
                    with contextlib.suppress(Exception):
                        client.write(
                            (json.dumps({
                                "type": "broker_error",
                                "error": f"pi process exited (code={proc.returncode})",
                            }) + "\n").encode()
                        )
                        await client.drain()
            planned = proc.pid in self._planned_pids
            self._planned_pids.discard(proc.pid)
            log_fn = log.info if planned else log.warning
            log_fn(
                "pi kapandı: %s (pid=%s code=%s current=%s planned=%s)",
                self.key.label, proc.pid, proc.returncode, current, planned,
            )

    async def warm(self, prompt: str, timeout: float, cancel: asyncio.Event) -> bool:
        """Tek ısıtma turu: prompt gönder, `agent_settled`'a kadar çıktıyı YUT.

        Çıktı hiçbir client'a gitmez (client zaten yok — çağıran `busy`yi eledi).
        `cancel` set edilirse (gerçek kullanıcı geldi) tur beklenmeden bırakılır.
        Döner: tur tamamlandı mı. Geçmişin geri alınması ÇAĞIRANIN işi."""
        q: asyncio.Queue = asyncio.Queue()
        self._warm_q = q
        try:
            await self.send_raw((json.dumps({"type": "prompt", "message": prompt}) + "\n").encode())

            async def _drain() -> None:
                while True:
                    line = await q.get()
                    try:
                        obj = json.loads(line)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if isinstance(obj, dict) and obj.get("type") in (
                        "agent_settled",
                        "broker_error",
                    ):
                        return

            drained = asyncio.ensure_future(_drain())
            stopped = asyncio.ensure_future(cancel.wait())
            done, pending = await asyncio.wait(
                {drained, stopped}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            return drained in done
        finally:
            self._warm_q = None

    async def yield_warm(self) -> None:
        """Isıtma sürüyorsa BIRAK: turu kes ve temizliğin bitmesini bekle.

        Gerçek kullanıcı ısıtma yüzünden bekletilmemeli; bekleme burada süreç
        yeniden doğumu kadardır (~1 sn), ısıtmanın engellediği prefill ise 9-17 sn."""
        cancel = self._warm_cancel
        if cancel is not None:
            cancel.set()
            with contextlib.suppress(Exception):
                await self.send_raw(b'{"type":"abort"}\n')
        async with self._warm_lock:
            return

    async def _pump_stderr(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stderr is not None
        while line := await proc.stderr.readline():
            text = line.decode(errors="replace").strip()
            if not text:
                continue
            low = text.casefold()
            routine = (
                "packages are looking for funding",
                "run `npm fund`",
                "found 0 vulnerabilities",
                "npm notice",
                "npm warn deprecated",
                "added ",
                "audited ",
                "startup session lookup, project settings",
                "runtime creation, project settings",
            )
            if any(token in low for token in routine):
                log.debug("pi[%s]: %s", self.key.session_id, text)
            elif low.startswith("error:"):
                log.warning("pi[%s]: %s", self.key.session_id, text)
            else:
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
            if proc.pid is not None:
                self._planned_pids.add(proc.pid)
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
    def __init__(self, socket_path: Path, allowed_mode: str = "all"):
        self.socket_path = socket_path
        self.allowed_mode = allowed_mode
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
        if self.allowed_mode == "dev":
            log.info("dev broker: normal persona prewarm atlandı")
            return
        for key in _prewarm_specs():
            try:
                process = await self.process_for(key)
            except Exception:  # noqa: BLE001 — tek prewarm servisi düşürmesin
                log.exception("prewarm başarısız: %s", key.label)
                continue
            # Süreci doğurmak YETMİYOR: beynin KV önbelleği hâlâ boş. Kuru bir
            # ısıtma turu at (geçmişe SIZMAZ, bkz. warm_once).
            await self.warm_once(process)

    async def warm_once(self, process: PiProcess) -> bool:
        """Bir ısıtma turu at ve pi'nın geçmişini ısıtma ÖNCESİ hâline geri döndür.

        Sıra: geçmişi parmak izle → prompt at (çıktı yutulur) → süreci ÖLDÜR
        (dosyaya yazma bitsin) → jsonl'i geri al → süreci temiz geçmişle yeniden
        doğur. Herhangi bir adım patlarsa akış NORMAL devam eder (sessiz düşüş:
        loglanır, tur düşürülmez)."""
        if not _warmup_enabled():
            return False
        async with process._warm_lock:
            if process.busy:
                return False  # gerçek kullanıcı slotta — ısıtma YARIŞMAZ
            key = process.key
            snap = snapshot_history(key.session_id)
            cancel = asyncio.Event()
            process._warm_cancel = cancel
            warmed = False
            try:
                await process.start()
                warmed = await process.warm(_warmup_prompt(), _warmup_timeout(), cancel)
            except Exception:  # noqa: BLE001 — ısıtma broker'ı düşürmesin
                log.warning("ısıtma turu başarısız: %s", key.label, exc_info=True)
            finally:
                process._warm_cancel = None
                with contextlib.suppress(Exception):
                    await process.stop()
                clean = await asyncio.to_thread(restore_history, key.session_id, snap)
                with contextlib.suppress(Exception):
                    await process.start()
            log.info(
                "ısıtma: %s (tur=%s, geçmiş temiz=%s)",
                key.label,
                "tamam" if warmed else "yarım",
                clean,
            )
            return warmed and clean

    async def keepalive_loop(self) -> None:
        """Periyodik hafif ısıtma. Aktif konuşma sırasında ÇALIŞMAZ (tek slot)."""
        if self.allowed_mode == "dev":
            log.info("dev broker: keepalive ısıtması kapalı")
            return
        interval = _warmup_interval()
        if not _warmup_enabled() or interval <= 0:
            log.info("keepalive ısıtması kapalı")
            return
        log.info("keepalive ısıtması açık: her %.0f sn", interval)
        while True:
            await asyncio.sleep(interval)
            for key in _prewarm_specs():
                process = self._processes.get(key)
                if process is None or process.busy:
                    continue
                try:
                    await self.warm_once(process)
                except Exception:  # noqa: BLE001 — döngü tek hatayla ölmesin
                    log.warning("keepalive ısıtması başarısız: %s", key.label, exc_info=True)

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
            if self.allowed_mode == "normal" and key.dev:
                raise RuntimeError("dev oturumu normal broker'da reddedildi")
            if self.allowed_mode == "dev" and not key.dev:
                raise RuntimeError("normal oturum dev broker'da reddedildi")
            process = await self.process_for(key)
            # Gerçek kullanıcı ısıtmayı ezer: süren ısıtma kesilir, geçmişi geri
            # alınır, süreç temiz doğar. Sonra kiralama yapılır.
            await process.yield_warm()
            attached = False
            async with process._warm_lock:
                # Aynı key için eşzamanlı ikinci client geldiyse mevcut kiralamaya
                # dokunma; sağlık probe'u yanıtını ilk client'a kaçırırdı.
                if not process.busy:
                    await process.start()  # ısıtma süreci yeniden doğurmuş olabilir
                    if not await process.probe(_probe_timeout()):
                        stale_pid = process.proc.pid if process.proc is not None else None
                        log.warning(
                            "pi RPC sağlık sorgusu yanıtsız → süreç yenileniyor: %s "
                            "(pid=%s generation=%d)",
                            key.label,
                            stale_pid,
                            process.generation,
                        )
                        await process.stop()
                        await process.start()
                        if not await process.probe(_probe_timeout()):
                            raise RuntimeError("pi RPC sağlık sorgusu taze süreçte de başarısız")
                    attached = await process.attach(writer)
            if not attached:
                writer.write(
                    json.dumps(
                        {"type": "broker_error", "error": "pi oturumu zaten kullanımda"}
                    ).encode()
                    + b"\n"
                )
                await writer.drain()
                return
            writer.write((json.dumps({
                "type": "broker_ready",
                "key": key.label,
                "pid": process.proc.pid if process.proc is not None else None,
                "generation": process.generation,
            }) + "\n").encode())
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
                if msg.get("type") == "get_entries":
                    # Cursor/yeniden-bağlanma güvenliği için gereken bu okuma Pi
                    # sürecine gönderilmez. Eski Candan session'ında Pi RPC burada
                    # takılıp prompt'u da arkasında bloke ediyordu.
                    request_id = msg.get("id")
                    try:
                        session_dir = PI_DEV_SESSION_DIR if key.dev else _session_dir()
                        entries, leaf = await asyncio.to_thread(
                            _session_entries,
                            key.session_id,
                            session_dir,
                            str(msg["since"]) if msg.get("since") else None,
                        )
                        # Cursor güvenliği için içerik metni/tool payload'ı gerekmez;
                        # yalnız id, entry tipi ve message rolü kullanılır. Uzun session
                        # metnini Unix socket'te her tur yeniden taşımayız.
                        compact_entries: list[dict] = []
                        for entry in entries:
                            compact: dict = {
                                "id": entry.get("id"),
                                "type": entry.get("type"),
                            }
                            message = entry.get("message")
                            if isinstance(message, dict):
                                compact["message"] = {"role": message.get("role")}
                            compact_entries.append(compact)
                        response = {
                            "id": request_id,
                            "type": "response",
                            "command": "get_entries",
                            "success": True,
                            "data": {"entries": compact_entries, "leafId": leaf},
                        }
                    except Exception as exc:  # noqa: BLE001 — Pi RPC biçiminde hata
                        response = {
                            "id": request_id,
                            "type": "response",
                            "command": "get_entries",
                            "success": False,
                            "error": str(exc),
                        }
                    writer.write((json.dumps(response) + "\n").encode())
                    await writer.drain()
                    continue
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
    dev = bool(hello.get("dev"))
    # mem_user dizin adına gider (dev kökü altında users/<mem_user>/) → aynı sade
    # karakter kümesi. Normal yolda YOK SAYILIR: kimlik orada session_id'den çözülür.
    mem_user = str(hello.get("mem_user") or "")
    if mem_user and not _SAFE_PART.fullmatch(mem_user):
        raise RuntimeError("geçersiz mem_user")
    return BrokerKey(persona, session_id, model, thinking, dev, mem_user if dev else "")


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


async def _run(socket_path: Path, allowed_mode: str = "all") -> None:
    broker = PiBroker(socket_path, allowed_mode=allowed_mode)
    await broker.start()
    await broker.prewarm_defaults()
    keepalive = asyncio.create_task(broker.keepalive_loop())
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in ("SIGINT", "SIGTERM"):
        signal = getattr(__import__("signal"), signal_name)
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signal, stop.set)
    await stop.wait()
    keepalive.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await keepalive
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
    parser.add_argument(
        "--allow-mode", choices=("normal", "dev", "all"), default="all",
        help="bu broker'ın kabul edeceği Pi oturum türü",
    )
    parser.add_argument("--reload", action="store_true", help="yaşayan Pi süreçlerini yenile")
    args = parser.parse_args()
    logging.basicConfig(level=os.environ.get("PI_BROKER_LOG_LEVEL", "INFO"))
    socket_path = Path(args.socket)
    if args.reload:
        return asyncio.run(_request_reload(socket_path))
    try:
        asyncio.run(_run(socket_path, args.allow_mode))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
