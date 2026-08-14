#!/usr/bin/env python3
"""Tamamlanmış closed-set kayıt oturumlarını ReDimNet2 speaker DB'ye aktar.

Örnek:
  cd worker
  .venv/bin/python tools/import_closed_set_recordings.py \
    /path/to/closed-set-speaker-id/recordings --dry-run
  .venv/bin/python tools/import_closed_set_recordings.py \
    /path/to/closed-set-speaker-id/recordings

Varsayılan olarak yalnız ``kind=read`` klipleri profile eklenir. Natural klipler
kalibrasyon/doğrulama verisi olarak dışarıda kalır. Kaynak etiketi sabit olduğu
için aynı kayıt ikinci çalıştırmada yeniden eklenmez.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

WORKER_DIR = Path(__file__).resolve().parents[1]
if str(WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(WORKER_DIR))

try:
    from dotenv import load_dotenv  # noqa: E402
except ImportError:  # dry-run ve minimal bakım ortamı
    load_dotenv = None
if load_dotenv is not None:
    load_dotenv(WORKER_DIR / ".env")

from speaker_id import (  # noqa: E402
    SpeakerStore,
    create_speaker_id,
    emb_to_bytes,
    name_key,
)


def _sessions(root: Path) -> list[tuple[Path, dict]]:
    found: list[tuple[Path, dict]] = []
    for manifest in sorted(root.rglob("session.json")):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"ATLA: {manifest}: {exc}", file=sys.stderr)
            continue
        if data.get("completed") is True and data.get("speaker_name"):
            found.append((manifest, data))
    return found


def _audio(path: Path) -> tuple[np.ndarray, int]:
    samples, rate = sf.read(path, dtype="float32", always_2d=True)
    return np.ascontiguousarray(samples.mean(axis=1)), int(rate)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recordings", type=Path, help="session.json dosyalarını içeren kök")
    parser.add_argument("--dry-run", action="store_true", help="model/DB açmadan planı göster")
    parser.add_argument(
        "--include-natural",
        action="store_true",
        help="natural klipleri de profile ekle (normal geçişte önerilmez)",
    )
    parser.add_argument(
        "--verify-natural",
        action="store_true",
        help="aktarımdan sonra profile katılmayan natural kliplerle doğrula",
    )
    args = parser.parse_args(argv)
    if args.include_natural and args.verify_natural:
        parser.error("--verify-natural için natural klipler profile eklenmemeli")
    root = args.recordings.expanduser().resolve()
    if not root.is_dir():
        parser.error(f"kayıt dizini yok: {root}")

    sessions = _sessions(root)
    planned: list[tuple[Path, str, Path]] = []
    for manifest, data in sessions:
        name = " ".join(str(data["speaker_name"]).split())
        for clip in data.get("clips") or []:
            if clip.get("kind") != "read" and not args.include_natural:
                continue
            audio_path = manifest.parent / str(clip.get("path") or "")
            if audio_path.is_file():
                planned.append((manifest, name, audio_path))
            else:
                print(f"ATLA: ses dosyası yok: {audio_path}", file=sys.stderr)

    names = sorted({name for _manifest, name, _audio_path in planned})
    print(
        f"tamamlanmış oturum={len(sessions)} kişi={len(names)} "
        f"klip={len(planned)} tür={'read+natural' if args.include_natural else 'read'}"
    )
    if args.dry_run:
        for name in names:
            print(f"  {name}: {sum(1 for _m, n, _a in planned if n == name)} klip")
        return 0
    if not planned:
        print("Aktarılacak tamamlanmış kayıt bulunamadı.", file=sys.stderr)
        return 2

    speaker = create_speaker_id()
    store = SpeakerStore()
    added = skipped = 0
    for manifest, name, audio_path in planned:
        row = store.create_speaker_sync(name)
        samples, rate = _audio(audio_path)
        embeddings = speaker.embed_samples_many(samples, rate)
        base_source = "closed-set-import:" + str(audio_path.relative_to(root))
        file_added = 0
        for index, embedding in enumerate(embeddings):
            source = f"{base_source}#window:{index}"
            if store.has_sample_source_sync(int(row["id"]), source):
                skipped += 1
                continue
            store.add_sample_sync(
                int(row["id"]),
                emb_to_bytes(embedding),
                speaker.dim,
                speaker.model_id,
                source,
            )
            added += 1
            file_added += 1
        print(
            f"EKLE: {name}: {audio_path.relative_to(manifest.parent)} "
            f"({file_added}/{len(embeddings)} pencere)"
        )

    print(f"tamamlandı: eklenen={added} zaten_var={skipped} db={store.path}")
    if not args.verify_natural:
        return 0

    speaker.reload(store.all_speaker_embeddings_sync())
    correct = unknown = wrong = total = 0
    for manifest, data in sessions:
        expected = " ".join(str(data["speaker_name"]).split())
        for clip in data.get("clips") or []:
            if clip.get("kind") != "natural":
                continue
            audio_path = manifest.parent / str(clip.get("path") or "")
            if not audio_path.is_file():
                continue
            samples, rate = _audio(audio_path)
            identified, score = speaker.identify(speaker.embed_samples(samples, rate))
            total += 1
            if identified is None:
                unknown += 1
                verdict = "Bilinmeyen"
            elif name_key(identified) == name_key(expected):
                correct += 1
                verdict = "doğru"
            else:
                wrong += 1
                verdict = f"YANLIŞ:{identified}"
            print(f"TEST: {expected}: {audio_path.name}: {verdict} skor={score:.3f}")
    print(
        f"doğrulama: doğru={correct}/{total} Bilinmeyen={unknown} yanlış={wrong}"
    )
    return 3 if wrong else 0


if __name__ == "__main__":
    raise SystemExit(main())
