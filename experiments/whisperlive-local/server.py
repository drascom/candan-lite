#!/usr/bin/env python3
"""WhisperLive'ı yalnız localhost'a bağlanan yerel Faster-Whisper sunucusu olarak çalıştır."""

from __future__ import annotations

import argparse
from pathlib import Path

import whisper_live.diarization as diarization_module
from whisper_live.server import TranscriptionServer


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", type=int, default=9090)
    p.add_argument("--max-connection-time", type=int, default=3600)
    p.add_argument(
        "--diarization-threshold",
        type=float,
        default=0.40,
        help="Online speaker kümeleme cosine eşiği (ölçülen ev verisi varsayılanı: 0.40)",
    )
    args = p.parse_args()

    # WhisperLive server bu ayarı client JSON'undan okuyabiliyor, fakat upstream
    # TranscriptionClient parametreyi göndermiyor. Yalnız bu yerel süreçte sınıfı
    # sararak ölçülmüş eşiği uygula; site-packages veya canlı sistem değişmez.
    upstream_diarizer = diarization_module.SpeakerDiarizer

    class LocalSpeakerDiarizer(upstream_diarizer):
        def __init__(self, *inner_args, **kwargs):
            kwargs["similarity_threshold"] = args.diarization_threshold
            super().__init__(*inner_args, **kwargs)

    diarization_module.SpeakerDiarizer = LocalSpeakerDiarizer

    cache = Path(__file__).resolve().parent / ".cache"
    cache.mkdir(exist_ok=True)
    print(
        f"WhisperLive ws://127.0.0.1:{args.port} — cache={cache} — "
        f"diarization_threshold={args.diarization_threshold:.2f}"
    )
    try:
        TranscriptionServer().run(
            "127.0.0.1",
            port=args.port,
            backend="faster_whisper",
            single_model=False,
            max_clients=1,
            max_connection_time=args.max_connection_time,
            cache_path=str(cache),
        )
    except KeyboardInterrupt:
        print("\nWhisperLive durduruldu.")


if __name__ == "__main__":
    main()
