#!/usr/bin/env python3
"""Mac microphone client for the isolated CrispASR A/B server.

`turn` records one manually bounded room turn and uploads it as a WAV. This is
the mode that tests diarization, because the whole sequence is available to
CrispASR. `stream` sends float32 PCM to CrispASR's Whisper WebSocket and shows
incremental text only; the upstream streaming endpoint does not diarize.
"""

from __future__ import annotations

import argparse
import asyncio
from io import BytesIO
import json
import queue
import sys
import wave

import numpy as np
import requests
import sounddevice as sd
import websockets


SAMPLE_RATE = 16_000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("turn", "stream"), nargs="?", default="turn")
    parser.add_argument("--host", default="192.168.0.25", help="CrispASR sunucusunun IP'si")
    parser.add_argument("--port", type=int, default=8090, help="HTTP portu")
    parser.add_argument("--ws-port", type=int, default=8091, help="ham PCM WebSocket portu")
    parser.add_argument("--device", help="sounddevice giriş aygıtı adı/indeksi")
    parser.add_argument(
        "--max-speakers",
        type=int,
        default=4,
        help="tek kayıtta beklenen azami konuşmacı sayısı (varsayılan: 4)",
    )
    parser.add_argument("--max-seconds", type=float, default=45, help="turn modunda azami kayıt süresi")
    parser.add_argument("--timeout", type=float, default=120, help="HTTP yanıt zaman aşımı")
    parser.add_argument(
        "--context",
        action="store_true",
        help="etiketli transkripti yerel Gemma'ya bağlam düzeltmesi için gönder",
    )
    parser.add_argument(
        "--brain-url",
        default="http://192.168.0.25:8082",
        help="OpenAI-uyumlu yerel beyin adresi",
    )
    return parser.parse_args()


def wav_bytes(samples: np.ndarray) -> bytes:
    clipped = np.clip(samples.reshape(-1), -1.0, 1.0)
    pcm = (clipped * 32767).astype("<i2").tobytes()
    result = BytesIO()
    with wave.open(result, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)
    return result.getvalue()


def format_time(value: object) -> str:
    if isinstance(value, (int, float)):
        return f"{value:6.2f}s"
    return str(value or "? ")


def print_diarized(payload: dict[str, object]) -> None:
    print("\n--- CrispASR sonucu ---")
    segments = payload.get("segments") or payload.get("transcription") or []
    if not isinstance(segments, list):
        print(payload.get("text", payload))
        return
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        timestamps = segment.get("timestamps", {})
        if not isinstance(timestamps, dict):
            timestamps = {}
        start = segment.get("start", timestamps.get("from"))
        end = segment.get("end", timestamps.get("to"))
        speaker = str(segment.get("speaker", "speaker ?")).strip()
        text = str(segment.get("text", "")).strip()
        print(f"[{format_time(start)} - {format_time(end)}] {speaker}: {text}")
    text = str(payload.get("text", "")).strip()
    if text:
        print(f"\nTam metin: {text}")


def context_lines(payload: dict[str, object]) -> str:
    """LLM'e yalnız segment metni ve değiştirilemez anonim etiketleri ver."""
    segments = payload.get("segments") or payload.get("transcription") or []
    if not isinstance(segments, list):
        return ""
    lines: list[str] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        speaker = str(segment.get("speaker", "?")).strip()
        text = str(segment.get("text", "")).strip()
        if text:
            lines.append(f"{speaker}: {text}")
    return "\n".join(lines)


def print_context_correction(payload: dict[str, object], args: argparse.Namespace) -> None:
    transcript = context_lines(payload)
    if not transcript:
        return
    system = (
        "Sen Türkçe konuşma transkripti için ihtiyatlı bir düzelticisin. "
        "Konuşmacı etiketleri (A, B, C, D) değiştirilemez; kişi isimleri uydurma, "
        "etiketleri birleştirme ve konuşma ekleme. Yalnız mevcut bağlamın açıkça "
        "doğruladığı ASR yazım/kelime hatalarını düzelt. Emin değilsen özgün metni koru. "
        "Açıklama yapmadan, kaynak sırasını ve `ETIKET: metin` biçimini koruyarak yaz."
    )
    try:
        response = requests.post(
            f"{args.brain_url.rstrip('/')}/v1/chat/completions",
            json={
                "model": "gemma-4-12B-it-qat-q4_0",
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": transcript}],
                "temperature": 0,
                "max_tokens": 1024,
            },
            timeout=args.timeout,
        )
        response.raise_for_status()
        choices = response.json().get("choices", [])
        corrected = choices[0]["message"]["content"].strip() if choices else ""
        if corrected:
            print(f"\n--- Bağlamla düzeltilmiş metin (LLM) ---\n{corrected}")
    except (requests.RequestException, KeyError, IndexError, TypeError) as exc:
        print(f"Bağlam düzeltmesi atlandı: {exc}", file=sys.stderr)


def record_turn(args: argparse.Namespace) -> np.ndarray:
    blocks: list[np.ndarray] = []

    def capture(indata: np.ndarray, frames: int, time: object, status: sd.CallbackFlags) -> None:
        if status:
            print(f"Mikrofon durumu: {status}", file=sys.stderr)
        blocks.append(indata.copy())

    input("Kaydı başlatmak için Enter'a basın… ")
    print("● Kayıt açık. Ayhan ve Havi sırayla konuşsun; bitince Enter'a basın.")
    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocksize=1600,
        device=args.device,
        callback=capture,
    ):
        try:
            input()
        except KeyboardInterrupt:
            pass
    if not blocks:
        raise RuntimeError("Mikrofondan ses gelmedi")
    samples = np.concatenate(blocks, axis=0)
    duration = len(samples) / SAMPLE_RATE
    if duration > args.max_seconds:
        raise RuntimeError(f"Kayıt {duration:.1f} sn; --max-seconds sınırını aştı")
    print(f"■ Kayıt kapandı: {duration:.1f} sn. Sunucuya gönderiliyor…")
    return samples


def run_turn(args: argparse.Namespace) -> None:
    samples = record_turn(args)
    url = f"http://{args.host}:{args.port}/v1/audio/transcriptions"
    response = requests.post(
        url,
        files={"file": ("live-turn.wav", wav_bytes(samples), "audio/wav")},
        data={
            "language": "tr",
            "response_format": "diarized_json",
            "vad": "true",
            "diarize": "true",
            "diarize_method": "pyannote",
            "diarize_embedder": "auto",
            "diarize_max_speakers": str(args.max_speakers),
        },
        timeout=args.timeout,
    )
    response.raise_for_status()
    payload = response.json()
    print_diarized(payload)
    if args.context:
        print_context_correction(payload, args)


async def run_stream(args: argparse.Namespace) -> None:
    audio: asyncio.Queue[bytes] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    ws_url = f"ws://{args.host}:{args.ws_port}"

    def capture(indata: np.ndarray, frames: int, time: object, status: sd.CallbackFlags) -> None:
        if status:
            print(f"Mikrofon durumu: {status}", file=sys.stderr)
        loop.call_soon_threadsafe(audio.put_nowait, indata.copy().astype("<f4").tobytes())

    async with websockets.connect(ws_url, max_size=None) as socket:
        print("● Canlı metin açık. Çıkmak için Ctrl+C. (Bu mod konuşmacı ayırmaz.)")

        async def send_audio() -> None:
            while True:
                await socket.send(await audio.get())

        async def receive_updates() -> None:
            last_text = ""
            async for message in socket:
                event = json.loads(message)
                text = str(event.get("text", "")).strip()
                if not text or text == last_text:
                    continue
                last_text = text
                marker = "✓" if event.get("final") else "…"
                print(f"\r{marker} {text}", end="\n" if event.get("final") else "", flush=True)

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=1600,
            device=args.device,
            callback=capture,
        ):
            await asyncio.gather(send_audio(), receive_updates())


def main() -> int:
    args = parse_args()
    try:
        if args.mode == "turn":
            run_turn(args)
        else:
            asyncio.run(run_stream(args))
    except (KeyboardInterrupt, requests.RequestException, OSError, RuntimeError) as exc:
        print(f"Hata: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
