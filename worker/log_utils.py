"""log_utils — tekrarlayan logları susturan basit dedupe filtresi.

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

import logging
import os
import time


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
