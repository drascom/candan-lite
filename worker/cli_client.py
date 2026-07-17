#!/usr/bin/env python3
"""Candan terminal sesli istemcisi — tarayıcısız test kabuğu.

NEDEN: web istemcisi her worker restart'ında bağlantı dansı istiyor (sayfa yenile,
`.next` bozulmaları, dev sunucusu çakışmaları). Bu dosya aynı işi TERMİNALDEN yapar:
mikrofonu yayınlar, Candan'ın sesini hoparlörden çalar, transkripti ve tool olaylarını
(`mate.tool`) satır satır basar. Next.js dev sunucusu KAPALIYKEN de çalışır — token
yerelde üretilir (worker/.env), `/api/token`'a bağımlılık YOK.

Kullanım:
    cd worker && .venv/bin/python cli_client.py                 # yerel beyin (varsayılan)
    cd worker && .venv/bin/python cli_client.py --brain remote   # uzak beyin (GPT)
    cd worker && .venv/bin/python cli_client.py --list-devices    # ses cihazlarını listele
    cd worker && .venv/bin/python cli_client.py --no-audio        # sadece bağlan (smoke test)

Ctrl+C → odadan düzgün ayrılır (agent shutdown callback'i / finalize() çalışır).

RENK ŞEMASI (düz ANSI — rich/colorama YOK, Pi'de ve her terminalde çalışır):
    kullanıcı = cyan · Candan = yeşil · eylem kartı = mor · hata = kırmızı · damga = soluk.
    Kapatma: `--no-color` ya da `NO_COLOR=1` (standart). stdout TTY değilse (pipe/dosya)
    renk OTOMATİK kapanır → kopyala-yapıştır çıktısı temiz kalır.
    NEDEN düz ANSI: terminale özel API'ye (CMuX vb.) bağlanmak bu kodu Pi'de/köprüde
    kırardı; ANSI'yi CMuX dahil her terminal render eder.

TRANSKRİPT DOSYASI: ekrana basılan her şey `worker/logs/transcript.log`'a DÜZ metin
    (renksiz, kutusuz, tek satır) olarak da yazılır → orkestratöre yapıştırmaya gerek yok.
    Yol `MATE_CLI_TRANSCRIPT` ile değişir; "" → yazma. Her çalıştırmada SIFIRLANIR
    (worker/logs/agent.log deseni).

YANKI BASTIRMA (AEC): hoparlörle konuşurken Candan'ın kendi sesi mikrofona geri girer →
    kendini böler, üstelik o ses speaker-ID'ye BİLİNMEYEN görünüp "sen kimsin?" enrollment'ını
    tetikler. Tarayıcı bunu `getUserMedia({echoCancellation:true})` ile bedava alıyordu; burada
    WebRTC APM'i (livekit.rtc.AudioProcessingModule) elle sürüyoruz — bkz. EchoCanceller.
    Varsayılan AÇIK. Kaçış kapıları: `--no-aec` / `MATE_CLI_AEC=0` (kulaklık takılıysa gereksiz),
    `--half-duplex` / `MATE_CLI_HALF_DUPLEX=1` (yedek yol: Candan konuşurken mikrofonu sustur;
    yankıyı kesin bitirir ama sözünü kesemezsin).

TASARIM KISITI — bu kod ileride Raspberry Pi hoparlör istemcisine taşınacak
(docs/MULTI-CLIENT-PLAN.md). Bu yüzden:
  - macOS'a özel HİÇBİR şey yok; `sounddevice` (PortAudio) + livekit.rtc yeter.
    (AEC de öyle: APM WebRTC'nin taşınabilir YAZILIM hattı, işletim sistemine bağlı değil.)
  - Ses cihazı seçilebilir (--input-device / --output-device / --list-devices).
  - Yapılandırma env + CLI bayrağı; kod içine gömülü sabit YOK.

NEDEN worker/ İÇİNDE: bağımlılıkların TAMAMI (livekit, livekit-api, sounddevice, numpy)
zaten worker/.venv'de ve kimlikler zaten worker/.env'de. Ayrı bir `clients/terminal/`
kendi venv'ini + requirements'ını isterdi; üstelik MULTI-CLIENT-PLAN.md §6 notu Pi
köprüsü için `bridge/` adını ayırmış — şimdi `clients/` açmak o kararı önden bozardı.
"""

from __future__ import annotations

import argparse
import array
import asyncio
import datetime
import json
import logging
import math
import os
import shutil
import signal
import sys
import textwrap
import threading
import time
import unicodedata
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from livekit import api, rtc

WORKER_DIR = Path(__file__).resolve().parent
load_dotenv(WORKER_DIR / ".env")

# ── Sözleşme sabitleri — web ile AYNI olmak ZORUNDA ─────────────────────────────
# Ad uyuşmazsa agent odaya HİÇ girmez (bkz. web/lib/agent-name.ts, worker/agent.py:86).
AGENT_NAME = os.environ.get("LIVEKIT_AGENT_NAME") or os.environ.get("AGENT_NAME") or "candan"
# `mate.tool` — Candan'ın NE YAPTIĞI (worker/agent.py:145, web/lib/tool-events.ts).
TOOL_TOPIC = "mate.tool"
# `lk.transcription` — livekit-agents'ın transkript kanalı (agents/types.py:54).
TRANSCRIPTION_TOPIC = "lk.transcription"
# Stream header'ındaki "bu segment kesinleşti mi" bayrağı (agents/types.py:9).
# Kullanıcı satırını AYIKLAMAK için şart — bkz. _on_transcription.
ATTR_TRANSCRIPTION_FINAL = "lk.transcription_final"
# `candan.awake` — worker'ın uyku/uyanıklık yayını (agent.py:382, _apply_wake_state).
# Değeri sadece "true"/"false"; başka bir şey gelirse yok sayılır.
WAKE_ATTR = "candan.awake"
# Beyin seçimi — web/lib/brain.ts BRAINS ile aynı küme.
BRAINS = ("local", "remote")

# Ses. 48 kHz mono int16: LiveKit'in taşıdığı format; PortAudio hem macOS'ta hem Pi'de
# (USB mik / I2S hoparlör) 48k mono'yu sorunsuz verir. Cihaz desteklemiyorsa --sample-rate.
DEFAULT_SAMPLE_RATE = 48000
CHANNELS = 1
BLOCK_MS = 10  # PortAudio blok boyutu → 10 ms = 480 örnek @48k (LiveKit'in sevdiği kadar)
# Hoparlör jitter tamponu üst sınırı. Ağ tıkanıp sonra boşalırsa sonsuz büyümesin:
# eski sesi biriktirip gecikmeli çalmaktansa DÜŞÜRMEK doğru (konuşma canlı olmalı).
SPEAKER_BUFFER_MAX_MS = 2000

# ── Yankı bastırma sabitleri ────────────────────────────────────────────────────
# WebRTC APM'in doğal örnekleme oranları. Başka bir oran (ör. 44100) → 10 ms'de 441
# örnek düşer, APM reddedebilir → AEC'yi açmayız (bkz. _make_echo_canceller).
APM_RATES = (8000, 16000, 32000, 48000)
# Yarı-çift yönlü modda: hoparlör tamponu boşaldıktan SONRA mikrofonu bu kadar daha
# kapalı tut. Odanın yankı kuyruğu (reverb) hoparlör sustuğu an bitmiyor.
HALF_DUPLEX_HANGOVER_MS = 250

# Var olan oda için dispatch kararı — web/app/api/token/route.ts ile AYNI eşik.
DISPATCH_GRACE_MS = 8000

log = logging.getLogger("cli_client")


# ── Renk (düz ANSI) ─────────────────────────────────────────────────────────────
# Bağımlılık YOK (rich/colorama değil) ve terminale özel API YOK: CMuX'un kendi
# renklendirmesine bağlanmak bu kodu Pi'de / köprüde / düz terminalde kırardı; ANSI'yi
# CMuX dahil hepsi render eder (bkz. TASARIM KISITI, dosya başı).
_ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "me": "\033[36m",     # cyan  — kullanıcı
    "agent": "\033[32m",  # yeşil — Candan
    "tool": "\033[35m",   # mor   — eylem kartı
    "err": "\033[31m",    # kırmızı — hata
}
_COLOR = False  # main() ayarlar; varsayılan KAPALI (import edildiğinde kirletme)


def _color_enabled(no_color_flag: bool) -> bool:
    """`--no-color` > NO_COLOR (standart) > TTY mi?

    TTY DEĞİLSE (pipe/dosya) renk kapanır: kullanıcı çıktıyı kopyalayıp yapıştırıyor,
    ANSI kaçışları çöp olmasın.
    """
    if no_color_flag or os.environ.get("NO_COLOR") is not None:
        return False
    return sys.stdout.isatty()


def _c(text: str, *styles: str) -> str:
    """Metni ANSI ile boya. Renk kapalıysa metin AYNEN döner (uzunluk da değişmez)."""
    if not _COLOR or not styles:
        return text
    return "".join(_ANSI[s] for s in styles) + text + _ANSI["reset"]


def _dwidth(text: str) -> int:
    """Görünen sütun genişliği — emoji/CJK 2 sütun. Kutu çizgilerini hizalamak için."""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _term_width() -> int:
    """Terminal genişliği (dar terminalde kart bozulmasın diye alt/üst sınırlı)."""
    return max(40, min(shutil.get_terminal_size((100, 24)).columns, 100))


# ── Yazdırma ────────────────────────────────────────────────────────────────────
def _stamp(ts_ms: float | None = None) -> str:
    """[SS:DD:sn] damgası. Saniye VAR — gecikmeyi (STT→cevap→tool) gözle görelim."""
    return time.strftime("%H:%M:%S", time.localtime((ts_ms / 1000) if ts_ms else time.time()))


class Transcript:
    """Ekrana basılan her şeyin DÜZ metin kopyası → worker/logs/transcript.log.

    NEDEN: sohbet + tool kartları istemcinin stdout'unda kalıyordu, kullanıcı bunları
    orkestratöre elle yapıştırmak zorundaydı. Worker log'u (worker/logs/agent.log) zaten
    dosyada; bu onun sohbet tarafındaki eşi.

    Dosyada renk/kutu/ikon YOK, satırlar TAM (akışkan yazım parçaları burada birleşir)
    ve etiketler grep'lenebilir düz metin (TOOL_CALL / TOOL_RESULT / TOOL_ERROR).
    """

    def __init__(self, path: Path | None) -> None:
        self._fh = None
        self.path = path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # mode="w" → her çalıştırmada sıfırla (agent.py log dosyası deseni).
            self._fh = path.open("w", encoding="utf-8")
        except OSError:
            log.warning("transkript dosyası açılamadı: %s", path, exc_info=True)
            self.path = None

    def write(self, line: str) -> None:
        if self._fh is None:
            return
        try:
            self._fh.write(line.rstrip("\n") + "\n")
            self._fh.flush()  # kullanıcı canlı testte; orkestratör anında okuyabilsin
        except OSError:
            log.debug("transkript yazılamadı", exc_info=True)

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


class Printer:
    """Terminal çıktısının TEK sahibi.

    Neden bir sınıf: Candan'ın metni AKIŞKAN basılıyor (parça parça, `end=""`), yani
    ekranda YARIM bir satır açık kalabiliyor. O sırada bir tool kartı ya da kullanıcı
    satırı düşerse araya girip satırları birbirine katardı. Açık satırın sahibini
    (`_open`) burada tutup, başka bir şey basılmadan ÖNCE satırı kapatıyoruz; akış
    devam ederse etiket yeniden yazılıyor.

    Asyncio tek thread → kilit gerekmez.
    """

    def __init__(self, transcript: Transcript) -> None:
        self._open: str | None = None   # yarım satırın etiketi | None
        self._tr = transcript
        self._buf = ""                  # akışkan satırın dosya kopyası (tam satır yazılır)
        self._after_card = False        # kart zaten arkasında boşluk bıraktı mı

    # ── iç yardımcılar ──
    def _close_open_line(self) -> None:
        """Yarım satır varsa newline at — araya girecek çıktı onu bozmasın."""
        if self._open is not None:
            print(flush=True)
            self._open = None

    def _flush_buf(self, label: str) -> None:
        """Akışkan satırın TAMAMINI dosyaya tek satır olarak yaz."""
        if self._buf.strip():
            self._tr.write(f"[{_stamp()}] {label}: {self._buf.strip()}")
        self._buf = ""

    # ── genel API ──
    def line(self, label: str, text: str, style: str, ts_ms: float | None = None) -> None:
        """Tamamlanmış tek satır: `[14:32:07] Ayhan: metin`."""
        self._close_open_line()
        self._after_card = False
        stamp = _stamp(ts_ms)
        print(f"{_c('[' + stamp + ']', 'dim')} {_c(label + ':', style, 'bold')} {text}", flush=True)
        self._tr.write(f"[{stamp}] {label}: {text}")

    def stream(self, label: str, style: str, chunk: str) -> None:
        """Akışkan parça — agent'ın sesle HİZALI temposunu ekrana yansıtır (bkz. run())."""
        if self._open != label:
            self._close_open_line()
            self._after_card = False
            print(f"{_c('[' + _stamp() + ']', 'dim')} {_c(label + ':', style, 'bold')} ",
                  end="", flush=True)
            self._open = label
        print(chunk, end="", flush=True)
        self._buf += chunk

    def stream_end(self, label: str) -> None:
        """Segment bitti → satırı kapat, dosyaya TAM satırı yaz."""
        if self._open == label:
            print(flush=True)
            self._open = None
        self._flush_buf(label)

    def info(self, text: str) -> None:
        self._close_open_line()
        self._after_card = False
        print(_c(f"·  {text}", "dim"), flush=True)
        self._tr.write(f"·  {text}")

    def card(self, event: dict[str, Any]) -> None:
        """Eylem kartı — web UI'deki ToolRow'un terminal karşılığı ("kart modu").

        Konuşma akışından görsel olarak AYRIK: kenar çubuğu + başlık çizgisi + girintili,
        sarmalanmış argümanlar. Renk kapalıyken de (pipe) okunur kalır — kutu çizgileri
        ANSI değil, düz Unicode.
        """
        self._close_open_line()
        name = str(event.get("name", "?"))
        stamp = _stamp(event.get("ts"))
        is_call = event.get("type") == "tool_call"
        error = bool(event.get("isError"))

        if is_call:
            icon, style, rows = "🔧", "tool", _rows_from_args(event.get("args"))
        elif error:
            icon, style, rows = "❌", "err", [str(event.get("result", ""))]
        else:
            icon, style, rows = "✅", "agent", [str(event.get("result", ""))]

        # Kutu genişliği: üst ve alt çizgi AYNI sütunda bitsin (emoji 2 sütun → _dwidth).
        width = _term_width()
        head = f"╭─ {icon} {name} · {stamp} "
        rule = "─" * max(2, width - _dwidth(head) - 1)
        inner = max(16, width - 5)  # "│  " + sağ pay; dar terminalde de sarma çalışsın

        if not self._after_card:
            print()  # kartın önünde boşluk → konuşma akışından ayrılsın
        print(_c(f"╭─ {icon} ", style) + _c(name, style, "bold")
              + _c(f" · {stamp} {rule}", style), flush=True)
        for row in rows:
            for wrapped in (textwrap.wrap(row, width=inner, subsequent_indent="  ") or [""]):
                print(_c("│", style) + f"  {wrapped}", flush=True)
        print(_c("╰" + "─" * (_dwidth(head) + len(rule) - 1), style), flush=True)
        print()
        self._after_card = True

        # Dosya: ikonsuz, kutusuz, grep'lenebilir tek satır.
        tag = "TOOL_CALL" if is_call else ("TOOL_ERROR" if error else "TOOL_RESULT")
        payload = _json_or_str(event.get("args")) if is_call else str(event.get("result", ""))
        self._tr.write(f"[{stamp}] {tag} {name} {payload}".rstrip())


# Tek süreç / tek oturum → tek Printer. main() kurar; kurulmadan çağrılırsa (import,
# --list-devices) düz print'e düşer, kimse patlamaz.
_OUT: Printer | None = None


def _info(text: str) -> None:
    if _OUT is None:
        print(f"·  {text}", flush=True)
        return
    _OUT.info(text)


def _json_or_str(value: Any) -> str:
    """Argümanları tek satır JSON'a çevir; çevrilemezse repr'ine düş (satır BOZULMASIN)."""
    try:
        return json.dumps(value if value is not None else {}, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _rows_from_args(args: Any) -> list[str]:
    """Kart gövdesi: `anahtar: değer` satırları. Tek satır JSON okunmuyordu (kullanıcı
    "daha belirgin" istedi) → alan başına bir satır, uzun değer sarmalanır."""
    if not isinstance(args, dict) or not args:
        return [_json_or_str(args)]
    rows = []
    for key, value in args.items():
        text = value if isinstance(value, str) else _json_or_str(value)
        rows.append(f"{key}: {text}")
    return rows


# ── Token + dispatch — web/app/api/token/route.ts'in BİREBİR karşılığı ──────────
def _brain_metadata(brain: str | None) -> str:
    """`--brain local|remote` → `{"brain":"local"}` (JSON string).

    Geçersiz/eksik → '' → metadata GÖNDERİLMEZ → worker `worker/.env` içindeki
    PI_MODEL/PI_THINKING varsayılanına düşer. web/lib/brain.ts + route.ts:129 ile aynı.

    Neden JOB (dispatch) metadata'sı, participant metadata'sı DEĞİL: worker bunu
    `ctx.job.metadata` ile entrypoint'in İLK satırında görür (agent.py:_brain_choice) —
    pi alt-süreci doğarken seçim ELDEDİR. Participant metadata'sında yarış olurdu.
    """
    # separators: JS JSON.stringify boşluksuz basar → worker'ın gördüğü metadata web ile BİREBİR aynı.
    return json.dumps({"brain": brain}, separators=(",", ":")) if brain in BRAINS else ""


def _mint_token(*, api_key: str, api_secret: str, room: str, identity: str, name: str, metadata: str) -> str:
    """Katılımcı token'ı — route.ts createParticipantToken + withAgentDispatch.

    Token'a gömülü `roomConfig.agents[]` daveti YALNIZCA oda İLK KEZ oluşturulurken
    işlenir; oda zaten varsa yok sayılır. Yeni-oda yolunu bu kaplar, var olan oda için
    `_ensure_agent_dispatch` devreye girer (route.ts:82-88 ile aynı ikili).
    """
    token = (
        api.AccessToken(api_key, api_secret)
        .with_identity(identity)
        .with_name(name)
        .with_ttl(datetime.timedelta(minutes=15))  # route.ts: ttl '15m'
        .with_grants(
            api.VideoGrants(
                room=room,
                room_join=True,
                can_publish=True,
                can_publish_data=True,
                can_subscribe=True,
            )
        )
    )
    if AGENT_NAME:
        token = token.with_room_config(
            api.RoomConfiguration(
                agents=[api.RoomAgentDispatch(agent_name=AGENT_NAME, metadata=metadata)]
            )
        )
    return token.to_jwt()


async def _ensure_agent_dispatch(url: str, api_key: str, api_secret: str, room: str, metadata: str) -> None:
    """Var olan oda için agent'ı açıkça dispatch et — route.ts ensureAgentDispatch.

    CANLI KANIT (route.ts:198-220): worker ölünce LiveKit dispatch KAYDINI SİLMİYOR ve
    job durumunu da güncellemiyor (60+ sn sonra hâlâ status=RUNNING). Yani "kayıt var mı"
    agent'ın CANLI olduğunu SÖYLEMEZ. Tek güvenilir canlılık sinyali: odadaki AGENT
    kind KATILIMCI. Sıra:
      - Oda YOKSA → dokunma (token'a gömülü davet oda yaratılırken tetiklenir).
      - Odada AGENT katılımcı VARSA → dokunma (agent canlı).
      - Katılımcı yok ama TAZE kayıt varsa (< DISPATCH_GRACE_MS) → dokunma (agent yolda;
        çift-agent = çift ses yarışını kapatan koşul BUDUR).
      - Aksi halde → bayat kayıtları sil + createDispatch (worker restart senaryosu).

    route.ts'teki in-flight kilidi BURADA YOK ve gerekmiyor: o kilit aynı Next.js
    sürecine AYNI ANDA düşen iki POST'u (StrictMode çift-mount, iki sekme) topluyordu.
    CLI tek süreç, tek oturum, tek çağrı → yarışacak ikinci istek yok.

    Hata dayanıklılığı: patlarsa bağlanmayı yine deniyoruz (token elde) — sadece uyarı.
    """
    if not AGENT_NAME:
        return
    lkapi = api.LiveKitAPI(url=url, api_key=api_key, api_secret=api_secret)
    try:
        rooms = await lkapi.room.list_rooms(api.ListRoomsRequest(names=[room]))
        if not rooms.rooms:
            return  # oda yok → gömülü dispatch halleder

        parts = await lkapi.room.list_participants(api.ListParticipantsRequest(room=room))
        if any(p.kind == api.ParticipantInfo.Kind.AGENT for p in parts.participants):
            return  # agent canlı

        listed = await lkapi.agent_dispatch.list_dispatch(room_name=room)
        mine = [d for d in listed if d.agent_name == AGENT_NAME]
        # state.created_at NANOSANİYE → ms (protobuf: alan yoksa 0 döner).
        newest_ms = max((d.state.created_at / 1e6 for d in mine), default=0)
        now_ms = time.time() * 1000
        if newest_ms > 0 and now_ms - newest_ms < DISPATCH_GRACE_MS:
            return  # agent yolda

        # Buraya geldiysek kayıtlar kesinlikle ölü (worker restart) → temizle.
        for d in mine:
            try:
                await lkapi.agent_dispatch.delete_dispatch(dispatch_id=d.id, room_name=room)
            except Exception:  # noqa: BLE001 — silme patlarsa createDispatch yine denenir
                log.warning("bayat dispatch silinemedi: %s", d.id, exc_info=True)

        await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(room=room, agent_name=AGENT_NAME, metadata=metadata)
        )
        _info(f"agent dispatch edildi ({AGENT_NAME}{', ' + metadata if metadata else ''}"
              f"{'; bayat kayıt vardı → worker restart' if newest_ms else ''})")
    except Exception:  # noqa: BLE001 — dispatch patlasa da bağlanmayı deneriz
        log.warning("ensureAgentDispatch başarısız (yine de bağlanılıyor)", exc_info=True)
    finally:
        await lkapi.aclose()


# ── Yankı bastırma (AEC) ────────────────────────────────────────────────────────
class EchoCanceller:
    """WebRTC ses işleme hattı (APM) — tarayıcının `echoCancellation:true`'sunun karşılığı.

    NEDEN VAR: tarayıcı istemcisi mikrofonu `getUserMedia({echoCancellation:true})` ile
    açıyordu, yani yankı bastırmayı BEDAVA alıyordu. Bu dosya mikrofonu `sounddevice`
    (PortAudio) ile HAM açıyor → hoparlörden çıkan Candan sesi mikrofona geri giriyor →
    Candan kendi sesini duyup kendini bölüyor. Üstelik o "ses" speaker-ID'ye BİLİNMEYEN
    görünüp enrollment'ı ("kimsin?") tetikliyor.

    ÇÖZÜM (kaynak: worker/.venv/.../livekit/rtc/apm.py — SDK'da HAZIR, kendi DSP'mizi
    yazmıyoruz): aynı WebRTC APM'i doğrudan çağır. İki akış beslenir:
      - `process_stream`      → mikrofon (near-end): yankı ÇIKARILIR, yerinde değişir.
      - `process_reverse_stream` → hoparlöre giden ses (far-end): "referans" — APM neyin
        yankı olduğunu ancak bunu görürse bilir. BESLENMEZSE AEC HİÇ ÇALIŞMAZ.
      - `set_stream_delay_ms` → far-end'in hoparlörden çıkıp mikrofona dönmesi arasındaki
        gecikme (apm.py:96-111). AEC3 bunu kendi de kestirir; biz iyi bir başlangıç veririz.

    NEDEN PortAudio thread'lerinden çağrılıyor: WebRTC APM zaten "capture thread +
    render thread" için tasarlı; `process_stream`/`process_reverse_stream` ayrı
    thread'lerden çağrılmak ÜZERE var. Üstelik FFI isteği ctypes ile doğrudan native
    çağrı (_ffi_client.py:265 — IPC yok, ağ yok) → 10 ms bütçesinde rahat sığar.
    Asıl kazanç hizalama: referansı hoparlör callback'inde beslersek APM'in gördüğü ses
    donanıma giden sesin TA KENDİSİ olur (underrun sessizliği dahil) ve gecikme SABİT
    kalır — jitter tamponunda beslersek tampon derinliğiyle oynar, ki AEC'yi bozan tam
    olarak budur.

    Pi NOTU: burada macOS'a özel hiçbir şey yok — APM WebRTC'nin taşınabilir yazılım
    hattı, Pi'de de aynı çalışır (bkz. TASARIM KISITI, dosya başı).
    """

    def __init__(self, *, sample_rate: int) -> None:
        self._apm = rtc.AudioProcessingModule(
            echo_cancellation=True,   # asıl derdimiz: Candan kendi sesini duymasın
            noise_suppression=True,   # ev ortamı: buzdolabı/klima uğultusu STT'yi bozuyor
            high_pass_filter=True,    # DC/rumble at → speaker-ID gömmesi temizlensin
            auto_gain_control=True,   # uzaktan konuşan (mutfaktaki) kişi de duyulsun
        )
        self._sample_rate = sample_rate
        self._samples = sample_rate * BLOCK_MS // 1000  # APM 10 ms İSTER (apm.py:49)
        self._warned = False

    def _frame(self, buf: bytearray) -> rtc.AudioFrame:
        return rtc.AudioFrame(
            data=buf,
            sample_rate=self._sample_rate,
            num_channels=CHANNELS,
            samples_per_channel=len(buf) // (2 * CHANNELS),
        )

    def _guard(self, buf: bytearray, frames: int) -> bool:
        """APM SADECE tam 10 ms kabul eder; blok beklenenden farklıysa dokunma."""
        return frames == self._samples and len(buf) == self._samples * 2 * CHANNELS

    def _complain(self, what: str) -> None:
        """AEC patlarsa oturumu ÖLDÜRME — ham sese düş, bir kez uyar."""
        if not self._warned:
            self._warned = True
            log.warning("AEC %s başarısız → ham sese düşülüyor", what, exc_info=True)

    def process_capture(self, buf: bytearray, frames: int) -> None:
        """Mikrofon bloğu — YERİNDE temizlenir (yankı çıkar, gürültü bastırılır)."""
        if not self._guard(buf, frames):
            return
        try:
            self._apm.process_stream(self._frame(buf))
        except Exception:  # noqa: BLE001 — bkz. _complain
            self._complain("process_stream")

    def process_render(self, buf: bytearray, frames: int) -> None:
        """Hoparlöre giden blok — referans olarak beslenir; sonucu KULLANMIYORUZ
        (ses zaten donanıma yazıldı; burada amaç APM'e "işte çalan buydu" demek)."""
        if not self._guard(buf, frames):
            return
        try:
            self._apm.process_reverse_stream(self._frame(buf))
        except Exception:  # noqa: BLE001
            self._complain("process_reverse_stream")

    def set_delay_ms(self, delay_ms: int) -> None:
        try:
            self._apm.set_stream_delay_ms(max(0, delay_ms))
        except Exception:  # noqa: BLE001 — kestirim tutmasa da AEC3 kendi bulur
            self._complain("set_stream_delay_ms")


# ── Uyku/uyanıklık çanı ─────────────────────────────────────────────────────────
# NEDEN web ile BİREBİR aynı ses (web/components/app/debug-status.tsx playChime): evde
# iki istemci dolaşıyor (tarayıcı + terminal) ve kullanıcı çanı SESİNDEN tanıyor —
# terminalde farklı bir tını "başka bir şey oldu" diye okunurdu. Bu yüzden sabitler
# Web Audio çağrılarının birebir karşılığı; değiştirirken web'i de değiştir.
# NEDEN dosya değil sentez: .wav eklemek Pi'ye taşırken (docs/MULTI-CLIENT-PLAN.md)
# taşınacak bir varlık daha demek; math + array zaten stdlib.
CHIME_F0 = 660.0                        # ikisi de aynı yerden başlar → aynı "aile" duyulur
CHIME_F1 = {"wake": 990.0, "sleep": 440.0}  # yükselen = uyandı, alçalan = uyudu
CHIME_SWEEP_S = 0.18                    # f0→f1 üstel geçiş (exponentialRampToValueAtTime)
CHIME_ATTACK_S = 0.02
CHIME_DECAY_END_S = 0.35
CHIME_TOTAL_S = 0.37                    # osc.stop(now + 0.37)
# Web Audio'nun ÜSTEL rampası sıfıra inemez (0 → çalışmaz), o yüzden zarf 0.0001'den
# başlayıp 0.0001'e döner. Aynı sayıyı taşıyoruz ki zarfın eğrisi birebir çıksın.
CHIME_FLOOR = 0.0001
CHIME_PEAK = 32767  # int16 tam ölçek; web'de gain 1.0'a çıkıyor → aynı yükseklik

_CHIME_CACHE: dict[tuple[str, int], bytes] = {}


def _chime_gain(t: float) -> float:
    """Zarf: 0.0001 →(0.02 sn)→ 1.0 →(0.35 sn)→ 0.0001, hepsi ÜSTEL.

    Web Audio'da her rampa bir ÖNCEKİ olayın anından başlar; sönüm bu yüzden 0.02'den
    0.35'e uzanır (0.35 sn SÜRMEZ). Kuyruktaki 0.35→0.37 aralığı taban değerde sabit.
    """
    if t < CHIME_ATTACK_S:
        return CHIME_FLOOR * (1.0 / CHIME_FLOOR) ** (t / CHIME_ATTACK_S)
    if t < CHIME_DECAY_END_S:
        return CHIME_FLOOR ** ((t - CHIME_ATTACK_S) / (CHIME_DECAY_END_S - CHIME_ATTACK_S))
    return CHIME_FLOOR


def _chime_pcm(kind: str, sample_rate: int) -> bytes:
    """Çanın int16 PCM'i, istemcinin ÇALIŞTIĞI oranda (48k varsayma — --sample-rate var).

    Faz artımlı toplanıyor, sin(2πft) DEĞİL: frekans süpürülürken t'yi doğrudan çarpmak
    fazı kırar (klik/cızırtı). Sonuç önbelleklenir — çan her uyku/uyanışta çalıyor,
    aynı diziyi her seferinde yeniden üretmenin anlamı yok.
    """
    key = (kind, sample_rate)
    cached = _CHIME_CACHE.get(key)
    if cached is not None:
        return cached

    ratio = CHIME_F1[kind] / CHIME_F0
    samples = array.array("h")
    phase = 0.0
    for i in range(int(sample_rate * CHIME_TOTAL_S)):
        t = i / sample_rate
        freq = CHIME_F0 * ratio ** min(t / CHIME_SWEEP_S, 1.0)  # süpürme bitince sabit
        value = int(CHIME_PEAK * _chime_gain(t) * math.sin(phase))
        phase += 2 * math.pi * freq / sample_rate
        value = max(-32768, min(32767, value))  # zarf tepesi tam ölçek → yuvarlama taşmasın
        for _ in range(CHANNELS):
            samples.append(value)
    pcm = samples.tobytes()
    _CHIME_CACHE[key] = pcm
    return pcm


# ── Ses G/Ç — sounddevice (PortAudio). Pi'de de aynı kod. ───────────────────────
class Speaker:
    """Candan'ın sesi → hoparlör.

    PortAudio callback'i AYRI bir thread'de koşar; asyncio tarafı sadece tampona yazar.
    Kilit + bytearray yeterli (queue.Queue'nun blok başına nesne maliyeti yok).
    """

    def __init__(self, *, device: str | int | None, sample_rate: int,
                 aec: EchoCanceller | None = None) -> None:
        import sounddevice as sd

        self._buf = bytearray()
        self._lock = threading.Lock()
        self._aec = aec
        self._sample_rate = sample_rate  # çan bu oranda sentezlenir (bkz. play_chime)
        self._bytes_per_frame = 2 * CHANNELS
        self._max_bytes = sample_rate * self._bytes_per_frame * SPEAKER_BUFFER_MAX_MS // 1000
        self._silent_since = time.monotonic()  # yarı-çift yönlü kapı için (bkz. is_playing)
        self._stream = sd.RawOutputStream(
            samplerate=sample_rate,
            channels=CHANNELS,
            dtype="int16",
            device=device,
            blocksize=sample_rate * BLOCK_MS // 1000,
            callback=self._callback,
        )
        self._stream.start()

    @property
    def latency(self) -> float:
        """PortAudio'nun bildirdiği çıkış gecikmesi (sn) — AEC gecikme kestirimi için."""
        return float(self._stream.latency)

    def _callback(self, outdata, frames, _time, status) -> None:
        if status:
            log.debug("hoparlör: %s", status)
        need = frames * self._bytes_per_frame
        with self._lock:
            take = min(need, len(self._buf))
            if take:
                outdata[:take] = self._buf[:take]
                del self._buf[:take]
            if not self._buf:
                self._silent_since = time.monotonic()
        if take < need:
            outdata[take:need] = b"\x00" * (need - take)  # veri yok → sessizlik (underrun)
        if self._aec is not None:
            # Referansı BURADA besliyoruz: donanıma giden sesin birebir kopyası
            # (underrun sessizliği DAHİL → akış sürekli, gecikme sabit).
            self._aec.process_render(bytearray(outdata[:need]), frames)

    def is_playing(self) -> bool:
        """Yarı-çift yönlü kapı: Candan şu an konuşuyor mu?

        Tampon boşaldığı ANDA "sustu" demek erken — odanın yankı kuyruğu (reverb) hâlâ
        mikrofona düşüyor. HALF_DUPLEX_HANGOVER_MS kadar daha kapalı tut.
        """
        with self._lock:
            if self._buf:
                return True
            quiet_ms = (time.monotonic() - self._silent_since) * 1000
        return quiet_ms < HALF_DUPLEX_HANGOVER_MS

    def push(self, data: bytes) -> None:
        with self._lock:
            self._buf.extend(data)
            if len(self._buf) > self._max_bytes:
                # Taşma: ESKİ sesi at. Gecikmeli konuşma çalmaktansa kesmek yeğdir.
                del self._buf[: len(self._buf) - self._max_bytes]

    def play_chime(self, kind: str) -> None:
        """Uyku/uyanış çanını Candan'ın sesiyle AYNI akıştan çal.

        NEDEN ayrı bir ses cihazı/akış AÇMIYORUZ: bu akıştan geçen her şey `_callback`'te
        AEC'nin referansına da giriyor (process_render). Çanı oradan geçirmezsek mikrofona
        dönen çan APM'e görünmez → Candan kendi çanını "kullanıcı konuşması" sanar, yani
        uyku çanı onu anında geri uyandırabilirdi. Kendi akışını açmak bunu garanti bozardı.

        Kuyruğa EKLENİR (araya girmez): Candan konuşurken çan zaten çalmaz, çünkü çan
        yalnızca uyku/uyanış geçişinde gelir.
        """
        try:
            self.push(_chime_pcm(kind, self._sample_rate))
        except Exception:  # noqa: BLE001 — çan süs; oturumu ÖLDÜRMESİN
            log.warning("çan çalınamadı (%s)", kind, exc_info=True)

    def close(self) -> None:
        self._stream.stop()
        self._stream.close()


class Microphone:
    """Mikrofon → odaya yayın.

    PortAudio callback'i thread'den gelir → `call_soon_threadsafe` ile asyncio kuyruğuna
    aktarılır; kuyruğu tüketen task LiveKit'e frame basar (capture_frame await ister,
    callback içinde await edilemez).
    """

    def __init__(self, *, device: str | int | None, sample_rate: int, loop: asyncio.AbstractEventLoop,
                 aec: EchoCanceller | None = None, gate: Callable[[], bool] | None = None) -> None:
        import sounddevice as sd

        self._loop = loop
        self._sample_rate = sample_rate
        self._aec = aec
        self._gate = gate  # yarı-çift yönlü: True dönerse mikrofon susturulur
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)
        self.source = rtc.AudioSource(sample_rate, CHANNELS)
        self.track = rtc.LocalAudioTrack.create_audio_track("cli-mic", self.source)
        self._stream = sd.RawInputStream(
            samplerate=sample_rate,
            channels=CHANNELS,
            dtype="int16",
            device=device,
            blocksize=sample_rate * BLOCK_MS // 1000,
            callback=self._callback,
        )
        self._stream.start()
        self._task = loop.create_task(self._pump())

    @property
    def latency(self) -> float:
        """PortAudio'nun bildirdiği giriş gecikmesi (sn) — AEC gecikme kestirimi için."""
        return float(self._stream.latency)

    def _callback(self, indata, frames, _time, status) -> None:
        if status:
            log.debug("mikrofon: %s", status)
        data = bytearray(indata)
        if self._gate is not None and self._gate():
            # Yarı-çift yönlü: Candan konuşurken SESSİZLİK yolla. Track'i durdurmuyoruz —
            # akışı kesmek yeniden anlaşma (renegotiation) gerektirir; sessizlik ucuz.
            data = bytearray(len(data))
        elif self._aec is not None:
            # Yankıyı BURADA çıkar: yakalama thread'i → t_capture→t_process sabit ve
            # asyncio kuyruğunun değişken gecikmesi APM'in gecikme kestirimini bozmaz.
            self._aec.process_capture(data, frames)
        self._loop.call_soon_threadsafe(self._offer, bytes(data))

    def _offer(self, data: bytes) -> None:
        try:
            self._queue.put_nowait(data)
        except asyncio.QueueFull:
            log.debug("mikrofon kuyruğu dolu → blok düştü")  # asyncio takılmışsa sessizce at

    async def _pump(self) -> None:
        while True:
            data = await self._queue.get()
            frame = rtc.AudioFrame(
                data=data,
                sample_rate=self._sample_rate,
                num_channels=CHANNELS,
                samples_per_channel=len(data) // (2 * CHANNELS),
            )
            try:
                await self.source.capture_frame(frame)
            except Exception:  # noqa: BLE001 — oda kapanırken normal
                return

    async def aclose(self) -> None:
        self._stream.stop()
        self._stream.close()
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        await self.source.aclose()


# ── İstemci ─────────────────────────────────────────────────────────────────────
class TerminalClient:
    def __init__(self, args: argparse.Namespace, out: Printer) -> None:
        self.args = args
        self.out = out
        self.room = rtc.Room()
        self.identity = args.identity or f"cli-{uuid.uuid4()}"
        self._mic: Microphone | None = None
        self._speaker: Speaker | None = None
        self._tasks: set[asyncio.Task] = set()  # RUF006: task referansı kaybolmasın
        self._seen_tools: set[str] = set()      # yeniden bağlanmada çift kart basma
        self._awake: str | None = None          # `candan.awake` son değeri; None = hiç görülmedi

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ── Metin kanalları ────────────────────────────────────────────────────────
    def _on_transcription(self, reader: rtc.TextStreamReader, participant_identity: str) -> None:
        """`lk.transcription` — hem BENİM STT'im hem Candan'ın cevabı bu kanaldan gelir.

        Kimin konuştuğunu `participant_identity` söyler: worker, kullanıcı transkriptini
        `sender_identity=<kullanıcının kimliği>` ile yayınlar (agents room_io/_output.py:
        _create_text_writer) → gönderen agent olsa da olay BİZİM kimliğimizle görünür.

        İKİ TARAF, İKİ AYRI DAVRANIŞ (kaynak: agents/voice/room_io/room_io.py:145+):

        - KULLANICI çıkışı `is_delta_stream=False` kurulur. Bu modda _output.py:492
          `capture_text` HER çağrıda YENİ bir writer açıp TAM metni yazıp kapatır, ve
          `flush()` (is_final) _output.py:520'de BİR TANE DAHA writer açıp aynı metni
          `lk.transcription_final="true"` ile yeniden yayınlar. room_io.py:369
          `_forward_user_transcript` ise her olayda `capture_text` + (final ise) `flush`
          çağırır → STT tek bir FINAL olay yollasa bile odaya İKİ stream düşer.
          ÇİFT BASMANIN SEBEBİ BUDUR (interim değil, mimari). → sadece header'ında
          `lk.transcription_final="true"` olan stream'i bas; interim/ara stream'leri at.
        - AGENT çıkışı `is_delta_stream=True` (room_io.py:154) ve üstünde
          `TranscriptSynchronizer` var (room_io.py:163-170; `sync_transcription` NOT_GIVEN
          → `is not False` → AÇIK, audio output mevcut). Yani metin SESİN çalma hızına
          göre tempolanıp parça parça yayınlanır. Segment başına TEK writer olduğu için
          çift basma riski yok → parçaları GELDİKÇE bas (read_all ile beklersek sunucunun
          yaptığı ses-metin hizalaması çöpe giderdi; kullanıcı telaffuz avlıyor).
        """
        if participant_identity == self.identity:
            attrs = reader.info.attributes or {}
            if attrs.get(ATTR_TRANSCRIPTION_FINAL) != "true":
                return  # ara yayın → terminali kirletme (çift satırın kaynağı)
            self._spawn(self._read_user_line(reader))
        else:
            self._spawn(self._read_agent_stream(reader))

    async def _read_user_line(self, reader: rtc.TextStreamReader) -> None:
        """Kullanıcı satırı: TEK seferde, tamamlanmış. Artımlı basılmaz — kendi STT'mizin
        ara sonuçları ekranda yazıp-silme gürültüsü yapardı.

        ETİKET NEDEN NÖTR ("Sen"), kişi adı DEĞİL: worker konuşmacıyı ÇÖZÜYOR
        (speaker_tap.py:184 → SpeakerState.current) ama odaya YAYMIYOR. Worker'ın odaya
        yazdığı her şey şu ikisi: `mate.tool` (agent.py:304-310; payload'da konuşmacı alanı
        YOK) ve `candan.awake` katılımcı özniteliği (agent.py:382; değeri sadece
        true/false). `lk.transcription` üstündeki öznitelikler de livekit-agents'ın kendi
        alanları (segment_id / transcribed_track_id / transcription_final / expression) —
        isim taşımıyor. Yani istemci kimin konuştuğunu BİLEMEZ.

        Sabit bir isim yazmak (eski hâli: MATE_CLI_NAME=Ayhan) evdeki HERKESİ "Ayhan"
        etiketliyordu — anne konuşurken bile. Yanlış isim, isimsizlikten KÖTÜ: transkript
        okuyan (insan ya da orkestratör) uydurma veriyi gerçek sanır. Worker konuşmacıyı
        odaya yayarsa (ör. `candan.speaker` özniteliği) burası ona bağlanır.
        """
        try:
            text = (await reader.read_all()).strip()
        except Exception:  # noqa: BLE001 — okuma hatası oturumu BOZMAZ, satır düşer
            log.debug("transkript okunamadı", exc_info=True)
            return
        if text:
            self.out.line(self.args.me_label, text, "me")

    async def _read_agent_stream(self, reader: rtc.TextStreamReader) -> None:
        """Candan'ın satırı: parçalar GELDİKÇE ekrana (ses hızında akar)."""
        label = self.args.agent_label
        try:
            async for chunk in reader:
                if chunk:
                    self.out.stream(label, "agent", chunk)
        except Exception:  # noqa: BLE001 — okuma hatası oturumu BOZMAZ
            log.debug("agent transkripti okunamadı", exc_info=True)
        finally:
            self.out.stream_end(label)  # satırı kapat + dosyaya TAM satırı yaz

    def _on_tool_event(self, reader: rtc.TextStreamReader, _participant_identity: str) -> None:
        """`mate.tool` — Candan'ın NE YAPTIĞI (web/lib/tool-events.ts ile aynı sözleşme)."""
        self._spawn(self._read_tool_event(reader))

    async def _read_tool_event(self, reader: rtc.TextStreamReader) -> None:
        try:
            raw = await reader.read_all()
        except Exception:  # noqa: BLE001 — yayın hatası konuşmayı BOZMAZ
            log.debug("%s okunamadı", TOOL_TOPIC, exc_info=True)
            return
        try:
            event = json.loads(raw)
        except (ValueError, TypeError):
            return
        if not isinstance(event, dict) or event.get("type") not in ("tool_call", "tool_result"):
            return
        key = f"{event.get('type')}:{event.get('id')}"
        if key in self._seen_tools:
            return  # aynı olay iki kez geldi (yeniden bağlanma) → bir kez göster
        self._seen_tools.add(key)
        self.out.card(event)

    # ── Uyku/uyanıklık ─────────────────────────────────────────────────────────
    def _on_attributes_changed(self, changed: dict[str, str], participant: rtc.Participant) -> None:
        """`participant_attributes_changed` — worker'ın uyku/uyanış yayını.

        İmza SDK'dan doğrulandı (rtc/room.py:936 → emit(changed_attributes, participant)).
        Kendi özniteliklerimizi eleriz: bu olay LOCAL katılımcı için de atar, `candan.awake`
        bize ait olmasa da eleme ucuz ve niyeti açık.
        """
        if participant.identity == self.identity:
            return
        value = changed.get(WAKE_ATTR)
        if value is not None:
            self._apply_awake(value)

    def _apply_awake(self, value: str) -> None:
        """Durumu bas + geçişte çan çal.

        İLK değerde ÇAN YOK (web/debug-status.tsx ile aynı kural: `prev === undefined`):
        bağlanır bağlanmaz çan çalması "bir şey oldu" yanılgısı yaratır — oysa sadece
        odanın hâlini öğrendik. Satır yine basılır; kullanıcı bağlanınca durumu görmeli.
        """
        if value not in ("true", "false"):
            return  # sözleşme dışı değer → yok say (agent.py sadece true/false yayar)
        prev, self._awake = self._awake, value
        if prev == value:
            return
        self.out.info("👂 Uyanık — dinliyorum" if value == "true" else "😴 Uykuda — 'candan' de")
        if prev is not None and self._speaker is not None:
            self._speaker.play_chime("wake" if value == "true" else "sleep")

    def _sync_awake_from_room(self) -> None:
        """İlk değeri KAÇIRMA: agent biz bağlanmadan önce özniteliği set etmiş olabilir →
        o olay bize hiç düşmez, sadece katılımcının mevcut `attributes`'ında durur."""
        for participant in self.room.remote_participants.values():
            value = (participant.attributes or {}).get(WAKE_ATTR)
            if value is not None:
                self._apply_awake(value)

    # ── Ses aboneliği ──────────────────────────────────────────────────────────
    def _on_track_subscribed(self, track: rtc.Track, *_rest) -> None:
        if track.kind != rtc.TrackKind.KIND_AUDIO or self._speaker is None:
            return
        self._spawn(self._play(track))

    async def _play(self, track: rtc.Track) -> None:
        """Agent sesi → hoparlör. AudioStream odadaki 48k'yı istediğimiz orana çevirir."""
        stream = rtc.AudioStream.from_track(
            track=track, sample_rate=self.args.sample_rate, num_channels=CHANNELS
        )
        try:
            async for event in stream:
                if self._speaker:
                    self._speaker.push(bytes(event.frame.data))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — track kapanırken normal
            log.debug("ses akışı bitti", exc_info=True)
        finally:
            await stream.aclose()

    # ── Yaşam döngüsü ──────────────────────────────────────────────────────────
    def _make_echo_canceller(self) -> EchoCanceller | None:
        """AEC kurulabiliyor mu? Kurulamıyorsa None → çağıran yedeğe düşer.

        Kapatma yolları (Pi'de / başka donanımda ters giderse kod değişmeden dönülsün):
        `--no-aec` / `MATE_CLI_AEC=0` / `--half-duplex` / oran APM'e uymuyor.
        """
        a = self.args
        if not a.aec or a.half_duplex:
            return None
        if a.sample_rate not in APM_RATES:
            _info(f"AEC atlandı: {a.sample_rate} Hz APM'in oranlarından biri değil "
                  f"({'/'.join(map(str, APM_RATES))}) → --sample-rate 48000 dene")
            return None
        try:
            return EchoCanceller(sample_rate=a.sample_rate)
        except Exception:  # noqa: BLE001 — AEC kurulamazsa oturum yine de açılsın
            log.warning("APM kurulamadı → AEC'siz devam", exc_info=True)
            return None

    async def run(self, stop: asyncio.Event) -> None:
        a = self.args
        metadata = _brain_metadata(a.brain)
        token = _mint_token(
            api_key=a.api_key,
            api_secret=a.api_secret,
            room=a.room,
            identity=self.identity,
            name=a.me,
            metadata=metadata,
        )
        await _ensure_agent_dispatch(a.url, a.api_key, a.api_secret, a.room, metadata)

        self.room.register_text_stream_handler(TRANSCRIPTION_TOPIC, self._on_transcription)
        self.room.register_text_stream_handler(TOOL_TOPIC, self._on_tool_event)
        self.room.on("track_subscribed", self._on_track_subscribed)
        self.room.on("participant_attributes_changed", self._on_attributes_changed)
        self.room.on("disconnected", lambda *_: stop.set())

        await self.room.connect(a.url, token)
        _info(f"bağlandı: oda={a.room} kimlik={self.identity} beyin={a.brain or 'worker varsayılanı'}")

        if not a.no_audio:
            aec = self._make_echo_canceller()
            self._speaker = Speaker(device=a.output_device, sample_rate=a.sample_rate, aec=aec)
            # Yarı-çift yönlü kapı SADECE APM yolu kapalıyken takılır: ikisi birlikte
            # anlamsız olurdu (mikrofon zaten susturulmuşsa çıkaracak yankı yok).
            gate = self._speaker.is_playing if (aec is None and a.half_duplex) else None
            self._mic = Microphone(
                device=a.input_device, sample_rate=a.sample_rate, loop=asyncio.get_running_loop(),
                aec=aec, gate=gate,
            )
            if aec is not None:
                # delay = (t_render - t_analyze) + (t_process - t_capture) (apm.py:100-110).
                # Referansı hoparlör callback'inde, mikrofonu yakalama callback'inde
                # işlediğimiz için iki terim de PortAudio'nun bildirdiği donanım
                # gecikmesine iner. AEC3 bunu koşarken kendi rafine eder.
                delay_ms = int((self._speaker.latency + self._mic.latency) * 1000)
                aec.set_delay_ms(delay_ms)
                _info(f"AEC açık (APM: yankı+gürültü+HPF+AGC, gecikme≈{delay_ms} ms)")
            elif gate is not None:
                _info(f"yarı-çift yönlü: Candan konuşurken mikrofon kapalı "
                      f"(+{HALF_DUPLEX_HANGOVER_MS} ms) — sözünü KESEMEZSİN")
            else:
                _info("AEC KAPALI — hoparlör kullanıyorsan Candan kendi sesini duyar "
                      "(kulaklık tak ya da --half-duplex)")
            await self.room.local_participant.publish_track(
                self._mic.track,
                rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
            )
            _info("mikrofon yayında — konuşabilirsin. Çıkış: Ctrl+C")
        else:
            _info("--no-audio: ses açılmadı (yalnız bağlantı/transkript). Çıkış: Ctrl+C")

        # Hoparlör KURULDUKTAN sonra tara: aksi halde bu tarama sırasında düşen bir geçiş
        # `self._speaker is None` diye çanını kaybederdi. (--no-audio'da çan yok, satır var.)
        self._sync_awake_from_room()

        await stop.wait()

    async def aclose(self) -> None:
        """Temiz kapanış: önce odadan AYRIL → agent shutdown callback'i (finalize) koşsun."""
        _info("kapatılıyor…")
        try:
            await self.room.disconnect()
        except Exception:  # noqa: BLE001 — zaten kopmuş olabilir
            log.debug("disconnect hatası", exc_info=True)
        if self._mic:
            await self._mic.aclose()
        if self._speaker:
            self._speaker.close()
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)


# ── CLI ─────────────────────────────────────────────────────────────────────────
def _device(value: str | None) -> str | int | None:
    """`--input-device 3` (indeks) veya `--input-device "USB Mic"` (ad) — ikisi de geçerli."""
    if value is None or value == "":
        return None
    return int(value) if value.lstrip("-").isdigit() else value


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="cli_client.py",
        description="Candan terminal sesli istemcisi (tarayıcısız).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    # web/lib/brain.ts BRAIN_DEFAULT = 'local' → CLI de aynı varsayılanla gelir.
    # 'default' = metadata GÖNDERME → worker/.env PI_MODEL/PI_THINKING'e düş.
    p.add_argument("--brain", choices=(*BRAINS, "default"), default="local", help="oturum beyni")
    p.add_argument("--room", default=os.environ.get("MATE_LIVEKIT_ROOM"), help="LiveKit oda adı")
    p.add_argument("--url", default=os.environ.get("MATE_PUBLIC_LIVEKIT_URL") or os.environ.get("LIVEKIT_URL"),
                   help="LiveKit sunucu URL'i")
    p.add_argument("--identity", default=os.environ.get("MATE_CLI_IDENTITY"),
                   help="katılımcı kimliği (varsayılan: cli-<uuid>; canlı oturumla çakışmasın)")
    p.add_argument("--me", default=os.environ.get("MATE_CLI_NAME", "Sen"),
                   help="LiveKit katılımcı adı (cihazın sahibi; transkript ETİKETİ DEĞİL)")
    p.add_argument("--me-label", default=os.environ.get("MATE_CLI_LABEL", "Sen"),
                   help="transkriptte kullanıcı satırının etiketi. SABİTTİR — worker "
                        "konuşmacı kimliğini odaya yaymadığı için istemci kimin konuştuğunu "
                        "BİLEMEZ; nötr bırak (bkz. _read_user_line)")
    p.add_argument("--agent-label", default=os.environ.get("MATE_CLI_AGENT_LABEL", "Candan"),
                   help="transkriptte agent etiketi")
    p.add_argument("--input-device", type=_device, default=os.environ.get("MATE_CLI_INPUT_DEVICE"),
                   help="mikrofon: indeks veya ad (--list-devices)")
    p.add_argument("--output-device", type=_device, default=os.environ.get("MATE_CLI_OUTPUT_DEVICE"),
                   help="hoparlör: indeks veya ad (--list-devices)")
    p.add_argument("--sample-rate", type=int, default=int(os.environ.get("MATE_CLI_SAMPLE_RATE", DEFAULT_SAMPLE_RATE)),
                   help="ses örnekleme oranı (cihaz 48k desteklemiyorsa düşür)")
    p.add_argument("--list-devices", action="store_true", help="ses cihazlarını listele ve çık")
    p.add_argument("--no-audio", action="store_true",
                   help="mikrofon/hoparlör AÇMA — yalnız bağlan + transkript (smoke test)")
    # AEC varsayılan AÇIK: hoparlörle konuşurken Candan kendi sesini duyup kendini bölüyor
    # (ve o ses speaker-ID'ye BİLİNMEYEN görünüp "kimsin?" sorusunu tetikliyor).
    p.add_argument("--no-aec", dest="aec", action="store_false",
                   default=os.environ.get("MATE_CLI_AEC", "1") not in ("0", "false", ""),
                   help="yankı bastırmayı KAPAT (MATE_CLI_AEC=0 ile aynı; kulaklıkla "
                        "gereksiz, farklı donanımda ters giderse kaçış kapısı)")
    p.add_argument("--half-duplex", action="store_true",
                   default=os.environ.get("MATE_CLI_HALF_DUPLEX", "") in ("1", "true"),
                   help="APM yerine YEDEK yol: Candan konuşurken mikrofonu sustur. "
                        "Yankıyı kesin bitirir AMA sözünü kesemezsin (barge-in gider)")
    p.add_argument("--no-color", action="store_true",
                   help="ANSI rengi kapat (NO_COLOR env de aynı işi yapar; TTY değilse zaten kapalı)")
    p.add_argument("--verbose", action="store_true", help="debug logları")
    args = p.parse_args(argv)

    args.api_key = os.environ.get("LIVEKIT_API_KEY")
    args.api_secret = os.environ.get("LIVEKIT_API_SECRET")
    return args


def _transcript_path() -> Path | None:
    """MATE_CLI_TRANSCRIPT: yol (göreli → worker/ dizinine göre). Tanımsız →
    worker/logs/transcript.log. Boş string ("") → dosyaya HİÇ yazma.
    AGENT_LOG_FILE (log_utils.py:152) ile AYNI desen."""
    raw = os.environ.get("MATE_CLI_TRANSCRIPT")
    if raw is not None and not raw.strip():
        return None
    path = Path(raw.strip()).expanduser() if raw else Path("logs/transcript.log")
    return path if path.is_absolute() else WORKER_DIR / path


async def _amain(args: argparse.Namespace, out: Printer) -> int:
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    # Ctrl+C (SIGINT) ve SIGTERM → temiz kapanış. KeyboardInterrupt'ı asyncio'nun ortasında
    # yakalamak yerine event'e çeviriyoruz: odadan AYRILMA await'i böyle güvenle koşuyor.
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    client = TerminalClient(args, out)
    try:
        await client.run(stop)
    finally:
        await client.aclose()
    return 0


def main(argv: list[str] | None = None) -> int:
    # noqa: PLW0603 gerekçesi — ikisi de SÜREÇ ÖMRÜ boyunca tek kez, burada kurulur:
    # renk kararı (TTY/NO_COLOR) ve tek Printer. Alternatif (her yardımcıya parametre
    # geçirmek) modül düzeyindeki _info/_c çağrılarını gereksiz yere kirletirdi.
    global _COLOR, _OUT  # noqa: PLW0603

    args = _parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    _COLOR = _color_enabled(args.no_color)

    if args.list_devices:
        import sounddevice as sd

        print(sd.query_devices())
        return 0

    missing = [n for n, v in (("LIVEKIT_URL", args.url), ("LIVEKIT_API_KEY", args.api_key),
                              ("LIVEKIT_API_SECRET", args.api_secret), ("MATE_LIVEKIT_ROOM", args.room)) if not v]
    if missing:
        print(f"HATA: eksik ayar: {', '.join(missing)} → worker/.env veya CLI bayrağı", file=sys.stderr)
        return 2

    transcript = Transcript(_transcript_path())
    _OUT = out = Printer(transcript)
    if transcript.path:
        _info(f"transkript: {transcript.path} (her çalıştırmada sıfırlanır)")
    try:
        return asyncio.run(_amain(args, out))
    finally:
        transcript.close()


if __name__ == "__main__":
    raise SystemExit(main())
