"""log_utils — log gürültüsü (DedupeFilter) + log dosyası (setup_file_logging).

Sorun: bazı loglar HER chunk/pencerede basılır (ör. speaker_tap "sessiz pencere
atlandı" — sessizlik boyunca ~saniyede bir). Bir kere görmek yeterli; aynısını
sürekli tekrar görmek gürültü.

Çözüm: `DedupeFilter` — aynı logger + aynı seviye + aynı mesaj ŞABLONU (yani
`record.msg`, %-placeholder'lar doldurulmadan ÖNCEKİ hâli — böylece değişen
sayısal değerler [rms=0.001 vs 0.002] hâlâ "aynı mesaj" sayılır) art arda
LOG_DEDUPE_SECONDS içinde tekrar ederse yalnızca İLK oluşum basılır;
sonrakiler bastırılır. Pencere dolup mesaj yine basılırken kaç tekrarın
bastırıldığı da eklenir → teşhis yeteneği KAYBOLMAZ, sadece seyrekleşir.

WORKER_VERBOSE_LOGS=true → dedupe tamamen devre dışı (eski/ham davranış: her
satır basılır). Gerçek hata/uyarı (logger.warning/error) logları da bu filtre
tarafından ASLA tamamen SİLİNMEZ — yalnızca sık tekrarları seyreltilir, pencere
dolunca yine basılır.
"""
from __future__ import annotations

import json
import logging
import multiprocessing as mp
import os
import time
from pathlib import Path


def _verbose() -> bool:
    return os.getenv("WORKER_VERBOSE_LOGS", "").strip().lower() in ("1", "true", "yes", "on")


def _window() -> float:
    try:
        return float(os.getenv("LOG_DEDUPE_SECONDS", "30") or 30)
    except (TypeError, ValueError):
        return 30.0


class DedupeFilter(logging.Filter):
    """Bir logger'a `logger.addFilter(DedupeFilter())` ile takılır."""

    def __init__(self) -> None:
        super().__init__()
        self._last_key: tuple | None = None
        self._last_time: float = 0.0
        self._suppressed: int = 0

    def filter(self, record: logging.LogRecord) -> bool:
        if _verbose():
            return True  # eski davranış: hiç susturma yok
        window = _window()
        if window <= 0:
            return True
        key = (record.levelno, record.msg)
        now = time.monotonic()
        if key == self._last_key:
            if (now - self._last_time) < window:
                self._suppressed += 1
                return False
            # aynı mesaj ama pencere doldu → tekrar göster, kaçı bastırıldıysa ekle
            if self._suppressed:
                record.msg = f"{record.msg}  [+{self._suppressed} tekrar bastırıldı]"
            self._last_time = now
            self._suppressed = 0
            return True
        # farklı bir mesaj: önceki mesajın bastırma sayacı ona ait değil, düşülür
        self._last_key = key
        self._last_time = now
        self._suppressed = 0
        return True


# ── Log dosyası ───────────────────────────────────────────────────────────────
# Amaç: terminalde akan HER ŞEY aynı anda bir dosyaya da düşsün — kullanıcı log
# kopyalayıp yapıştırmasın, dosya doğrudan okunabilsin. Terminal çıktısı AYNEN
# devam eder (dosya EK bir hedef; livekit'in stdout handler'ına DOKUNMUYORUZ,
# yalnız root'a ikinci bir handler EKLİYORUZ — cli/log.py setup_logging de
# `root.addHandler` yapar, var olanları silmez).
#
# ÇOK-SÜREÇ (`agent.py dev` her job için ayrı pid) — NEDEN TEK YAZAN VAR:
# livekit job süreçleri log'u kendi stdout'una BASMAZ; `ipc/log_queue.py`
# LogQueueHandler ile LogRecord'ları pickle'layıp ANA sürece yollar, ana süreçteki
# LogQueueListener.handle da `lger.callHandlers(record)` ile bunları ANA sürecin
# handler'larından geçirir. Yani root'a ANA süreçte takılan bu FileHandler hem ana
# sürecin hem TÜM job süreçlerinin loglarını görür → dosyaya tek bir süreç yazar,
# satırlar birbirine KARIŞMAZ ve dosya kilidi/append yarışı diye bir sorun YOKTUR.
#
# TRUNCATE bu yüzden güvenle mode="w": handler yalnız ana süreçte, worker BİR KEZ
# başlarken kurulur. Job süreçleri bu fonksiyonu HİÇ çağırmaz (agent.py'de
# `if __name__ == "__main__"` bloğundan çağrılıyor; mp start method darwin'de
# "spawn", linux'ta "forkserver" → çocuk modülü `__mp_main__` olarak import eder,
# `__main__` bloğu ÇALIŞMAZ). Alttaki parent_process() kontrolü ikinci emniyet:
# "fork" bağlamında çocuk handler'ı miras alsa bile dosyayı SIFIRLAMAZ.
#
# Kaynak pid'i satırda: forward edilen record'da `record.process` ÇOCUĞUN pid'idir
# (record çocukta yaratılır, ana süreçte yalnız yeniden emit edilir) → hangi job'ın
# konuştuğu dosyadan okunur.

# Varsayılan yol: worker/logs/agent.log (bu dosyanın yanındaki logs/ dizini) — cwd'den
# bağımsız olsun diye modül konumuna çapalı. .gitignore zaten `logs/` + `*.log` içeriyor.
_DEFAULT_LOG_FILE = Path(__file__).resolve().parent / "logs" / "agent.log"

# livekit'in extra alanlarını (room=, participant=, reason= gibi teşhis için kritik
# bağlam) dosyaya da yazabilmek için kendi yardımcılarını kullanıyoruz. Private API:
# sürüm değişirse dosya extra'sız yazsın, ASLA patlamasın (log kurulumu worker'ı
# düşüremez).
try:  # noqa: SIM105
    from livekit.agents.cli.log import JsonFormatter as _LkJsonFormatter
    from livekit.agents.cli.log import _merge_record_extra as _lk_merge_extra
except Exception:  # noqa: BLE001 — livekit yok/değişti → extra'sız düz format
    _LkJsonFormatter = None
    _lk_merge_extra = None


class _PlainFormatter(logging.Formatter):
    """ANSI'siz, extra alanlarını sona JSON olarak ekleyen dosya formatlayıcısı.

    Terminal ColoredFormatter kullanır (renk kaçış kodları); dosyaya onları YAZMAK
    istemiyoruz — grep/okuma bozulur. Traceback'ler `logging.Formatter.format`
    tarafından exc_info/exc_text'ten TAM olarak eklenir (kırpma yok).
    """

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)   # mesaj + (varsa) tam traceback
        if _lk_merge_extra is None:
            return base
        extra: dict = {}
        try:
            _lk_merge_extra(record, extra)
            if not extra:
                return base
            return f"{base} {json.dumps(extra, cls=_LkJsonFormatter.JsonEncoder, ensure_ascii=False)}"
        except Exception:  # noqa: BLE001 — extra serileşmezse satırı yine de yaz
            return base


def setup_file_logging() -> Path | None:
    """Root logger'a dosya handler'ı tak; dosyayı SIFIRDAN başlat (truncate).

    YALNIZ ana süreçten çağrılmalı (bkz. yukarıdaki not). Dönen değer: yazılan yol,
    kapalı/başarısızsa None.

    AGENT_LOG_FILE: yol (göreli yol worker/ dizinine göre çözülür). Tanımsız →
    worker/logs/agent.log. Boş string ("") → dosyaya HİÇ yazma (handler takılmaz).

    Root'a takılır → livekit.agents, worker.*, pi_brain, omnivoice_tts dahil TÜM
    logger'lar dosyaya düşer. Seviye ROOT'tan gelir (setup_logging WORKER_LOG_LEVEL'i
    root'a basar) → dosya terminalle BİREBİR aynı satırları görür. DedupeFilter
    logger'lara takılı (handler'a değil) → dosya da aynı seyreltilmiş akışı alır.
    """
    raw = os.getenv("AGENT_LOG_FILE")
    if raw is not None and not raw.strip():
        return None  # açıkça boş → dosya log'u kapalı
    path = Path(raw.strip()).expanduser() if raw else _DEFAULT_LOG_FILE
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path

    # Emniyet: job süreci (fork bağlamı) buraya düşerse dosyayı SIFIRLAMASIN.
    if mp.parent_process() is not None:
        return None

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # mode="w" → her başlatmada baştan yaz (kullanıcı isteği: bayat log birikmesin).
        handler = logging.FileHandler(path, mode="w", encoding="utf-8")
    except OSError as e:
        # Dosya açılamazsa (izin/dolu disk) worker AYNEN çalışsın — sadece uyar.
        logging.getLogger("worker.log").warning("log dosyası açılamadı (%s): %r", path, e)
        return None

    handler.setFormatter(
        _PlainFormatter(
            "%(asctime)s %(levelname)-8s pid=%(process)d %(name)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logging.getLogger().addHandler(handler)
    return path
