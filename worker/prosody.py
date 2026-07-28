"""prosody — pencere başına prozodi/akış öznitelikleri. SADECE numpy.

NEDEN VAR: gölge kaydedici (`speaker_tap.SessionEmbLog`) pencere embedding'ini
saklıyor ama embedding zaten havuzlanmış (pooled) bir vektör — pencere İÇİNDEKİ
zaman akışı orada yok. F0 eğrisi, sesli-çerçeve oranı, zarf modülasyonu gibi
büyüklükler ham ses saklanmadığı için SONRADAN ASLA geri getirilemez; bu yüzden
şimdi, kayıt anında hesaplanıp sayı olarak yazılırlar.

KISITLAR (görev gereği, ihlal etme):
  * Yalnız numpy. Lokal ve canlı `worker/.venv`'de scipy/librosa/parselmouth YOK.
    Her şey `np.fft` ile el yazımı.
  * Ham ses YAZILMAZ — buradan yalnız hesaplanmış skaler/küçük vektörler çıkar.
  * Gerçek zamanlı yola girer: hedef ≤3 ms/pencere (embed ~20-40 ms).
    Tüm çerçeveler TEK toplu FFT ile işlenir; Python döngüsü yoktur.

YÖNTEM: 32 ms çerçeve / 20 ms hop. Çerçeveler tek `rfft` ile güç spektrumuna
çevrilir; aynı spektrumdan hem otokorelasyon (ters FFT → F0/HNR) hem de LTAS /
spektral eğim türetilir — yani spektrum bir kez hesaplanır, dört yerde kullanılır.
F0 için Boersma (1993) normalize otokorelasyonu: r(τ) çerçeve otokorelasyonu
pencere fonksiyonunun otokorelasyonuna bölünerek düzeltilir, tepe değeri hem
sesli/sessiz kararı hem de HNR = 10·log10(r/(1-r)) verir.
"""

from __future__ import annotations

import numpy as np

F0_TRAJ_LEN = 16   # f0_traj sabit uzunluğu — pencere kısa da olsa uzun da olsa 16
LTAS_BANDS = 24    # uzun-dönem ortalama spektrum bant sayısı

_FRAME_SECONDS = 0.032
_HOP_SECONDS = 0.020
_F0_MIN_HZ = 60.0
_F0_MAX_HZ = 400.0
# Boersma'nın sesli/sessiz eşiği. Normalize otokorelasyon tepe değeri bunun
# altındaysa çerçeve sessiz (gürültü/soluk) sayılır.
_VOICING_THRESHOLD = 0.45
_ENERGY_FLOOR = 1e-12
_EPS = 1e-20

# Kaydedilen alanlar: ad → eleman sayısı. `speaker_tap` npz sütunlarını buradan
# kurar, böylece alan listesi tek yerde tanımlı kalır.
SCALAR_FIELDS = (
    "f0_med", "f0_p10", "f0_p90", "voiced_ratio", "hnr",
    "alpha_ratio", "tilt", "snr_db", "env_mod_hz",
)
VECTOR_FIELDS = {"f0_traj": F0_TRAJ_LEN, "ltas": LTAS_BANDS}


def empty_features() -> dict:
    """Hesaplanamadı = NaN. `voiced_ratio=0` (gerçek sessizlik) ile karışmasın."""
    out: dict = dict.fromkeys(SCALAR_FIELDS, float("nan"))
    for name, size in VECTOR_FIELDS.items():
        out[name] = np.full(size, np.nan, dtype=np.float32)
    return out


def _band_edges(rate: float) -> np.ndarray:
    lo, hi = 50.0, min(8000.0, rate / 2.0)
    return np.geomspace(lo, hi, LTAS_BANDS + 1)


def window_features(samples, rate: int) -> dict:
    """Bir ses penceresinin prozodi özniteliklerini döndür. ASLA yükselmez."""
    try:
        # NaN/Inf içeren bozuk pencere log'u uyarı yağmuruna çevirmesin; sonuç
        # zaten NaN olur ve okuma tarafı NaN'i "hesaplanamadı" diye ayıklar.
        with np.errstate(all="ignore"):
            return _features(samples, rate)
    except Exception:  # noqa: BLE001 — ölçüm ASLA akışı bozmasın
        return empty_features()


def _features(samples, rate: int) -> dict:
    out = empty_features()
    x = np.asarray(samples, dtype=np.float64).reshape(-1)
    frame_len = round(_FRAME_SECONDS * rate)
    hop = max(1, round(_HOP_SECONDS * rate))
    if x.size < frame_len or frame_len < 32:
        return out

    frames = np.lib.stride_tricks.sliding_window_view(x, frame_len)[::hop]
    frames = frames - frames.mean(axis=1, keepdims=True)
    n_frames = frames.shape[0]
    energy = np.einsum("ij,ij->i", frames, frames) / frame_len

    win = np.hanning(frame_len)
    nfft = 1 << int(np.ceil(np.log2(2 * frame_len)))
    spec = np.fft.rfft(frames * win, n=nfft, axis=1)
    power = spec.real**2 + spec.imag**2          # (N, nfft/2+1) — dört yerde kullanılır

    _pitch(out, power, energy, n_frames, frame_len, hop, nfft, win, rate)
    _spectral(out, power, nfft, rate)
    _flow(out, energy, hop, rate)
    return out


def _pitch(out, power, energy, n_frames, frame_len, hop, nfft, win, rate) -> None:
    """Normalize otokorelasyondan F0 istatistikleri, eğrisi, HNR, sesli oranı."""
    acf = np.fft.irfft(power, n=nfft, axis=1)[:, :frame_len]
    wspec = np.fft.rfft(win, n=nfft)
    acw = np.fft.irfft(wspec.real**2 + wspec.imag**2, n=nfft)[:frame_len]
    acw = acw / max(float(acw[0]), _EPS)

    r0 = acf[:, :1].copy()
    r0[r0 <= 0.0] = np.inf                        # sessiz çerçeve → normalize acf 0
    norm = acf / r0 / np.maximum(acw, 1e-3)[None, :]

    lag_min = max(2, int(rate / _F0_MAX_HZ))
    lag_max = min(frame_len - 2, int(rate / _F0_MIN_HZ))
    if lag_max <= lag_min:
        return
    seg = norm[:, lag_min:lag_max + 1]
    rows = np.arange(n_frames)
    # OKTAV TUZAĞI: saf periyodik seste normalize otokorelasyon periyodun HER
    # katında ~1 olur; ham `argmax` rahatlıkla 2τ/3τ seçer (200 Hz sinüs → 66.7 Hz
    # ölçüldü). Boersma bunu oktav maliyetiyle çözer; burada ucuz ve deterministik
    # karşılığı: tepeye YETERİNCE yakın (≥%85) YEREL maksimumların EN KÜÇÜK
    # gecikmelisini seç. Tepe yoksa klasik argmax'a düşer.
    top = seg.max(axis=1)
    local = np.zeros(seg.shape, dtype=bool)
    local[:, 1:-1] = (seg[:, 1:-1] > seg[:, :-2]) & (seg[:, 1:-1] >= seg[:, 2:])
    local[:, 0] = seg[:, 0] >= seg[:, 1]
    ok = local & (seg >= 0.85 * top[:, None])
    idx = np.where(ok.any(axis=1), ok.argmax(axis=1), seg.argmax(axis=1))
    peak = seg[rows, idx]
    lag = idx + lag_min

    # Parabolik ara değerleme: F0 çözünürlüğü örnek ızgarasına çakılı kalmasın.
    y0, y1, y2 = norm[rows, lag - 1], peak, norm[rows, lag + 1]
    denom = y0 - 2.0 * y1 + y2
    delta = np.where(np.abs(denom) > _EPS, 0.5 * (y0 - y2) / np.where(denom == 0, 1.0, denom), 0.0)
    f0 = rate / (lag + np.clip(delta, -0.5, 0.5))

    voiced = (peak >= _VOICING_THRESHOLD) & (energy > _ENERGY_FLOOR)
    voiced &= (f0 >= _F0_MIN_HZ) & (f0 <= _F0_MAX_HZ)
    out["voiced_ratio"] = float(voiced.mean())
    if not voiced.any():
        return

    fv = f0[voiced]
    out["f0_med"] = float(np.median(fv))
    out["f0_p10"] = float(np.percentile(fv, 10))
    out["f0_p90"] = float(np.percentile(fv, 90))
    r = np.clip(peak[voiced], 1e-6, 1.0 - 1e-6)
    out["hnr"] = float(np.median(10.0 * np.log10(r / (1.0 - r))))

    # AKIŞ: F0 eğrisi sabit 16 noktaya örneklenir. Sesli çerçevelerin zaman
    # ekseninde doğrusal ara değerleme — tek sesli çerçevede sabit dizi çıkar,
    # uzunluk HER ZAMAN F0_TRAJ_LEN.
    times = (np.arange(n_frames) * hop + frame_len / 2.0) / float(rate)
    grid = np.linspace(times[0], times[-1], F0_TRAJ_LEN) if n_frames > 1 else np.full(F0_TRAJ_LEN, times[0])
    out["f0_traj"] = np.interp(grid, times[voiced], fv).astype(np.float32)


def _spectral(out, power, nfft, rate) -> None:
    """LTAS (24 bant, dB), alfa oranı ve spektral eğim (dB/oktav)."""
    ltas_power = power.mean(axis=0)
    freqs = np.fft.rfftfreq(nfft, d=1.0 / rate)

    edges = _band_edges(rate)
    bands = np.empty(LTAS_BANDS, dtype=np.float64)
    for i in range(LTAS_BANDS):
        sel = (freqs >= edges[i]) & (freqs < edges[i + 1])
        bands[i] = ltas_power[sel].mean() if sel.any() else _EPS
    out["ltas"] = (10.0 * np.log10(bands + _EPS)).astype(np.float32)

    low = (freqs >= 50.0) & (freqs < 1000.0)
    high = (freqs >= 1000.0) & (freqs < 5000.0)
    if low.any() and high.any():
        e_low, e_high = ltas_power[low].sum(), ltas_power[high].sum()
        out["alpha_ratio"] = float(10.0 * np.log10((e_high + _EPS) / (e_low + _EPS)))

    fit = (freqs >= 100.0) & (freqs <= 5000.0)
    if int(fit.sum()) >= 4:
        xs = np.log2(freqs[fit])
        ys = 10.0 * np.log10(ltas_power[fit] + _EPS)
        slope = np.polyfit(xs, ys, 1)[0]
        out["tilt"] = float(slope)                # dB / oktav (konuşmada negatif)


def _flow(out, energy, hop, rate) -> None:
    """SNR (p10 gürültü tabanı) ve zarf modülasyon tepesi ≈ hece hızı."""
    floor = float(np.percentile(energy, 10))
    out["snr_db"] = float(10.0 * np.log10((float(energy.mean()) + _EPS) / (floor + _EPS)))

    if energy.size < 8:
        return
    env = np.sqrt(np.maximum(energy, 0.0))
    env = env - env.mean()
    frame_rate = float(rate) / float(hop)
    nfft = 1 << int(np.ceil(np.log2(4 * env.size)))
    mag = np.abs(np.fft.rfft(env, n=nfft))
    mod_freqs = np.fft.rfftfreq(nfft, d=1.0 / frame_rate)
    band = (mod_freqs >= 2.0) & (mod_freqs <= 10.0)   # hece hızı aralığı
    if band.any() and mag[band].max() > 0.0:
        out["env_mod_hz"] = float(mod_freqs[band][int(mag[band].argmax())])
