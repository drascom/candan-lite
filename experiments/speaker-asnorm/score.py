#!/usr/bin/env python3
"""Üç skorlama yöntemi (aynı gömmeler üstünde) — hepsi L2-norm gömme bekler.

(a) raw_cos          : ham kosinüs (mevcut naif yöntem — baseline).
(b) asnorm           : simetrik adaptive score normalization (cohort gerekir).
(c) argmax_margin    : kapalı-küme — en yakın centroid + ikinciyle marj (cohort GEREKMEZ).

AS-norm (simetrik / S-norm; Matejka+2017, arXiv 2504.04512):
    s      = cos(enroll_centroid, test)
    top-K  = test'in cohort'a EN YÜKSEK K kosinüs skoru → μ_t, σ_t
    top-K  = centroid'in cohort'a EN YÜKSEK K kosinüs skoru → μ_e, σ_e
    s_norm = 0.5 * ((s - μ_t)/σ_t + (s - μ_e)/σ_e)
Böylece skor "sesin/centroid'in genel popülasyona benzerliği"ne göre düzeltilir →
kişiler-arası KARŞILAŞTIRILABİLİR olur (Havi'nin herkese benzemesi cezalanır).
"""

from __future__ import annotations

import numpy as np


def _u(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def centroid(embs: np.ndarray) -> np.ndarray:
    """Kayıt gömmelerinin ortalaması, sonra L2-normalize."""
    return _u(np.mean(embs, axis=0))


def raw_cos(cen: np.ndarray, test: np.ndarray) -> float:
    return float(np.dot(_u(cen), _u(test)))


def _topk_stats(scores: np.ndarray, k: int) -> tuple[float, float]:
    k = max(2, min(k, scores.size))
    top = np.sort(scores)[-k:]
    mu = float(np.mean(top))
    sd = float(np.std(top))
    return mu, (sd if sd > 1e-6 else 1e-6)


def asnorm(cen: np.ndarray, test: np.ndarray, cohort: np.ndarray, k: int = 40) -> float:
    """Simetrik AS-norm. cohort: (N,256) L2-norm yabancı gömmeler."""
    cen, test = _u(cen), _u(test)
    s = float(np.dot(cen, test))
    st = cohort @ test  # test ↔ cohort
    se = cohort @ cen  # centroid ↔ cohort
    mu_t, sd_t = _topk_stats(st, k)
    mu_e, sd_e = _topk_stats(se, k)
    return 0.5 * ((s - mu_t) / sd_t + (s - mu_e) / sd_e)


def argmax_margin(test: np.ndarray, centroids: dict[str, np.ndarray]) -> tuple[str, float, float]:
    """En yakın centroid'e ata + ikinciyle marj. cohort GEREKMEZ (kapalı-küme)."""
    test = _u(test)
    scored = sorted(((float(np.dot(_u(c), test)), name) for name, c in centroids.items()), reverse=True)
    best_s, best = scored[0]
    margin = best_s - (scored[1][0] if len(scored) > 1 else -1.0)
    return best, best_s, margin
