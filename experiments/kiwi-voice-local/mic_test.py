#!/usr/bin/env python3
"""Kiwi Voice'un speaker-ID çekirdeğini terminal mikrofonuyla izole test et."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import numpy as np
import sounddevice as sd

HERE = Path(__file__).resolve().parent
UPSTREAM = HERE / ".upstream" / "kiwi-voice"
if UPSTREAM.is_dir():
    sys.path.insert(0, str(UPSTREAM))


def record(seconds: float, device: int | str | None, sample_rate: int = 16_000) -> np.ndarray:
    print(f"{seconds:.1f} saniye konuşun…", flush=True)
    audio = sd.rec(
        int(seconds * sample_rate),
        samplerate=sample_rate,
        channels=1,
        dtype="float32",
        device=device,
    )
    sd.wait()
    mono = np.ascontiguousarray(audio[:, 0])
    rms = float(np.sqrt(np.mean(np.square(mono)))) if mono.size else 0.0
    print(f"Kayıt tamamlandı: {mono.size / sample_rate:.1f}s, RMS={rms:.4f}")
    if rms < 0.003:
        raise SystemExit("Ses seviyesi çok düşük; mikrofon aygıtını ve iznini kontrol edin.")
    return mono


def profile_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", name.casefold()).strip("_")
    return slug or "speaker"


def build_identifier():
    # Import is deliberately delayed so `devices` works before ML dependencies exist.
    if not UPSTREAM.is_dir():
        raise SystemExit("Kiwi kaynak kodu yok. Önce `bash setup.sh` çalıştırın.")
    from kiwi.speaker_id import SpeakerIdentifier
    from pyannote.audio.pipelines.speaker_verification import PretrainedSpeakerEmbedding

    upstream_checkpoint = (
        UPSTREAM / "models" / "pyannote-embedding-upstream" / "pytorch_model.bin"
    )
    compatibility_checkpoint = (
        UPSTREAM / "models" / "pyannote-embedding" / "pytorch_model.bin"
    )
    default_checkpoint = (
        upstream_checkpoint if upstream_checkpoint.is_file() else compatibility_checkpoint
    )
    checkpoint = Path(os.getenv("KIWI_EMBEDDING_CHECKPOINT", str(default_checkpoint))).expanduser()
    if not checkpoint.is_file():
        raise SystemExit(
            f"Embedding checkpoint bulunamadı: {checkpoint}\n"
            "Önce `bash setup.sh` çalıştırın veya KIWI_EMBEDDING_CHECKPOINT verin."
        )
    profile_dir = Path(
        os.getenv("KIWI_PROFILE_DIR", str(HERE / "profiles" / checkpoint.parent.name))
    ).expanduser()
    identifier = SpeakerIdentifier(str(profile_dir))
    # Upstream Kiwi klasör yolunu modele veriyor; pyannote 3.x ise doğrudan
    # checkpoint dosyası bekliyor. Deney katmanında doğru dosyayı yükle.
    identifier.embedding_model = PretrainedSpeakerEmbedding(
        str(checkpoint.resolve()), device=identifier.device
    )
    identifier._model_loaded = True
    identifier.experiment_model_source = str(checkpoint.resolve())
    identifier.SIMILARITY_THRESHOLD = float(os.getenv("KIWI_THRESHOLD", "0.40"))
    return identifier


def require_real_model(identifier, probe: np.ndarray) -> None:
    embedding = identifier.extract_embedding(probe, 16_000)
    if embedding is None:
        raise SystemExit("Kiwi embedding çıkaramadı.")
    if identifier.embedding_model is None:
        raise SystemExit(
            "Kiwi pyannote modelini yükleyemedi ve basit spektral fallback'e düştü. "
            "Bu sonuç speaker-ID karşılaştırması için geçerli değildir; yukarıdaki model "
            "yükleme hatasını düzeltmeden teste devam etmeyin."
        )
    print(
        f"Gerçek pyannote embedding etkin: dim={embedding.size}, device={identifier.device}, "
        f"threshold={identifier.SIMILARITY_THRESHOLD:.2f}, "
        f"checkpoint={identifier.experiment_model_source}"
    )


def doctor() -> None:
    identifier = build_identifier()
    probe = np.random.default_rng(17).normal(0, 0.01, 32_000).astype("float32")
    require_real_model(identifier, probe)
    print(f"Profil dizini: {identifier.profiles_dir}")


def enroll(args: argparse.Namespace) -> None:
    identifier = build_identifier()
    pid = args.id or profile_id(args.name)
    for index in range(args.samples):
        print(f"\nÖrnek {index + 1}/{args.samples} — doğal ve farklı bir cümle söyleyin.")
        audio = record(args.seconds, args.device)
        if index == 0:
            require_real_model(identifier, audio)
        ok = identifier.add_profile_sample(
            profile_id=pid,
            audio=audio,
            sample_rate=16_000,
            name=args.name,
            priority="owner" if args.owner else "guest",
        )
        if not ok:
            raise SystemExit(f"Örnek {index + 1} kaydedilemedi.")
    print(f"\nKayıt tamam: id={pid!r}, ad={args.name!r}, örnek={args.samples}")


def identify(args: argparse.Namespace) -> None:
    identifier = build_identifier()
    if not identifier.profiles:
        raise SystemExit("Profil yok. Önce `enroll` komutuyla en az iki kişi kaydedin.")
    audio = record(args.seconds, args.device)
    require_real_model(identifier, audio)
    speaker, score = identifier.identify_speaker(audio, 16_000)
    print(f"Sonuç: speaker={speaker!r}, cosine={score:.4f}")


def list_profiles() -> None:
    identifier = build_identifier()
    profiles = identifier.get_profile_info()
    if not profiles:
        print("Kayıtlı profil yok.")
        return
    for pid, info in profiles.items():
        print(f"{pid}: {info}")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("devices", help="PortAudio mikrofon aygıtlarını listele")

    ep = sub.add_parser("enroll", help="Bir kişiden birkaç ses örneği kaydet")
    ep.add_argument("name")
    ep.add_argument("--id")
    ep.add_argument("--owner", action="store_true")
    ep.add_argument("--samples", type=int, default=3)
    ep.add_argument("--seconds", type=float, default=5.0)
    ep.add_argument("--device", type=int)

    ip = sub.add_parser("identify", help="Yeni mikrofon kaydını profillerle karşılaştır")
    ip.add_argument("--seconds", type=float, default=5.0)
    ip.add_argument("--device", type=int)
    sub.add_parser("profiles", help="Yerel deney profillerini listele")
    sub.add_parser("doctor", help="Mikrofon açmadan gerçek embedding modelini doğrula")
    return p


def main() -> None:
    args = parser().parse_args()
    if args.command == "devices":
        print(sd.query_devices())
    elif args.command == "enroll":
        enroll(args)
    elif args.command == "identify":
        identify(args)
    elif args.command == "profiles":
        list_profiles()
    elif args.command == "doctor":
        doctor()
    else:  # pragma: no cover
        sys.exit(2)


if __name__ == "__main__":
    main()
