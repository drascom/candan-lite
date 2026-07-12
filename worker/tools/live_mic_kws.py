#!/usr/bin/env python3
"""
live_mic_kws.py — canli mikrofon KWS teshis araci (sherpa-onnx zipformer gigaspeech 3.3M).

DURUM: referans wav'da 'forever' TETIKLENIYOR, ama canli mikrofonda TETIKLENMIYOR.
=> model/encode/decoder saglam; supheli olan MIKROFON BESLEME YOLU.

Bu arac A/B testi yapar: mikrofondan kaydeder, kaydettigi BIREBIR ayni sinyali
offline yoldan KWS'e verir, sonra grid + gain sweep kosar. Boylece "mikrofon
yolu mu bozuk, yoksa esik/gain mi yetersiz" sorusu kesin cevaplanir.

TEK KOMUT (kullanici):
    python live_mic_kws.py --record

    -> PASS A: cihaz 16 kHz'te acilir (CoreAudio kendi resample'ini yapar)
    -> PASS B: cihaz NATIVE rate'inde (48 kHz) acilir, 16k'ya BIZ resample ederiz
    Her pass'te 6 sn "forever" de. Arac kendi kendine teshis eder.

SEVIYE (canli mikrofon cok kisik: peak ~0.030 vs referans 1.wav peak 0.4235):
    python live_mic_kws.py --agc                    # otomatik seviye (ONERILEN)
    python live_mic_kws.py --gain 14                # sabit gain
    Metre: level: HAM -> KWS'e giden (gain Nx) [bar] peak_raw / peak_out

Diger modlar:
    python live_mic_kws.py --list-devices
    python live_mic_kws.py --check-wav              # mikrofonsuz pipeline dogrulamasi
    python live_mic_kws.py --analyze <file.wav>     # var olan bir wav'i teshis et
    python live_mic_kws.py                          # eski canli mod (sadece dinle)
"""

import argparse
import math
import sys
import tarfile
import time
import urllib.request
from collections import deque
from pathlib import Path

import numpy as np
import sherpa_onnx

MODEL_NAME = "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"
MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/"
    f"{MODEL_NAME}.tar.bz2"
)

HERE = Path(__file__).resolve().parent

# Wake word adaylari. "forever" = KONTROL (pipeline saglam mi?).
KEYWORDS = ["forever", "jackie", "hey jackie"]
CONTROL = "FOREVER"

SAMPLE_RATE = 16000
CHUNK = int(SAMPLE_RATE * 0.1)  # 100 ms

# Teshis matrisi
GRID_THRESHOLDS = (0.30, 0.25, 0.20, 0.10, 0.05)
GRID_SCORES = (1.5, 2.0, 3.0, 4.0, 5.0, 6.0)

# AGC varsayilanlari — referans 1.wav peak=0.4235 oldugu icin hedef 0.40
AGC_TARGET = 0.40      # hedef peak
AGC_GATE_RMS = 0.003   # bunun altinda "sessizlik" -> kazanc SABIT tutulur
AGC_MAX_GAIN = 64.0
AGC_MIN_GAIN = 1.0
AGC_WINDOW = 30        # kac chunk (100 ms) uzerinden peak-hold (~3 s = bir sozce)
AGC_ATTACK = 0.35      # kazanci ARTIRIRKEN adim orani (kademeli, ani sicrama yok)
AGC_RELEASE = 0.15     # kazanci DUSURURKEN adim orani
AGC_CEIL = 0.90        # aninda fren: peak_out bunu asarsa kazanc hemen kisilir


# --------------------------------------------------------------------------
# 0) Gain / AGC katmani — mikrofon chunk'i KWS'e gitmeden ONCE buradan gecer
# --------------------------------------------------------------------------
class GainStage:
    """Sabit gain (--gain) ve/veya otomatik seviye (--agc).

    AGC: son ~1 s'lik pencerede peak takip edilir; konusma varken
    (RMS > AGC_GATE_RMS) hedef kazanc = AGC_TARGET / pencere_peak olarak
    hesaplanir ve mevcut kazanc oraya KADEMELI (AGC_SMOOTH) yaklastirilir.
    Sessizlikte kazanc dondurulur -> gurultu sisirilmez.
    """

    def __init__(self, fixed: float = 1.0, agc: bool = False,
                 target: float = AGC_TARGET, gate: float = AGC_GATE_RMS):
        self.fixed = float(fixed)
        self.agc = bool(agc)
        self.target = float(target)
        self.gate = float(gate)
        self.auto = 1.0                       # AGC'nin bulundugu kazanc
        self.peaks: deque[float] = deque(maxlen=AGC_WINDOW)
        self.clip_blocks = 0
        self.clip_warned = False

    @property
    def gain(self) -> float:
        return self.fixed * (self.auto if self.agc else 1.0)

    def process(self, x: np.ndarray) -> tuple[np.ndarray, dict]:
        """chunk -> (KWS'e verilecek chunk, metrikler)"""
        rms_raw = float(np.sqrt(np.mean(x**2))) if x.size else 0.0
        peak_raw = float(np.max(np.abs(x))) if x.size else 0.0

        if self.agc:
            if rms_raw > self.gate:
                # peak-hold penceresi SADECE konusma chunk'lariyla beslenir;
                # boylece pencere sessizlikteki kucuk peak'lerle asagi cekilmez.
                self.peaks.append(peak_raw)
                win_peak = max(self.peaks)
                if win_peak > 1e-6:
                    want = min(max(self.target / win_peak, AGC_MIN_GAIN), AGC_MAX_GAIN)
                    # kademeli yaklas (ani sicrama yok)
                    step = AGC_ATTACK if want > self.auto else AGC_RELEASE
                    self.auto += (want - self.auto) * step
            # else: sessizlik -> kazanci SABIT tut (gurultuyu sisirme)

            # guvenlik freni: cikis tavani asiyorsa kazanci ANINDA kis (clip'ten once)
            if peak_raw * self.gain > AGC_CEIL:
                self.auto = AGC_CEIL / max(peak_raw * self.fixed, 1e-9)

        g = self.gain
        y = x * g if g != 1.0 else x
        peak_out = float(np.max(np.abs(y))) if y.size else 0.0
        clipped = peak_out > 1.0
        if clipped:
            self.clip_blocks += 1
            y = np.clip(y, -1.0, 1.0)
            peak_out = 1.0
        y = np.ascontiguousarray(y, dtype=np.float32)

        rms_out = float(np.sqrt(np.mean(y**2))) if y.size else 0.0
        return y, {
            "rms_raw": rms_raw, "peak_raw": peak_raw,
            "rms_out": rms_out, "peak_out": peak_out,
            "gain": g, "clipped": clipped,
        }

    def clip_note(self) -> str:
        if self.clip_blocks and not self.clip_warned:
            self.clip_warned = True
        return f"  !! CLIPPING ({self.clip_blocks} blok kirpildi)" if self.clip_blocks else ""


# --------------------------------------------------------------------------
# 1) Model bul / indir
# --------------------------------------------------------------------------
def find_model() -> tuple[Path, str]:
    """Modeli lokalde ara; yoksa indir. (path, 'local'|'downloaded') doner."""
    search_roots = [
        HERE,
        Path("/Users/drascom/work/candan-lite/worker"),
        Path("/Users/drascom/work/candan-lite/worker/models"),
        Path.home() / ".cache" / "sherpa-onnx",
        Path.home() / ".cache",
        Path("/Users/drascom/work"),
    ]
    for root in search_roots:
        if not root.is_dir():
            continue
        cand = root / MODEL_NAME
        if (cand / "tokens.txt").is_file():
            return cand, "local"
        try:
            for child in root.iterdir():
                cand = child / MODEL_NAME
                if (cand / "tokens.txt").is_file():
                    return cand, "local"
        except PermissionError:
            pass

    dest = HERE / MODEL_NAME
    tarball = HERE / f"{MODEL_NAME}.tar.bz2"
    print(f"[model] lokalde bulunamadi, indiriliyor:\n        {MODEL_URL}")
    HERE.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(MODEL_URL, tarball)
    with tarfile.open(tarball, "r:bz2") as tf:
        tf.extractall(HERE)
    tarball.unlink(missing_ok=True)
    if not (dest / "tokens.txt").is_file():
        sys.exit(f"[model] HATA: indirme sonrasi {dest}/tokens.txt yok")
    return dest, "downloaded"


# --------------------------------------------------------------------------
# 2) Keyword encode (tokens.txt + bpe.model -> BPE token dizisi)
# --------------------------------------------------------------------------
def encode_keywords(model_dir: Path, out_path: Path) -> list[tuple[str, str]]:
    tokens = str(model_dir / "tokens.txt")
    bpe = str(model_dir / "bpe.model")
    # GigaSpeech transcript'leri BUYUK HARF; BPE de ona gore egitilmis.
    texts = [k.upper() for k in KEYWORDS]
    encoded = sherpa_onnx.text2token(
        texts, tokens=tokens, tokens_type="bpe", bpe_model=bpe
    )
    lines = [" ".join(toks) for toks in encoded]
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return list(zip(KEYWORDS, lines))


# --------------------------------------------------------------------------
# 3) Spotter kurulumu
# --------------------------------------------------------------------------
def build_spotter(model_dir: Path, keywords_file: Path, threshold: float, score: float):
    def m(stem: str) -> str:
        return str(model_dir / f"{stem}-epoch-12-avg-2-chunk-16-left-64.onnx")

    return sherpa_onnx.KeywordSpotter(
        tokens=str(model_dir / "tokens.txt"),
        encoder=m("encoder"),
        decoder=m("decoder"),
        joiner=m("joiner"),
        keywords_file=str(keywords_file),
        num_threads=2,
        sample_rate=SAMPLE_RATE,
        feature_dim=80,
        max_active_paths=4,
        keywords_score=score,
        keywords_threshold=threshold,
        num_trailing_blanks=1,
        provider="cpu",
    )


# --------------------------------------------------------------------------
# 4) OFFLINE besleme yolu — referans wav ve mic_capture.wav AYNI yoldan gecer
# --------------------------------------------------------------------------
def spot_audio(spotter, audio: np.ndarray, sr: int = SAMPLE_RATE) -> list[str]:
    """float32 [-1,1] mono dizi -> tetiklenen keyword listesi."""
    audio = np.ascontiguousarray(audio, dtype=np.float32)
    stream = spotter.create_stream()
    stream.accept_waveform(sr, audio)
    stream.accept_waveform(sr, np.zeros(int(0.5 * sr), dtype=np.float32))  # tail
    stream.input_finished()

    hits = []
    while spotter.is_ready(stream):
        spotter.decode_stream(stream)
        r = spotter.get_result(stream)
        if r:
            hits.append(r)
            spotter.reset_stream(stream)
    return hits


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    import soundfile as sf

    audio, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio[:, 0]
    return np.ascontiguousarray(audio, dtype=np.float32), int(sr)


def has_control(hits: list[str]) -> bool:
    return any(CONTROL in h.upper().replace(" ", "") for h in hits)


# --------------------------------------------------------------------------
# 5) Kendi resampler'imiz (scipy/soxr yok) — polyphase windowed-sinc FIR
#    CoreAudio'nun kendi donusumune GUVENMEMEK icin.
# --------------------------------------------------------------------------
def resample_to_16k(x: np.ndarray, sr_in: int) -> np.ndarray:
    if sr_in == SAMPLE_RATE:
        return np.ascontiguousarray(x, dtype=np.float32)

    g = math.gcd(int(sr_in), SAMPLE_RATE)
    up = SAMPLE_RATE // g
    down = int(sr_in) // g

    # Anti-alias lowpass: upsample edilmis rate'te normalize kesim frekansi
    m = max(up, down)
    cutoff = 0.5 / m           # Nyquist'in m kati altinda
    half = 20 * m              # 20 lob -> yeterince keskin
    n = np.arange(-half, half + 1, dtype=np.float64)
    h = 2 * cutoff * np.sinc(2 * cutoff * n)
    h *= np.hamming(h.size)
    h *= up / h.sum()          # zero-stuff kaybini telafi et (DC gain = up)

    # up-sample (zero stuffing)
    if up > 1:
        y = np.zeros(x.size * up, dtype=np.float64)
        y[::up] = x
    else:
        y = x.astype(np.float64)

    y = np.convolve(y, h, mode="same")
    y = y[::down]
    return np.ascontiguousarray(y, dtype=np.float32)


# --------------------------------------------------------------------------
# 6) Sinyal istatistikleri
# --------------------------------------------------------------------------
def describe(audio: np.ndarray, sr: int, label: str, wall_s: float | None = None) -> None:
    rms = float(np.sqrt(np.mean(audio**2))) if audio.size else 0.0
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    dc = float(np.mean(audio)) if audio.size else 0.0
    dur = audio.size / sr
    dbfs = 20 * math.log10(rms) if rms > 0 else -999
    print(f"[wav]   {label}")
    print(f"[wav]     dtype={audio.dtype}  sr={sr} Hz  n={audio.size}  sure={dur:.2f} s")
    print(f"[wav]     RMS={rms:.4f} ({dbfs:.1f} dBFS)  peak={peak:.4f}  DC={dc:+.5f}")
    if wall_s:
        eff = audio.size / wall_s
        warn = ""
        if abs(eff - sr) / sr > 0.05:
            warn = "  <<< !! ISTENEN SR ILE GERCEK SR UYUSMUYOR"
        print(f"[wav]     duvar-saati={wall_s:.2f} s -> efektif sr={eff:.0f} Hz{warn}")
    if peak >= 0.999:
        print("[wav]     !! KIRPILMA (clipping) var")
    if peak > 0 and rms / peak < 0.02:
        print("[wav]     !! sinyal cok seyrek/sessiz gorunuyor")
    if abs(dc) > 0.01:
        print("[wav]     !! belirgin DC offset")


# --------------------------------------------------------------------------
# 7) Teshis: offline KWS + grid sweep + gain sweep
# --------------------------------------------------------------------------
def gain_variants(audio: np.ndarray) -> list[tuple[str, np.ndarray]]:
    peak = float(np.max(np.abs(audio))) or 1.0
    dcrm = audio - float(np.mean(audio))
    dcpk = float(np.max(np.abs(dcrm))) or 1.0
    return [
        ("1x", audio),
        ("2x", audio * 2.0),
        ("4x", audio * 4.0),
        ("8x", audio * 8.0),
        ("pknorm", audio * (0.95 / peak)),
        ("dc+pk", dcrm * (0.95 / dcpk)),
    ]


def diagnose(model_dir: Path, keywords_file: Path, audio: np.ndarray, sr: int,
             threshold: float, score: float, label: str) -> bool:
    """(b) offline KWS  (c) grid sweep  (d) gain testi. True = kontrol tetiklendi."""
    if sr != SAMPLE_RATE:
        print(f"[diag]  {sr} Hz -> 16 kHz resample ediliyor (teshis icin)")
        audio = resample_to_16k(audio, sr)
        sr = SAMPLE_RATE

    print()
    print(f"===== TESHIS: {label} =====")

    # (b) offline KWS — referans wav ile AYNI yol
    base = build_spotter(model_dir, keywords_file, threshold, score)
    hits = spot_audio(base, audio, sr)
    ok = has_control(hits)
    print(f"[diag]  offline KWS (thr={threshold} score={score}): "
          f"{hits if hits else '(tetiklenme yok)'}")
    print(f"[diag]  KONTROL 'forever' -> {'TETIKLENDI' if ok else 'YOK'}")

    # (c)+(d) grid x gain matrisi
    variants = gain_variants(audio)
    print()
    print("[grid]  satir=threshold, sutun=score, hucre=tetikleyen gain'ler ('.' = hic)")
    w = 26
    header = "  thr\\score |" + "".join(f"{s:>{w}}" for s in GRID_SCORES)
    print(header)
    print("  " + "-" * (len(header) - 2))

    any_hit = False
    gain_hits: dict[str, int] = {tag: 0 for tag, _ in variants}
    for thr in GRID_THRESHOLDS:
        cells = []
        for s in GRID_SCORES:
            sp = build_spotter(model_dir, keywords_file, thr, s)
            tags = []
            for tag, v in variants:
                if has_control(spot_audio(sp, v, sr)):
                    tags.append(tag)
                    gain_hits[tag] += 1
                    any_hit = True
            cells.append(",".join(tags) if tags else ".")
        print(f"  {thr:>9} |" + "".join(f"{c:>{w}}" for c in cells))

    print()
    if not any_hit:
        print("[grid]  !! HICBIR (threshold,score,gain) kombinasyonunda 'forever' "
              "tetiklenmedi.")
        print("[grid]     => sorun esik/gain DEGIL. Sinyalin KENDISI bozuk "
              "(sample rate / hiz / ic erik).")
        print("[grid]     mic_capture wav'ini DINLE: hizli/tiz (chipmunk) mi? "
              "-> sample rate hatasi.")
    else:
        best = sorted((c for c, n in gain_hits.items() if n), key=lambda c: -gain_hits[c])
        print(f"[grid]  tetikleyen gain'ler (kac hucrede): "
              + ", ".join(f"{t}={gain_hits[t]}" for t in best))
        if gain_hits.get("1x", 0) == 0 and any(gain_hits[t] for t in
                                               ("2x", "4x", "8x", "pknorm", "dc+pk")):
            print("[grid]  => 1x (ham sinyal) HIC tetiklemiyor ama yukseltilmis "
                  "sinyal tetikliyor:")
            print("[grid]     SORUN GAIN — mikrofon sinyali cok zayif. "
                  "Canli yolda gain uygula veya mic seviyesini artir.")
    return ok


# --------------------------------------------------------------------------
# 8) KONTROL: referans wav (regresyon)
# --------------------------------------------------------------------------
def check_wav(model_dir: Path, keywords_file: Path, threshold: float, score: float) -> bool:
    wav = model_dir / "test_wavs" / "1.wav"  # iceriginde "...FOR EVER..." geciyor
    if not wav.is_file():
        print(f"[check] referans wav yok, atlaniyor: {wav}")
        return True

    audio, sr = read_wav(wav)
    spotter = build_spotter(model_dir, keywords_file, threshold, score)
    hits = spot_audio(spotter, audio, sr)
    ok = has_control(hits)
    rms = float(np.sqrt(np.mean(audio**2)))
    peak = float(np.max(np.abs(audio)))
    print(f"[check] referans {wav.name}: sr={sr} RMS={rms:.4f} peak={peak:.4f} "
          f"-> {hits if hits else '(tetiklenme yok)'}")
    if ok:
        print("[check] OK — KONTROL 'forever' yakalandi. Model+encode+decoder SAGLAM.")
        print("[check]        (referans RMS'i mikrofon RMS'i ile KARSILASTIR — "
              "buyuk fark varsa gain sorunu)")
        return True

    print(f"[check] !! REGRESYON: thr={threshold} score={score} ile referans wav bile "
          "tetiklemiyor.")
    for thr in (0.20, 0.15, 0.10):
        for s in (2.0, 3.0, 4.0, 5.0, 6.0):
            if has_control(spot_audio(
                    build_spotter(model_dir, keywords_file, thr, s), audio, sr)):
                print(f"[check]    -> CALISAN AYAR: --threshold {thr} --score {s}")
                return False
    print("[check]    sweep'te de tetiklenmedi.")
    return False


# --------------------------------------------------------------------------
# 9) Mikrofon kaydi (KWS'e verilen sinyalin BIREBIR aynisi wav'a yazilir)
# --------------------------------------------------------------------------
def bar(level: float, width: int = 12) -> str:
    n = int(min(level / 0.1, 1.0) * width)
    return "#" * n + "-" * (width - n)


def meter(m: dict, peak_raw_max: float, peak_out_max: float, extra: str = "") -> str:
    return (f"\rlevel: {m['rms_raw']:.3f} -> {m['rms_out']:.3f} "
            f"(gain {m['gain']:.1f}x) [{bar(m['rms_out'])}] "
            f"peak_raw:{peak_raw_max:.3f} peak_out:{peak_out_max:.2f}"
            f"{' CLIP' if m['clipped'] else ''}{extra}   ")


def record(spotter, device, seconds: float, open_sr: int, out_path: Path,
           label: str, stage: "GainStage | None" = None) -> tuple[np.ndarray, float]:
    """Mikrofonu open_sr'de acar, seconds kadar kaydeder, 16k mono float32 dondurur.
    Kayit sirasinda CANLI KWS de beslenir (canli vs offline farkini gormek icin).
    Donen dizi ile out_path'e yazilan wav BIREBIR AYNIDIR."""
    import sounddevice as sd
    import soundfile as sf

    blocksize = int(open_sr * 0.1)  # 100 ms
    print()
    print(f"===== KAYIT: {label} =====")
    print(f"[mic]   cihaz acilis sr : {open_sr} Hz  (blok {blocksize} frame)")
    print(f"[mic]   {seconds:.0f} saniye boyunca NET sekilde 'forever' de "
          f"(2-3 kez tekrarla).")
    for i in (3, 2, 1):
        print(f"\r[mic]   baslamaya {i}...", end="", flush=True)
        time.sleep(1)
    print("\r[mic]   >>> KONUS <<<                    ")

    if stage is None:
        stage = GainStage()

    blocks: list[np.ndarray] = []
    live_hits: list[str] = []
    kws_stream = spotter.create_stream()
    peak_raw_max = 0.0
    peak_out_max = 0.0
    t0 = time.time()

    with sd.InputStream(channels=1, dtype="float32", samplerate=open_sr,
                        device=device, blocksize=blocksize) as mic:
        while time.time() - t0 < seconds:
            samples, overflowed = mic.read(blocksize)
            samples = np.ascontiguousarray(samples.reshape(-1), dtype=np.float32)
            blocks.append(samples.copy())  # HAM kayit (diagnose kendi gain sweep'ini yapar)

            fed, m = stage.process(samples)
            peak_raw_max = max(peak_raw_max, m["peak_raw"])
            peak_out_max = max(peak_out_max, m["peak_out"])

            # CANLI KWS: cihaz zaten 16k'daysa dogrudan besle (mevcut canli yol).
            if open_sr == SAMPLE_RATE:
                kws_stream.accept_waveform(SAMPLE_RATE, fed)
                while spotter.is_ready(kws_stream):
                    spotter.decode_stream(kws_stream)
                    r = spotter.get_result(kws_stream)
                    if r:
                        live_hits.append(f"{r} @gain={m['gain']:.1f}x "
                                         f"peak_out={m['peak_out']:.2f}")
                        spotter.reset_stream(kws_stream)

            flag = " OVERFLOW" if overflowed else ""
            left = seconds - (time.time() - t0)
            print(meter(m, peak_raw_max, peak_out_max,
                        f" kalan:{max(left,0):.1f}s{flag}"), end="", flush=True)

    wall = time.time() - t0
    print("\r" + " " * 100 + "\r", end="")
    if stage.clip_blocks:
        print(f"[mic]  {stage.clip_note().strip()}")

    raw = np.concatenate(blocks) if blocks else np.zeros(0, dtype=np.float32)
    print(f"[mic]   ham kayit: {raw.size} frame @ {open_sr} Hz, duvar-saati {wall:.2f} s")
    describe(raw, open_sr, f"HAM (cihaz {open_sr} Hz)", wall_s=wall)

    if open_sr != SAMPLE_RATE:
        print(f"[mic]   KENDI resampler'imiz: {open_sr} -> {SAMPLE_RATE} Hz "
              f"(polyphase windowed-sinc)")
        audio16 = resample_to_16k(raw, open_sr)
    else:
        audio16 = raw

    # KWS'e verilen sinyalin BIREBIR aynisi diske yazilir.
    sf.write(str(out_path), audio16, SAMPLE_RATE, subtype="FLOAT")
    print(f"[mic]   yazildi: {out_path}")
    if live_hits:
        print(f"[mic]   CANLI KWS tetiklemesi: {live_hits}")
    elif open_sr == SAMPLE_RATE:
        print("[mic]   CANLI KWS: tetiklenme YOK")
    else:
        print("[mic]   CANLI KWS: bu pass'te calistirilmadi (native rate -> offline test)")
    return audio16, wall


# --------------------------------------------------------------------------
# 10) Eski canli mod
# --------------------------------------------------------------------------
def live(spotter, device, stage: "GainStage | None" = None):
    import sounddevice as sd

    if stage is None:
        stage = GainStage()

    info = sd.query_devices(device if device is not None else sd.default.device[0], "input")
    print(f"[mic]   cihaz    : [{info['index']}] {info['name']}")
    print(f"[mic]   native sr: {info['default_samplerate']:.0f} Hz  "
          f"(istenen: {SAMPLE_RATE} Hz)")
    mode = []
    if stage.fixed != 1.0:
        mode.append(f"sabit gain {stage.fixed:.1f}x")
    if stage.agc:
        mode.append(f"AGC hedef peak {stage.target:.2f} (gate RMS {stage.gate:.3f})")
    print(f"[mic]   seviye   : {', '.join(mode) if mode else 'ham (gain yok)'}")
    print()
    print("Konus. Ctrl+C ile cik.  (teshis icin: --record)")
    print()

    stream = spotter.create_stream()
    last_print = 0.0
    peak_raw_max = 0.0
    peak_out_max = 0.0

    with sd.InputStream(channels=1, dtype="float32", samplerate=SAMPLE_RATE,
                        device=device, blocksize=CHUNK) as mic:
        while True:
            samples, overflowed = mic.read(CHUNK)
            samples = np.ascontiguousarray(samples.reshape(-1), dtype=np.float32)

            fed, m = stage.process(samples)
            peak_raw_max = max(peak_raw_max, m["peak_raw"])
            peak_out_max = max(peak_out_max, m["peak_out"])

            stream.accept_waveform(SAMPLE_RATE, fed)
            while spotter.is_ready(stream):
                spotter.decode_stream(stream)
                result = spotter.get_result(stream)
                if result:
                    print(f"\r{' ' * 100}\r", end="")
                    print(f">>> TETIKLENDI: {result!r}   "
                          f"gain={m['gain']:.1f}x  peak_out={m['peak_out']:.2f}  "
                          f"peak_raw={m['peak_raw']:.3f}  "
                          f"(t={time.strftime('%H:%M:%S')})", flush=True)
                    spotter.reset_stream(stream)

            now = time.time()
            if now - last_print >= 0.2:
                flag = " OVERFLOW" if overflowed else ""
                print(meter(m, peak_raw_max, peak_out_max, flag), end="", flush=True)
                last_print = now


# --------------------------------------------------------------------------
def device_native_sr(device) -> int:
    import sounddevice as sd

    idx = device if device is not None else sd.default.device[0]
    info = sd.query_devices(idx, "input")
    print(f"[mic]   cihaz          : [{idx}] {info['name']}")
    print(f"[mic]   default_samplerate (CIHAZIN GERCEGI): "
          f"{info['default_samplerate']:.0f} Hz")
    print(f"[mic]   KWS'in bekledigi                    : {SAMPLE_RATE} Hz")
    if int(info["default_samplerate"]) != SAMPLE_RATE:
        print(f"[mic]   -> 16 kHz istendiginde CoreAudio resample yapiyor. "
              f"PASS B bunu BYPASS eder.")
    return int(info["default_samplerate"])


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--threshold", type=float, default=0.20,
                   help="keywords_threshold (default: 0.20; DUSUR = daha hassas)")
    p.add_argument("--score", type=float, default=4.0,
                   help="keywords_score (default: 4.0; ARTIR = daha hassas)")
    p.add_argument("--device", type=int, default=None,
                   help="sounddevice input index (--list-devices ile bak)")
    p.add_argument("--list-devices", action="store_true")
    p.add_argument("--gain", type=float, default=1.0, metavar="X",
                   help="mikrofon chunk'ini KWS'e vermeden once X ile carp "
                        "(default 1.0 = kapali). [-1,1] araligina clip edilir.")
    p.add_argument("--agc", action="store_true",
                   help="otomatik seviye: konusma varken sinyali hedef peak'e "
                        f"normalize et (default hedef {AGC_TARGET}). --gain ile carpilir.")
    p.add_argument("--agc-target", type=float, default=AGC_TARGET, metavar="P",
                   help=f"AGC hedef peak (default {AGC_TARGET}; referans 1.wav peak=0.42)")
    p.add_argument("--agc-gate", type=float, default=AGC_GATE_RMS, metavar="R",
                   help=f"AGC konusma kapisi, RMS (default {AGC_GATE_RMS}); "
                        "altinda kazanc SABIT tutulur (gurultu sisirilmez)")
    p.add_argument("--check-wav", action="store_true",
                   help="mikrofon acmadan referans wav ile pipeline'i dogrula")
    p.add_argument("--record", nargs="?", type=float, const=6.0, default=None,
                   metavar="N",
                   help="N saniye kaydet + tam teshis (default 6). "
                        "Iki pass: 16k dogrudan ve native+kendi resample.")
    p.add_argument("--native-resample", action="store_true",
                   help="--record ile: SADECE native-rate pass'ini kos")
    p.add_argument("--only-16k", action="store_true",
                   help="--record ile: SADECE 16k dogrudan pass'ini kos")
    p.add_argument("--analyze", type=str, default=None, metavar="WAV",
                   help="var olan bir wav'i tam teshisten gecir (mikrofon acmaz)")
    args = p.parse_args()

    if args.list_devices:
        import sounddevice as sd
        print(sd.query_devices())
        return 0

    model_dir, origin = find_model()
    print(f"[model] {origin}: {model_dir}")

    keywords_file = HERE / "keywords.txt"
    pairs = encode_keywords(model_dir, keywords_file)
    print(f"[kw]    keywords.txt: {keywords_file}")
    for raw, enc in pairs:
        tag = "  (KONTROL)" if raw == "forever" else ""
        print(f"[kw]      {raw!r:16} -> {enc}{tag}")
    print(f"[cfg]   threshold={args.threshold}  score={args.score}")
    stage = GainStage(fixed=args.gain, agc=args.agc,
                      target=args.agc_target, gate=args.agc_gate)
    if args.agc:
        print(f"[cfg]   AGC ACIK: hedef peak={args.agc_target} gate RMS={args.agc_gate} "
              f"max {AGC_MAX_GAIN:.0f}x  (sabit gain {args.gain:.1f}x ile carpilir)")
    elif args.gain != 1.0:
        print(f"[cfg]   sabit gain: {args.gain:.1f}x")
    print()

    # REGRESYON: referans wav hala tetikliyor mu? (offline yol saglam mi?)
    ref_ok = check_wav(model_dir, keywords_file, args.threshold, args.score)
    if args.check_wav:
        return 0 if ref_ok else 1

    # --analyze: var olan wav'i teshis et
    if args.analyze:
        wav = Path(args.analyze)
        if not wav.is_file():
            sys.exit(f"[analyze] dosya yok: {wav}")
        audio, sr = read_wav(wav)
        describe(audio, sr, str(wav))
        diagnose(model_dir, keywords_file, audio, sr,
                 args.threshold, args.score, wav.name)
        return 0

    # --record: A/B testi
    if args.record is not None:
        native = device_native_sr(args.device)
        spotter = build_spotter(model_dir, keywords_file, args.threshold, args.score)

        passes = []
        if not args.native_resample:
            passes.append(("PASS A — cihaz 16 kHz'te acildi (CoreAudio resample)",
                           SAMPLE_RATE, HERE / "mic_capture.wav"))
        if not args.only_16k and native != SAMPLE_RATE:
            passes.append((f"PASS B — cihaz NATIVE {native} Hz + KENDI resample'imiz",
                           native, HERE / "mic_capture_native.wav"))

        results = []
        for label, open_sr, out in passes:
            audio, _ = record(spotter, args.device, args.record, open_sr, out, label,
                              stage=stage)
            ok = diagnose(model_dir, keywords_file, audio, SAMPLE_RATE,
                          args.threshold, args.score, f"{out.name} ({label})")
            results.append((label, out, ok))

        print()
        print("=" * 72)
        print("OZET")
        print(f"  referans wav ({CONTROL}) : "
              f"{'TETIKLIYOR (offline yol saglam)' if ref_ok else 'TETIKLEMIYOR (!! regresyon)'}")
        for label, out, ok in results:
            print(f"  {out.name:24} : {'TETIKLIYOR' if ok else 'TETIKLEMIYOR'}   [{label}]")
        print()
        print("  Kaydi kendin dinle (hiz/ton dogru mu?):")
        for _, out, _ in results:
            print(f"    afplay {out}")
        print("=" * 72)
        return 0

    # varsayilan: eski canli mod
    spotter = build_spotter(model_dir, keywords_file, args.threshold, args.score)
    try:
        live(spotter, args.device, stage=stage)
    except KeyboardInterrupt:
        print("\nbitti.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
