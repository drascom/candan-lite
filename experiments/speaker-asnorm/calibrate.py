#!/usr/bin/env python3
"""FAR/FRR/EER kalibrasyonu — Speaker-ID eşiğini TAHMİNLE değil ÖLÇÜMLE çıkar.

Girdi (dengeli ev corpus'u — ikisi de AYNI metni normal tonla okudu):
    bench/ayhan/ayhan_read.wav  (~55s)
    bench/havva/havva_read.wav  (~71s)
Açık-küme impostor havuzu (aile-DIŞI yabancılar):
    cohort/embeddings.npy  (Nx256, WeSpeaker, L2-norm, FLEURS tr_tr)

Yöntem:
  - Her okuma 3sn pencerelere bölünür (rms<0.015 sessiz atılır) → WeSpeaker gömme, L2-norm.
  - GENUINE (aynı kişi): leave-one-out (LOO) — her pencere için centroid = kişinin
    DİĞER pencerelerinin ortalaması; skor = score(centroid, o_pencere). (Küçük veri → LOO en sağlam.)
  - IMPOSTOR (kapalı-küme çapraz): her pencere ↔ DİĞER kişinin TAM centroid'i.
  - IMPOSTOR (açık-küme): her cohort yabancı gömmesi ↔ her kişinin TAM centroid'i.
  - Bunların hepsi HEM raw_cos HEM asnorm için.
  - Eşik süpürmesi → FAR (impostor kabul) / FRR (genuine ret) / EER; 3 işletme noktası.
  - MARJ: kapalı-küme argmax kararında best−second (raw_cos).

enc.py + score.py YENİDEN kullanılır (import; kopya yok). Salt-okunur: worker/, .env, speakers.db'ye DOKUNULMAZ.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from enc import HERE, build_wespeaker, embed_wav_windows
from score import asnorm, centroid, raw_cos

BENCH_ROOT = HERE / "bench"
COHORT_EMB = HERE / "cohort" / "embeddings.npy"


# ---------------------------------------------------------------- veri toplama
def person_windows(enc, pdir: Path) -> list[np.ndarray]:
    embs: list[np.ndarray] = []
    for wav in sorted(pdir.glob("*.wav")):
        embs.extend(embed_wav_windows(enc, wav, per_window=True))
    return embs


def loo_centroid(embs: list[np.ndarray], skip: int) -> np.ndarray:
    """Leave-one-out centroid: skip indeksli pencere HARİÇ ortalama."""
    rest = [e for j, e in enumerate(embs) if j != skip]
    return centroid(np.stack(rest))


# ------------------------------------------------------------------- FAR / FRR
def sweep(genuine: np.ndarray, impostor: np.ndarray, n: int = 400):
    """Eşiği min→max tara. Dönüş: thr, far, frr dizileri."""
    lo = min(genuine.min(), impostor.min())
    hi = max(genuine.max(), impostor.max())
    pad = 0.02 * (hi - lo + 1e-9)
    thr = np.linspace(lo - pad, hi + pad, n)
    far = np.array([float(np.mean(impostor >= t)) for t in thr])  # impostor KABUL
    frr = np.array([float(np.mean(genuine < t)) for t in thr])  # genuine RET
    return thr, far, frr


def eer_point(thr, far, frr):
    """FAR≈FRR kesişimi. Dönüş: (eer, thr_eer)."""
    i = int(np.argmin(np.abs(far - frr)))
    return 0.5 * (far[i] + frr[i]), float(thr[i]), i


def op_low_far(thr, far, frr, target=0.01):
    """FAR<=target koşulunu sağlayan EN DÜŞÜK eşik (en düşük FRR). Dönüş: (thr,far,frr)."""
    ok = np.where(far <= target)[0]
    # hiç sağlanmıyorsa en düşük FAR; aksi halde thr artan → ilk (en düşük eşik) = en düşük FRR
    i = int(np.argmin(far)) if ok.size == 0 else int(ok.min())
    return float(thr[i]), float(far[i]), float(frr[i])


def op_low_frr(thr, far, frr, target=0.05):
    """FRR<=target koşulunu sağlayan EN YÜKSEK eşik (en düşük FAR). Dönüş: (thr,far,frr)."""
    ok = np.where(frr <= target)[0]
    # thr artan → son (en yüksek eşik) = en düşük FAR
    i = int(np.argmin(frr)) if ok.size == 0 else int(ok.max())
    return float(thr[i]), float(far[i]), float(frr[i])


def scorevec(name, thr, far, frr):
    e, te, _ = eer_point(thr, far, frr)
    return dict(name=name, thr=thr, far=far, frr=frr, eer=e, thr_eer=te)


# --------------------------------------------------------------------- toplama
def collect_scores(people, cohort, k):
    """genuine/impostor skor dizilerini raw ve asnorm için topla."""
    names = list(people)
    full_cen = {n: centroid(np.stack(people[n])) for n in names}

    g_raw, g_an, i_raw, i_an = [], [], [], []

    # GENUINE (LOO) + kapalı-küme çapraz IMPOSTOR
    for name in names:
        embs = people[name]
        others = [o for o in names if o != name]
        for idx, w in enumerate(embs):
            cen_loo = loo_centroid(embs, idx)
            g_raw.append(raw_cos(cen_loo, w))
            g_an.append(asnorm(cen_loo, w, cohort, k))
            for o in others:  # çapraz: w ↔ diğer kişinin tam centroid'i
                i_raw.append(raw_cos(full_cen[o], w))
                i_an.append(asnorm(full_cen[o], w, cohort, k))

    # açık-küme IMPOSTOR: cohort yabancıları ↔ her kişinin tam centroid'i
    for name in names:
        cen = full_cen[name]
        for j in range(cohort.shape[0]):
            i_raw.append(raw_cos(cen, cohort[j]))
            i_an.append(asnorm(cen, cohort[j], cohort, k))

    return (
        np.array(g_raw), np.array(i_raw), np.array(g_an), np.array(i_an), full_cen
    )


def margin_analysis(people, cohort, full_cen):
    """Kapalı-küme argmax marjı (raw_cos): best−second.
    GENUINE: kendi (LOO) centroid'i best olmalı → marj = self − diğer.
    Yabancı (cohort): en yakın aile centroid'i best; ikinci aile ile marj → düşük olmalı.
    """
    names = list(people)
    gen_margin, gen_correct = [], 0
    for name in names:
        embs = people[name]
        for idx, w in enumerate(embs):
            cens = {name: loo_centroid(embs, idx)}
            for o in names:
                if o != name:
                    cens[o] = full_cen[o]
            scored = sorted(((raw_cos(c, w), nm) for nm, c in cens.items()), reverse=True)
            best_s, best_nm = scored[0]
            second = scored[1][0] if len(scored) > 1 else -1.0
            gen_correct += int(best_nm == name)
            gen_margin.append(best_s - second)
    # yabancı marjı: cohort'un en yakın 2 aile centroid'i arası fark
    imp_margin = []
    fam = [full_cen[n] for n in names]
    for j in range(cohort.shape[0]):
        s = sorted((float(np.dot(c, cohort[j])) for c in fam), reverse=True)
        imp_margin.append(s[0] - (s[1] if len(s) > 1 else -1.0))
    return np.array(gen_margin), gen_correct, len(gen_margin), np.array(imp_margin)


# ------------------------------------------------------------------------ main
def pct(x):
    return f"{100 * x:5.1f}%"


def print_sweep_table(res, rows=11):
    thr, far, frr = res["thr"], res["far"], res["frr"]
    idx = np.linspace(0, len(thr) - 1, rows).astype(int)
    print(f"\n  [{res['name']}] eşik süpürmesi (örneklenmiş)")
    print("     eşik      FAR(impostor kabul)  FRR(genuine ret)")
    for i in idx:
        print(f"    {thr[i]:+7.3f}     {pct(far[i])}              {pct(frr[i])}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--k", type=int, default=40, help="AS-norm top-K cohort")
    p.add_argument("--far", type=float, default=0.01, help="düşük-FAR işletme hedefi")
    p.add_argument("--frr", type=float, default=0.05, help="düşük-FRR işletme hedefi")
    p.add_argument("--dir", default=None, help="corpus kökü (varsayılan: bench/). Örn: natural")
    a = p.parse_args(argv)

    root = (HERE / a.dir) if a.dir else BENCH_ROOT
    pdirs = sorted([d for d in root.iterdir() if d.is_dir()]) if root.is_dir() else []
    if not pdirs:
        print(f"[hata] corpus dizini yok: {root}/<kişi>/*.wav")
        return 1
    if not COHORT_EMB.is_file():
        print(f"[hata] cohort yok: {COHORT_EMB}")
        return 1
    cohort = np.load(COHORT_EMB).astype(np.float32)
    cohort = cohort / np.linalg.norm(cohort, axis=1, keepdims=True).clip(1e-9)

    enc = build_wespeaker()
    people = {}
    for d in pdirs:
        embs = person_windows(enc, d)
        if len(embs) < 3:
            print(f"[atla] {d.name}: <3 pencere ({len(embs)})")
            continue
        people[d.name] = embs
        print(f"[{d.name}] {len(embs)} pencere (3sn)")

    if len(people) < 2:
        print("[hata] en az 2 kişi gerekli")
        return 1

    print(f"\ncohort N={cohort.shape[0]}  AS-norm K={a.k}")
    print("[dogrulama] cohort = FLEURS tr_tr yabancilari; ayhan/havva cohort'ta YOK (acik-kume impostor temiz)")

    g_raw, i_raw, g_an, i_an, full_cen = collect_scores(people, cohort, a.k)
    print(f"\nskor sayilari:  genuine={len(g_raw)}  impostor={len(i_raw)}"
          f"  (capraz={sum(len(people[n]) for n in people)}  cohort={cohort.shape[0]*len(people)})")

    tr, far_r, frr_r = sweep(g_raw, i_raw)
    ta, far_a, frr_a = sweep(g_an, i_an)
    res_raw = scorevec("raw_cos", tr, far_r, frr_r)
    res_an = scorevec("asnorm", ta, far_a, frr_a)

    # ---- sanity check ----
    print("\n### SANITY CHECK (eşik uçları — EER hesabı tutarlı mı?)")
    for r in (res_raw, res_an):
        print(f"  [{r['name']}] eşik={r['thr'][0]:+.3f} (çok düşük) → FAR={pct(r['far'][0])} FRR={pct(r['frr'][0])}"
              f"   |  eşik={r['thr'][-1]:+.3f} (çok yüksek) → FAR={pct(r['far'][-1])} FRR={pct(r['frr'][-1])}")
    print("  beklenen: düşük eşikte FAR→100%/FRR→0%, yüksek eşikte FAR→0%/FRR→100%")

    # ---- sweep tabloları ----
    print_sweep_table(res_raw)
    print_sweep_table(res_an)

    # ---- EER karşılaştırma ----
    print("\n### EER KARŞILAŞTIRMA (düşük EER = daha iyi ayrım)")
    print(f"  raw_cos : EER={pct(res_raw['eer'])}  @ eşik={res_raw['thr_eer']:+.3f}")
    print(f"  asnorm  : EER={pct(res_an['eer'])}  @ eşik={res_an['thr_eer']:+.3f}")
    better = "asnorm" if res_an["eer"] <= res_raw["eer"] else "raw_cos"
    print(f"  → daha iyi (düşük EER): {better}")

    # ---- işletme noktaları (asnorm — canlı sistemin kullandığı) ----
    print(f"\n### İŞLETME NOKTALARI [asnorm]  (hedef FAR={pct(a.far)} / FRR={pct(a.frr)})")
    e, te, _ = eer_point(ta, far_a, frr_a)
    lf_t, lf_far, lf_frr = op_low_far(ta, far_a, frr_a, a.far)
    lr_t, lr_far, lr_frr = op_low_frr(ta, far_a, frr_a, a.frr)
    print(f"  (a) EER      : eşik={te:+.3f}   FAR={pct(e)} FRR={pct(e)}")
    print(f"  (b) düşük-FAR: eşik={lf_t:+.3f}   FAR={pct(lf_far)} FRR={pct(lf_frr)}   (yabancıyı içeri alma — ev güvenliği)")
    print(f"  (c) düşük-FRR: eşik={lr_t:+.3f}   FAR={pct(lr_far)} FRR={pct(lr_frr)}   (kullanıcıyı reddetme az)")

    # ---- marj ----
    gm, gc, gn, im = margin_analysis(people, cohort, full_cen)
    print("\n### MARJ (kapalı-küme argmax, raw_cos: best−second)")
    print(f"  argmax doğruluk (genuine): {gc}/{gn}")
    print(f"  genuine marj:  med={np.median(gm):+.3f}  min={gm.min():+.3f}  p5={np.percentile(gm,5):+.3f}")
    print(f"  yabancı marj (cohort, 2 aile centroid arası): med={np.median(im):+.3f}  max={im.max():+.3f}")
    margin_reco = max(0.0, round(float(np.percentile(gm, 5)), 3))
    print(f"  → önerilen MARGIN ≈ {margin_reco:+.3f} (genuine p5; altında kalan argmax kararı REDDEDİLİR)")

    # ---- ÖNERİ ----
    # Ev güvenliği: yabancıyı aile sanmak (FAR) kullanıcıyı reddetmekten (FRR) daha kötü.
    # Düşük-FAR eşiği burada FRR'yi neredeyse hiç artırmıyor (temiz corpus) → birincil öneri o.
    print("\n" + "=" * 68)
    print(f"ÖNERİLEN: SPEAKER_ASNORM_THRESHOLD={lf_t:+.3f}, MARGIN={margin_reco:+.3f}"
          f"  (FAR={pct(lf_far)}, FRR={pct(lf_frr)}; ev-güvenliği önceliği)")
    print(f"  dengeli alternatif (EER): SPEAKER_ASNORM_THRESHOLD={te:+.3f}  (FAR=FRR={pct(e)})")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
