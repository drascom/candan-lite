#!/usr/bin/env python3
"""Whisper (Wyoming) + NVIDIA Streaming Sortformer microphone/file experiment.

The production worker is not imported or changed. A complete recording is first
transcribed by the existing Wyoming faster-whisper service, then diarized by the
streaming Sortformer model. Finally, each speaker segment is sent to the same
Whisper service so the terminal can print speaker-labelled text.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly
from wyoming.event import Event, async_read_event, async_write_event


SAMPLE_RATE = 16_000
MODEL_ID = "nvidia/diar_streaming_sortformer_4spk-v2.1"


@dataclass
class Segment:
    start: float
    end: float
    speaker: str
    text: str = ""


def load_audio(path: Path) -> np.ndarray:
    audio, rate = sf.read(path, dtype="float32", always_2d=True)
    mono = audio.mean(axis=1)
    if rate != SAMPLE_RATE:
        divisor = math.gcd(int(rate), SAMPLE_RATE)
        mono = resample_poly(mono, SAMPLE_RATE // divisor, int(rate) // divisor)
    return np.ascontiguousarray(np.clip(mono, -1.0, 1.0), dtype=np.float32)


def record_microphone(seconds: float, device: int | None) -> np.ndarray:
    import sounddevice as sd

    print(f"[mic] {seconds:.1f} saniye kayıt başlıyor; Ayhan ve Havi sırayla konuşabilir.")
    audio = sd.rec(
        int(seconds * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        device=device,
    )
    sd.wait()
    return np.ascontiguousarray(audio[:, 0], dtype=np.float32)


async def whisper_transcribe(
    audio: np.ndarray,
    *,
    host: str,
    port: int,
    language: str,
    timeout: float,
) -> str:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    reader, writer = await asyncio.open_connection(host, port)
    try:
        data = {"language": language} if language else {}
        await async_write_event(Event(type="transcribe", data=data), writer)
        await async_write_event(
            Event(
                type="audio-start",
                data={"rate": SAMPLE_RATE, "width": 2, "channels": 1},
            ),
            writer,
        )
        chunk_bytes = SAMPLE_RATE * 2
        for offset in range(0, len(pcm), chunk_bytes):
            await async_write_event(
                Event(
                    type="audio-chunk",
                    data={"rate": SAMPLE_RATE, "width": 2, "channels": 1},
                    payload=pcm[offset : offset + chunk_bytes],
                ),
                writer,
            )
        await async_write_event(Event(type="audio-stop"), writer)
        while True:
            event = await asyncio.wait_for(async_read_event(reader), timeout)
            if event is None:
                return ""
            if event.type == "transcript":
                return str((event.data or {}).get("text", "") or "").strip()
    finally:
        writer.close()
        await writer.wait_closed()


def parse_segment(raw: Any) -> Segment:
    if isinstance(raw, str):
        parts = raw.replace(",", " ").split()
        if len(parts) < 3:
            raise ValueError(f"Beklenmeyen Sortformer segmenti: {raw!r}")
        return Segment(float(parts[0]), float(parts[1]), str(parts[2]))
    if isinstance(raw, dict):
        return Segment(
            float(raw["start"]),
            float(raw["end"]),
            str(raw.get("speaker", raw.get("label", "speaker_?"))),
        )
    if isinstance(raw, (tuple, list)) and len(raw) >= 3:
        return Segment(float(raw[0]), float(raw[1]), str(raw[2]))
    raise ValueError(f"Beklenmeyen Sortformer segment tipi: {type(raw).__name__}: {raw!r}")


def merge_segments(segments: list[Segment], *, gap: float, min_seconds: float) -> list[Segment]:
    merged: list[Segment] = []
    for segment in sorted(segments, key=lambda item: (item.start, item.end, item.speaker)):
        if segment.end - segment.start < min_seconds:
            continue
        if merged and merged[-1].speaker == segment.speaker and segment.start - merged[-1].end <= gap:
            merged[-1].end = max(merged[-1].end, segment.end)
        else:
            merged.append(segment)
    return merged


def configure_sortformer(model: Any, profile: str) -> None:
    module = model.sortformer_modules
    if profile == "low-latency":
        module.chunk_len = 6
        module.chunk_right_context = 7
        module.fifo_len = 188
        module.spkcache_update_period = 144
        module.spkcache_len = 188
    else:
        module.chunk_len = 340
        module.chunk_right_context = 40
        module.fifo_len = 40
        module.spkcache_update_period = 300
        module.spkcache_len = 188
    module._check_streaming_parameters()


def diarize(audio: np.ndarray, *, model_id: str, profile: str, device: str) -> tuple[list[Segment], dict[str, float]]:
    import torch
    from nemo.collections.asr.models import SortformerEncLabelModel

    if device.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("CUDA istendi fakat PyTorch CUDA göremiyor")
    torch.set_grad_enabled(False)
    started = time.perf_counter()
    model = SortformerEncLabelModel.from_pretrained(model_id).eval().to(device)
    configure_sortformer(model, profile)
    loaded = time.perf_counter()
    predictions = model.diarize(
        audio=[audio],
        sample_rate=SAMPLE_RATE,
        batch_size=1,
        num_workers=0,
        verbose=False,
    )
    finished = time.perf_counter()
    return [parse_segment(item) for item in predictions[0]], {
        "model_load_seconds": loaded - started,
        "diarization_seconds": finished - loaded,
    }


async def transcribe_segments(
    audio: np.ndarray,
    segments: list[Segment],
    *,
    host: str,
    port: int,
    language: str,
    timeout: float,
) -> None:
    for index, segment in enumerate(segments, start=1):
        start = max(0, int(segment.start * SAMPLE_RATE))
        end = min(audio.size, int(segment.end * SAMPLE_RATE))
        if end <= start:
            continue
        segment.text = await whisper_transcribe(
            audio[start:end],
            host=host,
            port=port,
            language=language,
            timeout=timeout,
        )
        print(f"[{segment.start:7.2f}–{segment.end:7.2f}] {segment.speaker}: {segment.text}")
        if index < len(segments):
            await asyncio.sleep(0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path, help="WAV/FLAC/MP3 gibi mevcut ses dosyası")
    source.add_argument("--mic", type=float, metavar="SECONDS", help="mikrofondan kayıt süresi")
    parser.add_argument("--device", type=int, default=None, help="sounddevice mikrofon indeksi")
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--whisper-host", default=os.getenv("STT_HOST", "127.0.0.1"))
    parser.add_argument("--whisper-port", type=int, default=int(os.getenv("STT_PORT", "10300")))
    parser.add_argument("--language", default="tr")
    parser.add_argument("--whisper-timeout", type=float, default=60.0)
    parser.add_argument("--model", default=MODEL_ID)
    parser.add_argument("--profile", choices=("low-latency", "quality"), default="low-latency")
    parser.add_argument("--torch-device", default="cuda")
    parser.add_argument("--merge-gap", type=float, default=0.25)
    parser.add_argument("--min-segment", type=float, default=0.30)
    parser.add_argument("--skip-segment-stt", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("result.json"))
    parser.add_argument("--keep-wav", type=Path, default=None)
    return parser


async def main_async(args: argparse.Namespace) -> int:
    if args.list_devices:
        import sounddevice as sd

        print(sd.query_devices())
        return 0

    if args.input:
        if not args.input.is_file():
            raise FileNotFoundError(args.input)
        audio = load_audio(args.input)
        source = str(args.input.resolve())
    else:
        audio = record_microphone(args.mic, args.device)
        source = f"microphone:{args.device if args.device is not None else 'default'}"

    duration = audio.size / SAMPLE_RATE
    if args.keep_wav:
        args.keep_wav.parent.mkdir(parents=True, exist_ok=True)
        sf.write(args.keep_wav, audio, SAMPLE_RATE, subtype="PCM_16")

    with tempfile.TemporaryDirectory(prefix="candan-nemo-") as temporary:
        normalized = Path(temporary) / "input-16k-mono.wav"
        sf.write(normalized, audio, SAMPLE_RATE, subtype="PCM_16")

        whisper_started = time.perf_counter()
        full_text = await whisper_transcribe(
            audio,
            host=args.whisper_host,
            port=args.whisper_port,
            language=args.language,
            timeout=args.whisper_timeout,
        )
        whisper_seconds = time.perf_counter() - whisper_started
        print(f"\n[Whisper] {full_text}\n")

        raw_segments, timing = diarize(
            audio,
            model_id=args.model,
            profile=args.profile,
            device=args.torch_device,
        )
        segments = merge_segments(raw_segments, gap=args.merge_gap, min_seconds=args.min_segment)
        print(f"[Sortformer] {len(raw_segments)} ham, {len(segments)} birleştirilmiş segment")

        if args.skip_segment_stt:
            for segment in segments:
                print(f"[{segment.start:7.2f}–{segment.end:7.2f}] {segment.speaker}")
        else:
            await transcribe_segments(
                audio,
                segments,
                host=args.whisper_host,
                port=args.whisper_port,
                language=args.language,
                timeout=args.whisper_timeout,
            )

    result = {
        "source": source,
        "duration_seconds": duration,
        "sample_rate": SAMPLE_RATE,
        "whisper": {
            "host": args.whisper_host,
            "port": args.whisper_port,
            "language": args.language,
            "full_text": full_text,
            "seconds": whisper_seconds,
        },
        "sortformer": {
            "model": args.model,
            "profile": args.profile,
            **timing,
            "rtf": timing["diarization_seconds"] / duration if duration else None,
        },
        "segments": [asdict(segment) for segment in segments],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"\nSüre: audio={duration:.2f}s whisper={whisper_seconds:.2f}s "
        f"sortformer={timing['diarization_seconds']:.2f}s "
        f"RTF={result['sortformer']['rtf']:.3f}"
    )
    print(f"JSON: {args.output.resolve()}")
    return 0


def main() -> int:
    parser = build_parser()
    if "--list-devices" in sys.argv and not ({"--input", "--mic"} & set(sys.argv)):
        parser = argparse.ArgumentParser()
        parser.add_argument("--list-devices", action="store_true")
        parser.parse_args()
        import sounddevice as sd

        print(sd.query_devices())
        return 0
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
