#!/usr/bin/env python3
"""higgs-tts — canlı TTS servisi (Higgs TTS 3 / 4B), `bridge_server.py` muadili.

NEDEN AYRI SERVİS: model yüklemesi ~3 s ve 8-10 GB VRAM. Her istekte yüklemek
imkânsız → süreç ömrü boyunca BİR KEZ yüklenir, referans sesi de BİR KEZ kodlanır
(`refs/default-ref.codes.pt`), sonra her istek yalnız `generate_speech()` çağırır.

SÖZLEŞME (worker/higgs_tts.py bunu konuşur):
  GET  /health        → 200 {"ready":true,...} · yüklenirken 503 (ready:false)
  GET  /api/default   → 200 referans kimliği (cache anahtarı buradan türetilir)
  POST /api/tts       → 200 audio/wav (24 kHz mono s16le) · JSON veya form gövde
                        {"text": "...", "mood": "excited|sad|null", "speed": 1.0,
                         "format": "wav|pcm"}

HATA POLİTİKASI: sessizce boş ses DÖNMEZ.
  400 metin yok/boş · 503 model henüz hazır değil · 502 model ses üretmedi (0 frame)
  500 beklenmedik hata (gövdede JSON `{"error": ...}`)
Boş ses 200 ile dönerse worker onu "başarılı ama sessiz" sanır ve sorun görünmez olur.

MOOD: bu turda BİLİNÇLİ OLARAK ses üzerinde etkisiz. Higgs'in inline prosody
token'ları (`<|prosody:speed_fast|>` …) var ama doğrulanmadan metne token gömmek
riskli — model onları SESLENDİREBİLİR. Parametre uçtan uca taşınıyor ve yanıt
başlığında (`X-Higgs-Mood`) raporlanıyor; eşleme ayrı bir iş olarak eklenecek.

BAĞIMLILIK: yalnız stdlib + torch/transformers/numpy (higgs-venv'de var).
fastapi/uvicorn KURULU DEĞİL, bilerek: yeni paket = yeni kırılma yüzeyi.
Eşzamanlılık tek `_SYNTH_LOCK` ile serileştirilir (tek GPU, tek model).

ÇALIŞTIRMA:
    HF_HOME=/opt/higgs-models/hf /opt/higgs-venv/bin/python /opt/higgs-tts/server.py
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import signal
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("higgs-tts")

# ── Yapılandırma (hepsi env ile geçersiz kılınabilir) ─────────────────────────
HERE = Path(__file__).resolve().parent

MODEL_ID = os.environ.get(
    "HIGGS_MODEL", "multimodalart/higgs-audio-v3-tts-4b-transformers")
# Referans ses: OmniVoice ile AYNI dosya (kullanıcı bu sesi seçti). SALT OKUNUR —
# bu servis /opt/omnivoice/ altına ASLA yazmaz.
REF_AUDIO = Path(os.environ.get("HIGGS_REF_AUDIO", "/opt/omnivoice/default-ref.wav"))
REF_TEXT = os.environ.get(
    "HIGGS_REF_TEXT",
    "Merhaba, bu bir Türkçe seslendirme testidir. VoxCPM 2 ile uzun kitapları "
    "sesli kitaba dönüştürebilirsiniz.",
)
REF_CODES = Path(os.environ.get("HIGGS_REF_CODES", str(HERE / "refs" / "default-ref.codes.pt")))

HOST = os.environ.get("HIGGS_HOST", "0.0.0.0")
PORT = int(os.environ.get("HIGGS_PORT", "8809"))

# run_higgs.py ile AYNI örnekleme (ölçümler bununla alındı: RTF 0.516, WER 0.028).
GEN = {
    "temperature": float(os.environ.get("HIGGS_TEMPERATURE", "0.8")),
    "top_k": int(os.environ.get("HIGGS_TOP_K", "50")),
    "max_new_tokens": int(os.environ.get("HIGGS_MAX_NEW_TOKENS", "2048")),
}
# Tek üretimde gidilecek en uzun metin. livekit zaten CÜMLE başına istek atıyor,
# bu yalnız patolojik uzun girdiye karşı emniyet supabı (2048 kare ≈ 82 sn ses).
MAX_CHARS = int(os.environ.get("HIGGS_MAX_CHARS", "400"))
# Isıtma cümlesi: ilk çağrı CUDA çekirdek derlemesi içeriyor (ölçüm: ~+3 sn).
# Süreç açılışında bir kez yapılır ki İLK GERÇEK istek yavaş olmasın.
WARMUP_TEXT = os.environ.get("HIGGS_WARMUP_TEXT", "Merhaba, hazırım.")

ENGINE_ID = "higgs-tts-3-4b"          # cache anahtarına giren motor kimliği
FALLBACK_SAMPLE_RATE = 24000

# ── Durum ─────────────────────────────────────────────────────────────────────
_SYNTH_LOCK = threading.Lock()   # tek GPU, tek model → istekler serileştirilir
_STATE: dict = {
    "ready": False,
    "error": None,
    "sample_rate": FALLBACK_SAMPLE_RATE,
    "ref_fingerprint": None,
    "ref_frames": None,
    "load_s": None,
    "warmup_s": None,
    "started_at": time.time(),
    "synth_count": 0,
    "fail_count": 0,
    "last_synth": None,
}
_MODEL = None
_TOK = None
_CODES = None


# ── Model + referans yüklemesi (SÜREÇ BAŞINDA BİR KEZ) ───────────────────────
def _read_wav_f32(path: Path):
    """Referans wav'ı float32 mono olarak oku. (numpy dizisi, sample_rate)."""
    import numpy as np

    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        width = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise RuntimeError(f"referans wav 16-bit değil (sampwidth={width})")
    data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    return data, sr


def _fingerprint_codes(codes) -> str:
    """Referans KODLARININ parmak izi — sesin gerçek kimliği.

    Dosya YOLUNU değil içeriği özetler: aynı yola başka bir wav yazılsa bile
    parmak izi değişir, worker cache'i kendiliğinden geçersizleşir.
    (OmniVoice köprüsünün `/api/default`'ı yalnız yol+metin döndürüyordu; bu
    onun bilinen zayıflığını kapatıyor.)
    """
    import numpy as np

    arr = np.ascontiguousarray(codes.detach().to("cpu").numpy())
    h = hashlib.sha256(arr.tobytes())
    h.update(REF_TEXT.encode("utf-8"))
    h.update(MODEL_ID.encode("utf-8"))
    return h.hexdigest()[:16]


def _load() -> None:
    """Modeli, kodeği ve referans kodlarını yükle; ısıtma sentezi yap."""
    global _MODEL, _TOK, _CODES
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    t0 = time.perf_counter()
    log.info("model yükleniyor: %s", MODEL_ID)
    # trust_remote_code tokenizer'da da ŞART: yoksa etkileşimli onay sorar ve
    # tty'siz systemd altında süreç orada takılır.
    tok = AutoTokenizer.from_pretrained(MODEL_ID, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        trust_remote_code=True,
        dtype=torch.bfloat16,
        low_cpu_mem_usage=True,
        device_map={"": 0},
    ).eval()
    model.get_audio_codec()   # kodek tembel yükleniyor → şimdi zorla
    torch.cuda.synchronize()
    load_s = time.perf_counter() - t0
    log.info("model yüklendi (%.1f s) · torch ayrılan %d MiB",
             load_s, round(torch.cuda.memory_allocated() / 2**20))

    # ── Referans: varsa diskten oku, yoksa BİR KEZ kodla ve yaz ──────────────
    if REF_CODES.exists():
        codes = torch.load(REF_CODES, map_location="cpu")
        log.info("referans kodları diskten: %s (%d kare)", REF_CODES, codes.shape[0])
    else:
        audio, sr = _read_wav_f32(REF_AUDIO)
        t1 = time.perf_counter()
        codes = model._encode_reference(torch.from_numpy(audio), sr).cpu()
        REF_CODES.parent.mkdir(parents=True, exist_ok=True)
        torch.save(codes, REF_CODES)
        log.info("referans kodlandı: %s → %d kare (%.2f s), yazıldı %s",
                 REF_AUDIO, codes.shape[0], time.perf_counter() - t1, REF_CODES)

    _MODEL, _TOK, _CODES = model, tok, codes
    _STATE["sample_rate"] = int(getattr(model.config, "sample_rate", FALLBACK_SAMPLE_RATE))
    _STATE["ref_fingerprint"] = _fingerprint_codes(codes)
    _STATE["ref_frames"] = int(codes.shape[0])
    _STATE["load_s"] = round(load_s, 2)

    t2 = time.perf_counter()
    pcm = _synthesize(WARMUP_TEXT)
    _STATE["warmup_s"] = round(time.perf_counter() - t2, 2)
    _STATE["synth_count"] = 0          # ısıtma sayaca girmesin
    log.info("ısıtma tamam (%.2f s, %d bayt) · referans %s",
             _STATE["warmup_s"], len(pcm), _STATE["ref_fingerprint"])
    _STATE["ready"] = True
    log.info("HAZIR — http://%s:%d/health", HOST, PORT)


# ── Sentez ────────────────────────────────────────────────────────────────────
_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")


def _chunks(text: str) -> list[str]:
    """Patolojik uzun metni cümle sınırından böl (emniyet supabı, normalde tek parça)."""
    if len(text) <= MAX_CHARS:
        return [text]
    out: list[str] = []
    cur = ""
    for piece in _SPLIT_RE.split(text):
        if cur and len(cur) + 1 + len(piece) > MAX_CHARS:
            out.append(cur)
            cur = piece
        else:
            cur = f"{cur} {piece}".strip()
    if cur:
        out.append(cur)
    # Tek cümle bile sığmıyorsa sert kes (üretim yine de max_new_tokens ile sınırlı).
    final: list[str] = []
    for c in out:
        while len(c) > MAX_CHARS * 2:
            final.append(c[: MAX_CHARS * 2])
            c = c[MAX_CHARS * 2:]
        final.append(c)
    return [c for c in final if c.strip()]


def _synthesize(text: str) -> bytes:
    """Metni s16le PCM'e çevir. GPU işi — `_SYNTH_LOCK` ile serileştirilmiş olmalı.

    Boş sonuç DÖNEBİLİR (b""); çağıran bunu HATA olarak ele alır, 200 ile dönmez.
    """
    import numpy as np
    import torch

    parts: list[bytes] = []
    for chunk in _chunks(text):
        with torch.inference_mode():
            wav = _MODEL.generate_speech(
                chunk, _TOK,
                reference_codes=_CODES, reference_text=REF_TEXT, **GEN,
            )
        arr = wav.detach().to("cpu").to(torch.float32).numpy().reshape(-1)
        if arr.size == 0:
            continue
        pcm16 = np.clip(arr, -1.0, 1.0) * 32767.0
        parts.append(pcm16.astype("<i2").tobytes())
    return b"".join(parts)


def _wav_bytes(pcm: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


# ── HTTP ──────────────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "higgs-tts/1.0"

    # Erişim logu journald'ı doldurmasın; anlamlı olayları kendimiz logluyoruz.
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        log.debug("%s - %s", self.address_string(), fmt % args)

    # ── yardımcılar ──────────────────────────────────────────────────────────
    def _send(self, code: int, body: bytes, ctype: str, extra: Optional[dict] = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, str(v))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            log.warning("istemci bağlantıyı kapattı (yanıt yazılamadı)")

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body_params(self) -> dict:
        """Gövdeyi çöz: JSON veya application/x-www-form-urlencoded."""
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        ctype = (self.headers.get("Content-Type") or "").lower()
        if "json" in ctype:
            return json.loads(raw.decode("utf-8") or "{}")
        return {k: v[0] for k, v in parse_qs(raw.decode("utf-8"), keep_blank_values=True).items()}

    # ── uçlar ────────────────────────────────────────────────────────────────
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/health":
            payload = {
                "engine": ENGINE_ID,
                "model": MODEL_ID,
                "ready": bool(_STATE["ready"]),
                "error": _STATE["error"],
                "sample_rate": _STATE["sample_rate"],
                "ref_fingerprint": _STATE["ref_fingerprint"],
                "load_s": _STATE["load_s"],
                "warmup_s": _STATE["warmup_s"],
                "uptime_s": round(time.time() - _STATE["started_at"], 1),
                "synth_count": _STATE["synth_count"],
                "fail_count": _STATE["fail_count"],
                "last_synth": _STATE["last_synth"],
            }
            self._json(200 if _STATE["ready"] else 503, payload)
            return
        if path == "/api/default":
            if not _STATE["ready"]:
                self._json(503, {"error": "model henüz hazır değil"})
                return
            # Worker cache anahtarını BURADAN türetiyor. `engine` + `ref_fingerprint`
            # birlikte "hangi motorun hangi sesi" sorusunu tekilleştirir — OmniVoice
            # döneminden kalan cache dosyaları bu anahtarla ASLA eşleşmez.
            self._json(200, {
                "engine": ENGINE_ID,
                "model": MODEL_ID,
                "ref_audio": str(REF_AUDIO),
                "ref_text": REF_TEXT,
                "ref_fingerprint": _STATE["ref_fingerprint"],
                "ref_frames": _STATE["ref_frames"],
                "sample_rate": _STATE["sample_rate"],
            })
            return
        self._json(404, {"error": f"bilinmeyen uç: {path}"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/api/tts":
            self._json(404, {"error": f"bilinmeyen uç: {path}"})
            return

        try:
            params = self._body_params()
        except Exception as exc:  # noqa: BLE001
            self._json(400, {"error": f"gövde çözülemedi: {exc}"})
            return

        text = (params.get("text") or "").strip()
        if not text:
            self._json(400, {"error": "text boş"})
            return
        if not _STATE["ready"]:
            self._json(503, {"error": "model henüz hazır değil", "detail": _STATE["error"]})
            return

        mood = params.get("mood") or None
        fmt = (params.get("format") or "wav").lower()
        t0 = time.perf_counter()
        try:
            with _SYNTH_LOCK:
                pcm = _synthesize(text)
        except Exception as exc:  # noqa: BLE001
            _STATE["fail_count"] += 1
            log.exception("sentez hatası: %.60r", text)
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
            return

        if not pcm:
            # SESSİZ BAŞARI YOK: boş ses 200 ile dönerse sorun görünmez olur.
            _STATE["fail_count"] += 1
            log.warning("model ses üretmedi: %.60r", text)
            self._json(502, {"error": "model ses üretmedi (0 frame)"})
            return

        sr = _STATE["sample_rate"]
        wall = time.perf_counter() - t0
        audio_s = len(pcm) / (2 * sr)
        _STATE["synth_count"] += 1
        _STATE["last_synth"] = {
            "chars": len(text), "audio_s": round(audio_s, 2),
            "wall_s": round(wall, 2), "rtf": round(wall / audio_s, 3) if audio_s else None,
            "mood": mood, "at": round(time.time(), 1),
        }
        log.info("sentez: %d karakter → %.2f s ses / %.2f s (RTF %.3f) mood=%s",
                 len(text), audio_s, wall, wall / audio_s if audio_s else 0.0, mood)

        headers = {
            "X-Higgs-Rtf": f"{wall / audio_s:.3f}" if audio_s else "0",
            "X-Higgs-Audio-Seconds": f"{audio_s:.3f}",
            "X-Higgs-Sample-Rate": str(sr),
            "X-Higgs-Mood": mood or "",     # bu turda ses üzerinde ETKİSİZ (bkz. modül başı)
            "X-Higgs-Ref": _STATE["ref_fingerprint"] or "",
        }
        if fmt == "pcm":
            self._send(200, pcm, "audio/pcm", headers)
        else:
            self._send(200, _wav_bytes(pcm, sr), "audio/wav", headers)


def main() -> int:
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    httpd.daemon_threads = True
    stopping = threading.Event()

    def _stop(signum, _frame):
        log.info("sinyal %s → kapanıyor", signum)
        stopping.set()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    # HTTP ÖNCE açılır: yükleme 3-40 sn sürebiliyor, bu sürede /health 503 döner
    # ("ready": false). Sessiz port yerine konuşan port — teşhis edilebilir olsun.
    threading.Thread(target=httpd.serve_forever, name="http", daemon=True).start()
    log.info("HTTP açık: %s:%d (model yükleniyor)", HOST, PORT)

    try:
        _load()
    except Exception as exc:  # noqa: BLE001
        _STATE["error"] = f"{type(exc).__name__}: {exc}"
        log.exception("MODEL YÜKLENEMEDİ — servis 503 dönecek")
        # Süreci hemen öldürmüyoruz: systemd Restart=on-failure sonsuz döngüye
        # girip GPU'yu tekrar tekrar yormasın; /health nedeni söylesin.
        # 30 sn sonra yine de çıkıyoruz ki restart politikası bir şans daha versin.
        stopping.wait(30)
        return 1

    stopping.wait()      # SIGTERM'e ANINDA cevap ver (systemd stop beklemesin)
    return 0


if __name__ == "__main__":
    sys.exit(main())
