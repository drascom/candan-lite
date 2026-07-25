#!/usr/bin/env python3
"""İki etiketli WAV'da WhisperLive embedding ve online kümeleme eşiğini ölç."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly
from whisper_live.diarization import SpeakerDiarizer

RATE = 16_000


def chunks(path: Path, seconds: float, stride: float, count: int) -> list[tuple[float, np.ndarray]]:
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sample_rate != RATE:
        audio = resample_poly(audio, RATE, sample_rate).astype("float32")
    size = int(seconds * RATE)
    step = int(stride * RATE)
    kept: list[tuple[float, np.ndarray]] = []
    for start in range(0, len(audio) - size + 1, step):
        window = np.ascontiguousarray(audio[start : start + size], dtype="float32")
        rms = float(np.sqrt(np.mean(window * window)))
        if rms < 0.005:
            continue
        kept.append((start / RATE, window))
        if len(kept) == count:
            break
    return kept


def summary(values: list[float]) -> str:
    return f"min={min(values):.3f} med={np.median(values):.3f} max={max(values):.3f}"


def online_cluster(rows: list[tuple[str, float, np.ndarray]], threshold: float):
    centroids: list[np.ndarray] = []
    labels: list[tuple[str, int]] = []
    for name, _start, embedding in sorted(rows, key=lambda row: (row[1], row[0])):
        similarities = [float(embedding @ centroid) for centroid in centroids]
        if similarities and max(similarities) >= threshold:
            index = int(np.argmax(similarities))
            centroids[index] = centroids[index] * 0.9 + embedding * 0.1
            centroids[index] /= np.linalg.norm(centroids[index])
        else:
            index = len(centroids)
            centroids.append(embedding.copy())
        labels.append((name, index))
    return centroids, labels


def main() -> None:
    here = Path(__file__).resolve().parent
    default_root = here.parent / "speaker-asnorm" / "audio"
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("speaker_a", nargs="?", type=Path, default=default_root / "ayhan/ayhan_read.wav")
    p.add_argument("speaker_b", nargs="?", type=Path, default=default_root / "havva/havva_read.wav")
    p.add_argument("--name-a", default="Ayhan")
    p.add_argument("--name-b", default="Havva")
    p.add_argument("--seconds", type=float, default=4.0)
    p.add_argument("--stride", type=float, default=8.0)
    p.add_argument("--count", type=int, default=6)
    p.add_argument("--thresholds", type=float, nargs="+", default=[0.40, 0.45, 0.50, 0.55, 0.60])
    args = p.parse_args()

    diarizer = SpeakerDiarizer()
    rows: list[tuple[str, float, np.ndarray]] = []
    for name, path in ((args.name_a, args.speaker_a), (args.name_b, args.speaker_b)):
        for start, window in chunks(path, args.seconds, args.stride, args.count):
            embedding = np.asarray(diarizer._compute_embedding(window, RATE)).reshape(-1)
            embedding /= np.linalg.norm(embedding)
            rows.append((name, start, embedding))

    for name in (args.name_a, args.name_b):
        embeddings = [embedding for row_name, _, embedding in rows if row_name == name]
        same = [
            float(first @ second)
            for index, first in enumerate(embeddings)
            for second in embeddings[index + 1 :]
        ]
        print(f"{name} within: {summary(same)}")

    a = [embedding for name, _, embedding in rows if name == args.name_a]
    b = [embedding for name, _, embedding in rows if name == args.name_b]
    print(f"cross: {summary([float(first @ second) for first in a for second in b])}")

    for threshold in args.thresholds:
        centroids, labels = online_cluster(rows, threshold)
        left = dict(Counter(index for name, index in labels if name == args.name_a))
        right = dict(Counter(index for name, index in labels if name == args.name_b))
        print(
            f"threshold={threshold:.2f} clusters={len(centroids)} "
            f"{args.name_a}={left} {args.name_b}={right}"
        )


if __name__ == "__main__":
    main()

