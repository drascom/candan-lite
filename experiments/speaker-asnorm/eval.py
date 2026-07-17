#!/usr/bin/env python3
"""Ölçüm harness'ı — üç yöntemin (ham / AS-norm / argmax) ayrım gücü.

Girdi: audio/<kişi>/*.wav  (ör. audio/ayhan/*.wav, audio/havi/*.wav).
Her WAV ~3sn pencerelere bölünür (rms<0.015 sessiz pencereler atılır), WeSpeaker gömmesi.
Kişi başına pencereler yarı-yarıya bölünür: ilk yarı = enroll centroid, ikinci yarı = test.

ÇIKTI:
  - aynı-kişi skor dağılımı vs çapraz-kişi skor dağılımı + MARJ (ham & AS-norm).
  - argmax+marj kapalı-küme doğruluğu (≥2 kişi varsa).
  - Havi yoksa: Ayhan-self vs COHORT(yabancı) marjı — AS-norm'un yabancıyı ittiğini gösterir.

Kullanım (Havi audio'su gelince aynı komut, otomatik ikinci kişiyi bulur):
    python eval.py
    python eval.py --k 60
"""

from __future__ import annotations

import argparse
from pathlib import Path
from statistics import median, mean

import numpy as np

from enc import HERE, build_wespeaker, embed_wav_windows
from score import argmax_margin, asnorm, centroid, raw_cos

AUDIO_ROOT = HERE / "audio"
COHORT_EMB = HERE / "cohort" / "embeddings.npy"


def _stats(vals: list[float]) -> str:
    if not vals:
        return "yok"
    return f"n={len(vals):3d} ort={mean(vals):+.3f} med={median(vals):+.3f} min={min(vals):+.3f} max={max(vals):+.3f}"


def _dprime(pos: list[float], neg: list[float]) -> float:
    """Ölçek-bağımsız ayrım: (ort_pos−ort_neg)/sqrt(0.5(var_pos+var_neg)). Büyük = iyi ayrım."""
    p, n = np.asarray(pos), np.asarray(neg)
    denom = float(np.sqrt(0.5 * (p.var() + n.var())))
    return (float(p.mean()) - float(n.mean())) / denom if denom > 1e-9 else 0.0


def _overlap(pos: list[float], neg: list[float]) -> float:
    """neg (yabancı/çapraz) skorlarının, pos medyanını AŞAN oranı → düşük = iyi ayrım."""
    thr = median(pos)
    return sum(1 for x in neg if x >= thr) / max(1, len(neg))


def person_windows(enc, pdir: Path) -> list[np.ndarray]:
    embs: list[np.ndarray] = []
    for wav in sorted(pdir.glob("*.wav")):
        embs.extend(embed_wav_windows(enc, wav, per_window=True))
    return embs


def split_half(embs: list[np.ndarray]) -> tuple[np.ndarray, list[np.ndarray]]:
    """İlk yarı → enroll centroid; ikinci yarı → test pencereleri."""
    n = len(embs)
    h = max(1, n // 2)
    enroll = np.stack(embs[:h])
    test = embs[h:] if n > h else embs[:h]
    return centroid(enroll), test


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--k", type=int, default=40, help="AS-norm top-K cohort (~N/3, d-prime tepe)")
    a = p.parse_args(argv)

    pdirs = sorted([d for d in AUDIO_ROOT.iterdir() if d.is_dir()]) if AUDIO_ROOT.is_dir() else []
    if not pdirs:
        print(f"[hata] kişi dizini yok: {AUDIO_ROOT}/<kişi>/*.wav")
        return 1
    cohort = np.load(COHORT_EMB) if COHORT_EMB.is_file() else None
    if cohort is None:
        print(f"[uyarı] cohort yok ({COHORT_EMB}) — AS-norm atlanır. fetch_cohort.py çalıştır.")

    enc = build_wespeaker()
    people: dict[str, tuple[np.ndarray, list[np.ndarray]]] = {}
    for d in pdirs:
        embs = person_windows(enc, d)
        if len(embs) < 2:
            print(f"[atla] {d.name}: <2 pencere ({len(embs)})")
            continue
        cen, test = split_half(embs)
        people[d.name] = (cen, test)
        print(f"[{d.name}] {len(embs)} pencere → enroll {len(embs)//2}, test {len(test)}")

    if not people:
        print("[hata] yeterli veri yok")
        return 1

    names = list(people)
    print(f"\nAS-norm K={a.k}, cohort N={0 if cohort is None else cohort.shape[0]}\n")

    # ---- (a)/(b): aynı-kişi vs çapraz-kişi, ham + AS-norm ----
    for cen_name in names:
        cen, _ = people[cen_name]
        for tst_name in names:
            _, tst = people[tst_name]
            raw = [raw_cos(cen, t) for t in tst]
            tag = "AYNI " if cen_name == tst_name else "CAPRAZ"
            line = f"  {tag} {tst_name}→{cen_name}  ham:  {_stats(raw)}"
            if cohort is not None:
                an = [asnorm(cen, t, cohort, a.k) for t in tst]
                line += f"\n         {' '*len(tst_name)+' '*len(cen_name)}  asnorm:{_stats(an)}"
            print(line)

    # ---- MARJ özeti: aynı-kişi med − çapraz-kişi med (pozitif = ayırıyor) ----
    if len(names) >= 2:
        print("\n### MARJ (aynı-kişi med − çapraz-kişi med; POZİTİF = doğru ayrım)")
        for cen_name in names:
            cen, _ = people[cen_name]
            same_raw = [raw_cos(cen, t) for t in people[cen_name][1]]
            for other in names:
                if other == cen_name:
                    continue
                cross_raw = [raw_cos(cen, t) for t in people[other][1]]
                m_raw = median(same_raw) - median(cross_raw)
                msg = f"  {cen_name} centroid:  ham marj({cen_name}−{other}) = {m_raw:+.3f}"
                if cohort is not None:
                    same_an = [asnorm(cen, t, cohort, a.k) for t in people[cen_name][1]]
                    cross_an = [asnorm(cen, t, cohort, a.k) for t in people[other][1]]
                    m_an = median(same_an) - median(cross_an)
                    msg += f"   |  asnorm marj = {m_an:+.3f}"
                print(msg)

    # ---- argmax+marj kapalı-küme (cohort GEREKMEZ) ----
    if len(names) >= 2:
        cens = {n: people[n][0] for n in names}
        print("\n### argmax+marj (kapalı-küme; test penceresi EN YAKIN centroid'e atanır)")
        for true_name in names:
            _, tst = people[true_name]
            correct = 0
            margins = []
            for t in tst:
                pred, _bs, mg = argmax_margin(t, cens)
                correct += int(pred == true_name)
                margins.append(mg)
            print(f"  {true_name}: doğru {correct}/{len(tst)}  ort-marj {mean(margins):+.3f}")

    # ---- Havi yoksa ilk sinyal: her kişi-self vs COHORT(yabancı) ----
    if cohort is not None:
        print("\n### self vs COHORT(yabancı) — AS-norm yabancıyı itiyor mu?")
        for cen_name in names:
            cen, tst = people[cen_name]
            self_raw = [raw_cos(cen, t) for t in tst]
            coh_raw = [raw_cos(cen, cohort[i]) for i in range(cohort.shape[0])]
            self_an = [asnorm(cen, t, cohort, a.k) for t in tst]
            coh_an = [asnorm(cen, cohort[i], cohort, a.k) for i in range(cohort.shape[0])]
            print(f"  [{cen_name}] self  ham:  {_stats(self_raw)}")
            print(f"  {' '*len(cen_name)}   cohort ham: {_stats(coh_raw)}")
            print(f"  {' '*len(cen_name)}   → ham marj = {median(self_raw)-median(coh_raw):+.3f}  d'={_dprime(self_raw,coh_raw):.2f}  cohort>self-med: {_overlap(self_raw,coh_raw):.0%}")
            print(f"  [{cen_name}] self  asnorm:{_stats(self_an)}")
            print(f"  {' '*len(cen_name)}   cohort asnorm:{_stats(coh_an)}")
            print(f"  {' '*len(cen_name)}   → asnorm marj = {median(self_an)-median(coh_an):+.3f}  d'={_dprime(self_an,coh_an):.2f}  cohort>self-med: {_overlap(self_an,coh_an):.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
