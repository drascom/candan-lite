"""OmniVoice TABAN ölçümü — CANLI köprüden (127.0.0.1:8808), sunucunun kendi GPU'sunda.

Neden: Higgs'i Mac'teki tts-local-bench sayılarıyla karşılaştırmak ADİL DEĞİL
(orada MPS, burada RTX 3090). Aynı sunucuda, aynı cümlelerle, aynı referans sesle
OmniVoice'i bir kez koşarız; Higgs'in yanına o sayıları koyarız.

ÖNEMLİ: Bu betik omnivoice-bridge DURDURULMADAN ÖNCE koşmalı. Sadece HTTP isteği
atar — köprüye, worker'a, hiçbir dosyaya DOKUNMAZ.

Serverdeki canlı yol (`worker/omnivoice_tts.py` → `_post_tts`) ile aynı form:
    text, language=Turkish, mode=clone, use_pinned=true
`instruct` VERİLMEZ → nötr ses (canlı sistemde nötr tur WS ile gider ama ses
kimliği aynı; ölçüm için HTTP yolu kullanılıyor).

    /opt/higgs-venv/bin/python run_omnivoice_server.py

Bağımlılık: stdlib + numpy.
"""
from __future__ import annotations

import io
import time
import urllib.request
import uuid
import wave

import numpy as np

from bench_common import OUT, bench

URL = "http://127.0.0.1:8808/api/tts"
FIELDS = {"language": "Turkish", "mode": "clone", "use_pinned": "true"}


def _multipart(fields: dict[str, str]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    buf = io.BytesIO()
    for k, v in fields.items():
        buf.write(f"--{boundary}\r\n".encode())
        buf.write(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode())
        buf.write(v.encode("utf-8"))
        buf.write(b"\r\n")
    buf.write(f"--{boundary}--\r\n".encode())
    return buf.getvalue(), f"multipart/form-data; boundary={boundary}"


def synth(text: str) -> tuple[np.ndarray, int]:
    body, ctype = _multipart(FIELDS | {"text": text})
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": ctype})
    with urllib.request.urlopen(req, timeout=180) as resp:
        raw = resp.read()
    with wave.open(io.BytesIO(raw), "rb") as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        frames = w.readframes(w.getnframes())
    if sw != 2:
        raise RuntimeError(f"beklenmeyen sampwidth={sw}")
    a = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    return a, sr


def main() -> None:
    # Köprü ayakta mı + hangi referansı kullanıyor?
    with urllib.request.urlopen("http://127.0.0.1:8808/api/default", timeout=10) as r:
        info = r.read().decode()
    print(f"[köprü] {info}", flush=True)

    # Isıtma: ilk istek model/cache ısınmasını içerir, ölçümü bozar.
    t0 = time.perf_counter()
    synth("Merhaba, bu bir ısıtma cümlesidir.")
    print(f"[ısıtma] {time.perf_counter() - t0:.2f}s (ölçüm DIŞI)", flush=True)

    bench("omnivoice-server", synth, device="cuda (RTX 3090) · canlı köprü :8808",
          extra={"not": "HTTP /api/tts, mode=clone use_pinned=true, instruct yok. "
                        "Streaming değil → wall_s = ilk sese kadar geçen süre."})
    print(f"çıktı: {OUT / 'omnivoice-server'}")


if __name__ == "__main__":
    main()
