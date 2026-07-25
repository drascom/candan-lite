#!/usr/bin/env python3
"""Yerel WhisperLive'a mikrofon akıt; metni ve varsa oturumluk speaker etiketini yaz."""

from __future__ import annotations

import argparse

from whisper_live.client import TranscriptionClient


class SegmentPrinter:
    def __init__(self) -> None:
        self.seen: set[tuple] = set()

    def __call__(self, _text: str, segments: list[dict]) -> None:
        for segment in segments:
            if not segment.get("completed", False):
                continue
            key = (
                segment.get("start"),
                segment.get("end"),
                segment.get("text"),
                segment.get("speaker"),
            )
            if key in self.seen:
                continue
            self.seen.add(key)
            speaker = segment.get("speaker") or "SPEAKER_?"
            print(
                f"[{segment.get('start', '?')}–{segment.get('end', '?')}] "
                f"{speaker}: {segment.get('text', '').strip()}",
                flush=True,
            )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", type=int, default=9090)
    p.add_argument("--model", default="small", help="tiny, base, small, medium, large-v3-turbo…")
    p.add_argument("--language", default="tr")
    p.add_argument("--diarization", action="store_true")
    p.add_argument("--max-speakers", type=int, default=4)
    p.add_argument("--save-wav", action="store_true")
    args = p.parse_args()

    client = TranscriptionClient(
        "127.0.0.1",
        args.port,
        lang=args.language,
        model=args.model,
        use_vad=True,
        log_transcription=False,
        transcription_callback=SegmentPrinter(),
        enable_diarization=args.diarization,
        max_speakers=args.max_speakers,
        save_output_recording=args.save_wav,
        output_recording_filename="whisperlive-mic.wav",
        output_transcription_path="whisperlive-mic.srt",
    )
    print("Mikrofon başladı; bitirmek için Ctrl-C.")
    client()


if __name__ == "__main__":
    main()

