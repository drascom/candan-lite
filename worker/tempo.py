"""tempo — konuşma hızını PERDEYİ BOZMADAN değiştiren akış filtresi (WSOLA).

NEDEN VAR (27 Tem, canlı şikâyet): kullanıcı "biraz daha hızlı konuşabilir misin"
dedi, Candan "hızımı artırıyorum" dedi ve **tempo değişmedi, değişemezdi**. Elinde
hız kolu yoktu. Ölçüm iki yolu karşılaştırdı:

  * Higgs `<|prosody:speed_fast|>` token'ı — bedava ama etkisi ölçülemez kadar zayıf
    (kelime/saniye kazancı %5'in altında; `experiments/konusma-hizi/` tablosu).
  * `higgs-tts` ucunun `speed` parametresi — sözleşmede YAZIYOR ama sunucu kodu
    (`server/higgs-tts/server.py`) onu HİÇ OKUMUYOR; motor hız kabul etmiyor.

Geriye tek gerçek yol kaldı: üretilen PCM'in temposunu worker'da değiştirmek.
**Basit resample KABUL EDİLMEZ** — o perdeyi de kaydırır, Candan'ın sesi değişir.
WSOLA (waveform similarity overlap-add) perdeyi koruyarak yalnız temposu değiştirir.

⚠️ STREAMING'E DOKUNMAZ. Bu filtre kodek çözücüsünün ÇIKIŞINDA durur; blok
(8 kare) / lookahead (8 kare) / sol bağlam (16 kare) mantığı olduğu gibi kalır.
İlk ses gecikmesine katkısı da bu yüzden yok denecek kadar az: filtre ilk çıktıyı
verebilmek için `delta + N + Hs` ≈ 55 ms girdi ister, ama sunucudan gelen İLK BLOK
zaten 320 ms — yani bekleme İLK BLOĞUN İÇİNDE soğurulur, ek tur beklenmez.
Ölçüldü (`experiments/konusma-hizi/`): ilk ses 0.55 s → 0.55 s.

ALGORİTMA (klasik WSOLA):
    N   = 30 ms analiz penceresi (periyodik Hann, %50 bindirme → toplam kazanç 1)
    Hs  = N/2 sentez adımı  ·  Ha = Hs × oran analiz adımı
    Her karede analiz konumu ±10 ms aranır; bir ÖNCEKİ karenin "doğal devamı"
    (şablon) ile çapraz korelasyonu en yüksek konum seçilir. Böylece bindirme
    perde periyoduna HİZALI olur → ne yankı ne metalik tını.
Arama normalize edilmiş korelasyonla yapılır: aksi hâlde en gürültülü konum
kazanır ve sessizlikten sonraki ilk hece kayar.

KUYRUK: son karenin ötesinde kalan ≤55 ms girdi HAM olarak eklenir (kırpılmaz).
Cümle sonundaki bu artık genelde sönümlenen ses/sessizliktir; kırpmak son heceyi
yiyebilirdi — "şüphede kalırsak süresi 1-2% kayar, hece kaybolmaz".
"""
from __future__ import annotations

import numpy as np

SAMPLE_RATE = 24000

# Pencere ve arama boyu. 30 ms pencere kadın sesinin (F0 ≈ 200 Hz) 6 perde
# periyodunu içerir; ±10 ms arama en pes erkek sesinde bile (100 Hz → 10 ms)
# tam bir periyot kaydırmaya yeter.
_FRAME_MS = 30.0
_SEARCH_MS = 10.0
_EPS = 1e-9


class TempoStream:
    """Parça parça beslenen tempo dönüştürücü. `rate>1` HIZLANDIRIR.

    `feed()` her çağrıda ELDEKİ kadarını döner (girdiyle birebir hizalı değil,
    filtre birkaç kare geriden gelir); `flush()` kalanı boşaltır ve durumu sıfırlar.
    `rate == 1.0` ise hiçbir işlem yapılmaz — bayt bayt AYNI PCM döner.
    """

    def __init__(self, rate: float = 1.0, sample_rate: int = SAMPLE_RATE) -> None:
        self.rate = float(rate)
        self.sample_rate = int(sample_rate)
        self._n = max(int(self.sample_rate * _FRAME_MS / 1000.0) // 2 * 2, 4)
        self._hs = self._n // 2
        self._ha = self._hs * self.rate
        self._delta = max(int(self.sample_rate * _SEARCH_MS / 1000.0), 1)
        i = np.arange(self._n, dtype=np.float32)
        # PERİYODİK Hann: w[k] + w[k+N/2] = 1 (tam), yani %50 bindirmede kazanç 1.
        self._win = (0.5 - 0.5 * np.cos(2.0 * np.pi * i / self._n)).astype(np.float32)
        self._reset()

    # ── durum ────────────────────────────────────────────────────────────────
    def _reset(self) -> None:
        self._buf = np.zeros(0, dtype=np.float32)
        self._pos = 0.0            # analiz konumu (buf koordinatı, kesirli)
        self._tail: np.ndarray | None = None   # bindirme taşıması (Hs örnek)
        self._tpl: np.ndarray | None = None    # bir önceki karenin doğal devamı
        self._consumed = 0         # `_tail`'in kapsadığı son girdi örneği
        # s16le'de bir örnek 2 bayt ama TCP parçası keyfî sınırdan gelebilir.
        # Tek sayılı kuyruk BİR SONRAKİ parçaya devredilir (çağıran hizalasa bile
        # filtre kendi başına doğru olsun — yarım örnek ses kaydırır, çıtırdatır).
        self._odd = b""

    @property
    def passthrough(self) -> bool:
        return abs(self.rate - 1.0) < 1e-6

    # ── akış ─────────────────────────────────────────────────────────────────
    def feed(self, pcm: bytes) -> bytes:
        """s16le PCM parçası ver → hazır olan s16le PCM parçasını al."""
        if self.passthrough or not pcm:
            return pcm
        raw = self._odd + pcm
        cut = len(raw) - (len(raw) % 2)
        self._odd = raw[cut:]
        raw = raw[:cut]
        if not raw:
            return b""
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32)
        self._buf = np.concatenate((self._buf, x)) if self._buf.size else x
        return _to_pcm(self._process(final=False))

    def flush(self) -> bytes:
        """Kalanı boşalt ve durumu sıfırla (aynı nesne yeni cümlede kullanılabilir)."""
        if self.passthrough:
            return b""
        parts = [self._process(final=True)]
        if self._tail is not None:
            parts.append(self._tail)
        if self._consumed < self._buf.size:
            # Kuyruk: son karenin ötesindeki ≤55 ms HAM eklenir (bkz. modül başı).
            parts.append(self._buf[self._consumed:])
        out = np.concatenate([p for p in parts if p.size]) if parts else np.zeros(0, np.float32)
        self._reset()
        return _to_pcm(out)

    # ── çekirdek ─────────────────────────────────────────────────────────────
    def _process(self, *, final: bool) -> np.ndarray:
        n, hs, delta = self._n, self._hs, self._delta
        outs: list[np.ndarray] = []
        buf = self._buf
        while True:
            ideal = int(self._pos)
            lo = max(ideal - delta, 0)
            hi = ideal + delta
            # Şablonu güncelleyebilmek için kare + Hs kadar ileri görmek gerekir.
            if buf.size < hi + n + hs:
                if not final:
                    break
                hi = min(hi, buf.size - n - hs)
                if hi < lo or ideal + n + hs > buf.size:
                    break
            p = ideal if self._tpl is None else lo + _best_offset(
                buf[lo:hi + n], self._tpl, n
            )
            frame = buf[p:p + n] * self._win
            if self._tail is None:
                # İLK kare: ilk yarıyı PENCERELEME. Aksi hâlde her cümle 15 ms'lik
                # bir fade-in ile başlar ve ilk hece yumuşar.
                outs.append(buf[p:p + hs].copy())
            else:
                outs.append(self._tail + frame[:hs])
            self._tail = frame[hs:]
            self._tpl = buf[p + hs:p + hs + n]
            self._consumed = p + n
            self._pos += self._ha

        # Artık gerekmeyen girdiyi at (bellek sabit kalsın).
        keep = max(min(int(self._pos) - delta, self._consumed), 0)
        if keep:
            self._buf = self._buf[keep:]
            self._pos -= keep
            self._consumed -= keep
            if self._tpl is not None:
                self._tpl = self._tpl.copy()   # dilim eski tampona bakmasın
        return np.concatenate(outs) if outs else np.zeros(0, dtype=np.float32)


def _best_offset(seg: np.ndarray, tpl: np.ndarray, n: int) -> int:
    """`seg` içinde `tpl`'e en çok benzeyen pencerenin başlangıcı (NORMALİZE korelasyon).

    Normalizasyon şart: ham korelasyon en GÜRÜLTÜLÜ konumu seçer, bu da
    sessizlikten sonraki ilk heceyi kaydırıyordu.
    """
    k = seg.size - n + 1
    if k <= 1 or tpl.size != n:
        return 0
    corr = np.correlate(seg, tpl, mode="valid")[:k]
    # Kayan pencere enerjisi: kümülatif kareler farkı (O(len) — döngü yok).
    cs = np.concatenate(([0.0], np.cumsum(seg.astype(np.float64) ** 2)))
    energy = np.sqrt(np.maximum(cs[n:n + k] - cs[:k], 0.0)) + _EPS
    return int(np.argmax(corr / energy))


def _to_pcm(arr: np.ndarray) -> bytes:
    if arr.size == 0:
        return b""
    return np.clip(arr, -32768.0, 32767.0).astype("<i2").tobytes()


def change(pcm: bytes, rate: float, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Tek seferlik dönüşüm (ölçüm/test için). Akışta `TempoStream` kullanılır."""
    ts = TempoStream(rate, sample_rate)
    return ts.feed(pcm) + ts.flush()


__all__ = ["SAMPLE_RATE", "TempoStream", "change"]
