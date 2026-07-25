#!/usr/bin/env python3
"""Build a sequential 16 kHz mono PCM WAV fixture without external dependencies."""

from __future__ import annotations

import argparse
import wave
from pathlib import Path


RATE = 16_000
SILENCE_SECONDS = 0.8


def read_pcm(path: Path) -> bytes:
    with wave.open(str(path), "rb") as source:
        if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, RATE):
            raise ValueError(f"{path}: 16 kHz mono s16 WAV gerekli")
        return source.readframes(source.getnframes())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("inputs", nargs=4, type=Path)
    args = parser.parse_args()

    parts = [read_pcm(path) for path in args.inputs]
    silence = b"\0\0" * int(RATE * SILENCE_SECONDS)
    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(RATE)
        for index, pcm in enumerate(parts):
            target.writeframes(pcm)
            if index != len(parts) - 1:
                target.writeframes(silence)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
