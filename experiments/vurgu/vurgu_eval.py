"""Vurgu geldi mi — hedef kelimeyi cümlenin GERİ KALANIYLA kıyaslayarak ölç.

ASIL SORU şu: bir yol hedef kelimeyi ÖNE ÇIKARIYOR mu, yoksa cümlenin tamamını
mı yükseltiyor? (`expressive_high` tam olarak bu yüzden şüpheli.) Bu yüzden
mutlak değil **KONTRAST** ölçülür — her ölçü hedef kelimenin cümlenin geri
kalanına ORANI:

    süre     = (hedef süresi / hedef hecesi) ÷ (diğer kelimelerin hece başına
               ortalama süresi)                             → oran, 1.0 = fark yok
    enerji   = 20log10 RMS(hedef) − 20log10 RMS(geri kalan) → dB
    perde    = 12log2( hedefin F0 tepesi / geri kalanın F0 medyanı ) → yarım ton

Sonra her yol için TABANA göre fark: Δ = yol − taban (aynı cümlede). Cümle
genelini yükselten bir yol kontrastı DEĞİŞTİRMEZ; yalnız hedefi öne çıkaran yol
kontrastı büyütür. Ayrım tam burada.

Kelime sınırları Whisper'ın kelime zaman damgalarından
(`mlx-community/whisper-large-v3-turbo`, `word_timestamps=True`). F0 kendi
otokorelasyon izleyicimizle (numpy) — ek bağımlılık yok.

ANLAŞILIRLIK ŞARTI da ölçülür (`token_eval.py`/`speed_eval.py` ile AYNI eşik,
WER ≤ 0.25): büyük harf ve noktalama denemelerinde model harfleri tek tek
okuyabilir ya da tuhaf duraklar yapabilir. Kontrast kazancı anlaşılırlığı
bozarak alınıyorsa kazanç değildir. `bas_yendi` ve `hedef_bulunamadi` ayrıca
sayılır.

    ../tts-local-bench/venvs/whisper/bin/python vurgu_eval.py
    ... vurgu_eval.py --yollar kombo        # yalnız yeni koşul

Çıktı: out/vurgu_eval.json + ekrana tablo.
"""
from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent / "tts-local-bench"
sys.path.insert(0, str(BENCH / "runners"))
sys.path.insert(0, str(BENCH))

import mlx_whisper  # noqa: E402

from asr_eval import MODEL, norm_words, wer  # noqa: E402

import vurgu_set  # noqa: E402

WER_OK = 0.25          # token_eval.py / speed_eval.py ile AYNI eşik
UNLU = set("aeıioöuüâî")


def heceler(kelime: str) -> int:
    """Türkçede hece sayısı = ünlü sayısı. Kelime başına süreyi normalize eder."""
    return max(1, sum(1 for ch in kelime.lower() if ch in UNLU))


def oku(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    return x.astype(np.float32) / 32768.0, sr


def f0_izi(x: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Otokorelasyon perde izleyici → (zamanlar, f0, sesli mi).

    Kare 1024 (~43 ms), adım 256 (~11 ms). Normalize otokorelasyon tepesi
    eşiği 0.35; ayrıca kare RMS'i cümlenin tepesinin %5'inin altındaysa sessiz
    sayılır (nefes/sessizlik perdeyi kirletmesin).
    """
    n, hop = 1024, 256
    if len(x) < n:
        return np.array([]), np.array([]), np.array([], dtype=bool)
    idx = np.arange(0, len(x) - n + 1, hop)
    fr = np.stack([x[i:i + n] for i in idx])
    fr = fr - fr.mean(axis=1, keepdims=True)
    rms = np.sqrt((fr ** 2).mean(axis=1))
    S = np.fft.rfft(fr * np.hanning(n), 2 * n)
    ac = np.fft.irfft(np.abs(S) ** 2, 2 * n)[:, :n]
    ac0 = ac[:, :1].copy()
    ac0[ac0 <= 0] = 1e-12
    acn = ac / ac0
    lmin, lmax = int(sr / 400), min(int(sr / 60), n - 1)
    seg = acn[:, lmin:lmax + 1]
    lag = seg.argmax(axis=1) + lmin
    tepe = seg.max(axis=1)
    f0 = sr / lag
    sesli = (tepe > 0.35) & (rms > 0.05 * (rms.max() or 1.0))
    return (idx + n / 2) / sr, f0, sesli


def _pencere_rms(x: np.ndarray, sr: int, ms: int = 10) -> tuple[np.ndarray, int]:
    w = int(sr * ms / 1000)
    nb = len(x) // w
    if nb < 1:
        return np.array([]), w
    return np.sqrt((x[:nb * w].reshape(nb, w) ** 2).mean(axis=1)), w


def _kirp(r: np.ndarray, w: int, sr: int, a: float, b: float,
          esik: float) -> tuple[float, float]:
    """Kelime aralığının başındaki/sonundaki SESSİZLİĞİ at.

    KRİTİK: `<|prosody:pause|>` sessizliği hedef kelimeden ÖNCE ekliyor ve
    Whisper o sessizliği çoğu zaman kelimenin span'ine yapıştırıyor. Kırpmadan
    ölçersek "kelime uzadı" (yanlış) ve "enerjisi düştü" (sessizlik RMS'i
    seyreltiyor, yine yanlış) deriz. Kırpınca kelimenin KENDİSİ ölçülür.
    """
    i0, i1 = int(a * sr / w), min(int(b * sr / w), len(r))
    if i1 - i0 < 2:
        return a, b
    ses = r[i0:i1] > esik
    if not ses.any():
        return a, b
    k0 = int(ses.argmax())
    k1 = int(len(ses) - ses[::-1].argmax())
    return (i0 + k0) * w / sr, (i0 + k1) * w / sr


def _span_rms(x: np.ndarray, sr: int, araliklar: list[tuple[float, float]]) -> float:
    par = [x[int(a * sr):int(b * sr)] for a, b in araliklar]
    par = [p for p in par if p.size]
    if not par:
        return 0.0
    v = np.concatenate(par)
    return float(np.sqrt((v ** 2).mean()))


def _dizi_bul(hyp: list[str], hedef: list[str]) -> tuple[int, int] | None:
    """Transkript kelimelerinde hedef dizisini bul (ilk eşleşme)."""
    k = len(hedef)
    for i in range(len(hyp) - k + 1):
        if hyp[i:i + k] == hedef:
            return i, i + k
    return None


def olc(path: Path, beklenen: str, hedef: str) -> dict:
    """Bir wav → kontrast ölçüleri + anlaşılırlık."""
    r = mlx_whisper.transcribe(str(path), path_or_hf_repo=MODEL, language="tr",
                               word_timestamps=True)
    kelimeler = [w for s in r.get("segments", []) for w in s.get("words", [])]
    hyp_ham = r["text"].strip()
    bek_w, hyp_w = norm_words(beklenen), norm_words(hyp_ham)
    sonuc: dict = {
        "wav": str(path.relative_to(HERE)), "transkript": hyp_ham,
        "wer": round(wer(bek_w, hyp_w)[0], 4),
        "bas_yendi": bool(bek_w) and bek_w[0] not in hyp_w,
    }

    # Kelime zaman damgalarını aynı normalizasyondan geçir (tek kelime → tek
    # jeton varsayımı; boşa çıkanlar atılır ki hizalama kaymasın).
    duz = []
    for w in kelimeler:
        nw = norm_words(w["word"])
        if len(nw) == 1:
            duz.append((nw[0], float(w["start"]), float(w["end"])))
    yer = _dizi_bul([d[0] for d in duz], norm_words(hedef))
    if yer is None:
        sonuc["hedef_bulundu"] = False
        return sonuc
    sonuc["hedef_bulundu"] = True
    i, j = yer
    hed = duz[i:j]
    geri = duz[:i] + duz[j:]
    if not geri:
        sonuc["hedef_bulundu"] = False
        return sonuc

    x, sr = oku(path)
    # Her kelime aralığı sessizlikten kırpılır (bkz. `_kirp`) — hem hedef hem
    # geri kalan, aynı ölçüyle.
    r, w = _pencere_rms(x, sr)
    esik = max(0.01, 0.08 * float(np.percentile(r, 90))) if r.size else 0.01
    hed_ar = [_kirp(r, w, sr, a, b, esik) for _, a, b in hed]
    geri_ar = [_kirp(r, w, sr, a, b, esik) for _, a, b in geri]
    h0, h1 = hed_ar[0][0], hed_ar[-1][1]
    # Hedeften önce GERÇEKTEN ne kadar sessizlik var (duraklama geldi mi).
    sonuc["on_sessizlik_s"] = round(h0 - hed[0][1], 3)

    # ── KELİME SONRASI BEKLEME (3. turun ASIL ölçütü) ───────────────────────
    # Kullanıcı komboda "vurgu geldi ama kelime sonrası uzun bekleme" dedi ve
    # 2. turda bu büyüklük HİÇ ölçülmemişti. Whisper'ın kelime kutusu sessizliği
    # keyfî bir yana yapıştırdığı için ham damgalar kullanılamaz; iki komşu
    # kelimenin de KIRPILMIŞ sınırları alınır, aradaki fark gerçek sessizliktir.
    #   geri_ar = duz[:i] + duz[j:] sırasını korur →
    #   önceki kelime  = geri_ar[i-1]   (i > 0 ise)
    #   sonraki kelime = geri_ar[i]     (hedeften sonra kelime varsa)
    sonuc["on_bosluk_s"] = round(h0 - geri_ar[i - 1][1], 3) if i > 0 else None
    sonuc["arka_bosluk_s"] = (round(geri_ar[i][0] - h1, 3)
                              if i < len(geri_ar) else None)

    # ── süre: hece başına, cümlenin geri kalanına oranla ──
    hed_hs = sum(b - a for a, b in hed_ar) / sum(heceler(w_) for w_, _, _ in hed)
    geri_hs = (sum(b - a for a, b in geri_ar)
               / sum(heceler(w_) for w_, _, _ in geri))
    sonuc["sure_orani"] = round(hed_hs / geri_hs, 4) if geri_hs else None
    sonuc["hedef_sure_s"] = round(h1 - h0, 3)

    # ── enerji: hedef RMS − geri kalan RMS (dB) ──
    rh, rg = _span_rms(x, sr, hed_ar), _span_rms(x, sr, geri_ar)
    sonuc["enerji_db"] = (round(20 * np.log10(rh / rg), 3)
                          if rh > 0 and rg > 0 else None)

    # ── perde: hedefin F0 tepesi (%90'lık dilim) − geri kalanın medyanı ──
    t, f0, sesli = f0_izi(x, sr)
    if t.size:
        def _sec(ar: list[tuple[float, float]]) -> np.ndarray:
            m = np.zeros_like(t, dtype=bool)
            for a, b in ar:
                m |= (t >= a) & (t <= b)
            return f0[m & sesli]
        fh, fg = _sec(hed_ar), _sec(geri_ar)
        if fh.size >= 3 and fg.size >= 3:
            sonuc["perde_st"] = round(
                float(12 * np.log2(np.percentile(fh, 90) / np.median(fg))), 3)
            sonuc["hedef_f0_tepe"] = round(float(np.percentile(fh, 90)), 1)
    return sonuc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yollar", nargs="*", default=None)
    ap.add_argument("--cumleler", nargs="*", default=None)
    args = ap.parse_args()

    probe = json.loads((HERE / "out" / "vurgu_probe.json").read_text(encoding="utf-8"))
    out_path = HERE / "out" / "vurgu_eval.json"
    rapor: dict = {"wer_esigi": WER_OK, "kosullar": {}}
    if out_path.exists():
        try:
            rapor["kosullar"] = json.loads(
                out_path.read_text(encoding="utf-8"))["kosullar"]
        except Exception:  # noqa: BLE001
            pass

    adlar = [a for a, k in probe["kosullar"].items()
             if (not args.yollar or k["yol"] in args.yollar)
             and (not args.cumleler or k["cumle_id"] in args.cumleler)]

    for idx, ad in enumerate(adlar, 1):
        k = probe["kosullar"][ad]
        satirlar = [olc(HERE / it["wav"], k["beklenen"], k["hedef"])
                    for it in k["items"]]
        bulunan = [s for s in satirlar if s.get("hedef_bulundu")]

        def ort(alan: str) -> float | None:
            v = [s[alan] for s in bulunan if s.get(alan) is not None]
            return round(float(np.mean(v)), 3) if v else None

        n = len(satirlar)
        rapor["kosullar"][ad] = {
            "ad": ad, "cumle_id": k["cumle_id"], "yol": k["yol"],
            "metin": k["metin"], "hedef": k["hedef"], "n": n,
            "beklenen": k["beklenen"], "sikayet": k["sikayet"],
            "yol_aciklama": k["yol_aciklama"],
            "hedef_bulundu": len(bulunan),
            "sure_orani": ort("sure_orani"), "enerji_db": ort("enerji_db"),
            "perde_st": ort("perde_st"), "on_sessizlik_s": ort("on_sessizlik_s"),
            "on_bosluk_s": ort("on_bosluk_s"), "arka_bosluk_s": ort("arka_bosluk_s"),
            "ort_sure_s": round(float(np.mean([it["sure_s"] for it in k["items"]])), 3),
            "ort_wer": round(float(np.mean([s["wer"] for s in satirlar])), 4),
            "anlasilan": sum(1 for s in satirlar if s["wer"] <= WER_OK),
            "bas_yendi": sum(1 for s in satirlar if s["bas_yendi"]),
            "items": satirlar,
        }
        r = rapor["kosullar"][ad]
        print(f"[{idx}/{len(adlar)}] {ad:26s} süre×{r['sure_orani']} "
              f"perde {r['perde_st']}st  ön {r['on_bosluk_s']}s "
              f"ARKA {r['arka_bosluk_s']}s  "
              f"WER {r['ort_wer']}  anlaşılan {r['anlasilan']}/{n}  "
              f"hedef {r['hedef_bulundu']}/{n}", flush=True)
        out_path.write_text(json.dumps(rapor, ensure_ascii=False, indent=2),
                            encoding="utf-8")

    tablo(rapor)
    print(f"\nyazıldı: {out_path}", file=sys.stderr)


def tablo(rapor: dict) -> None:
    """Yol × cümle tablosu — her hücre TABANA göre fark."""
    ks = rapor["kosullar"]
    yollar = [y["ad"] for y in vurgu_set.YOLLAR
              if any(k["yol"] == y["ad"] for k in ks.values())]
    cumleler = [c["id"] for c in vurgu_set.CUMLELER]
    # Kelime SONRASI bekleme burada MUTLAK saniyeyle de basılır: 3. turun asıl
    # şikâyeti "sonrasında uzun bekleme" ve orada önemli olan farktan çok
    # sürenin kendisi (0.1 s doğal, 0.5 s duyulur bir boşluk).
    print("\n── kelime SONRASI bekleme, saniye (küçük = iyi) " + "─" * 12)
    print(f"{'yol':18s}" + "".join(f"{c['id']:>12s}" for c in vurgu_set.CUMLELER)
          + f"{'ORT':>12s}")
    for y in yollar:
        hu, sat = [], []
        for c in cumleler:
            v = ks.get(f"{c}/{y}", {}).get("arka_bosluk_s")
            hu.append(f"{'—':>12s}" if v is None else f"{v:>12.3f}")
            if v is not None:
                sat.append(v)
        hu.append(f"{float(np.mean(sat)):>12.3f}" if sat else f"{'—':>12s}")
        print(f"{y:18s}" + "".join(hu))

    for alan, birim, bicim in (("sure_orani", "süre oranı", "{:+.2f}"),
                               ("enerji_db", "enerji dB", "{:+.2f}"),
                               ("perde_st", "perde yarım ton", "{:+.2f}"),
                               ("arka_bosluk_s", "kelime SONRASI bekleme s "
                                "(BURADA + = KÖTÜ)", "{:+.3f}")):
        print(f"\n── Δ {birim} (tabana göre; + = hedef kelime öne çıktı) " + "─" * 12)
        print(f"{'yol':18s}" + "".join(f"{c:>12s}" for c in cumleler) + f"{'ORT':>12s}")
        for y in yollar:
            hucre, sat = [], []
            for c in cumleler:
                t = ks.get(f"{c}/taban", {}).get(alan)
                v = ks.get(f"{c}/{y}", {}).get(alan)
                if t is None or v is None:
                    hucre.append(f"{'—':>12s}")
                    continue
                sat.append(v - t)
                hucre.append(f"{bicim.format(v - t):>12s}")
            ortalama = f"{bicim.format(float(np.mean(sat))):>12s}" if sat else f"{'—':>12s}"
            print(f"{y:18s}" + "".join(hucre) + ortalama)

    print("\n── anlaşılırlık " + "─" * 46)
    print(f"{'yol':18s}{'WER':>8s}{'anlaşılan':>12s}{'baş-yendi':>11s}"
          f"{'hedef bulundu':>15s}{'süre s':>9s}{'ön sessizlik':>14s}")
    for y in yollar:
        rs = [k for k in ks.values() if k["yol"] == y]
        if not rs:
            continue
        n = sum(r["n"] for r in rs)
        print(f"{y:18s}{np.mean([r['ort_wer'] for r in rs]):8.3f}"
              f"{sum(r['anlasilan'] for r in rs):8d}/{n:<3d}"
              f"{sum(r['bas_yendi'] for r in rs):11d}"
              f"{sum(r['hedef_bulundu'] for r in rs):11d}/{n:<3d}"
              f"{np.mean([r['ort_sure_s'] for r in rs]):9.2f}"
              f"{np.mean([r['on_sessizlik_s'] or 0 for r in rs]):13.3f}s")


if __name__ == "__main__":
    main()
