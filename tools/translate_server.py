#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TR→EN çeviri servisi — ROUTER İÇİN, .25'te koşar (systemd: candan-translate.service).

NEDEN VAR
─────────
Router (Qwen3.5-4B) Türkçe cümlede "semantik komşu" tuzaklarına düşüyor: katalogda
OLMAYAN bir cihaz istendiğinde ("kombiyi aç", "perdeleri kapat") en yakın tool'u
(light_control) çağırıyor. Aynı cümlenin İngilizcesinde bu hata belirgin biçimde
azalıyor (ölçüm: docs/ROUTER-EXPERIMENTS.md). Ama çeviri ÖZEL İSİMLERİ bozuyor
("Kuzu Kuzu" → "Lamb Lamb", "Müslüm Gürses" → "the Musicians"), o yüzden router'a
İKİ metin birlikte verilir: tool seçimi İngilizceden, argümanlar TÜRKÇE orijinalden
(worker/tool_catalog.py → build_prompt(text, text_en=...)).

NEDEN SUNUCUDA, WORKER'DA DEĞİL
────────────────────────────────
Worker'a yeni ağır bağımlılık (torch/transformers) İSTEMİYORUZ. ctranslate2 hafif
ama 242 MB'lık model dizinini Mac'e ayrıca kurmak/güncellemek gerekirdi (repo'ya
girmez) ve çeviri Mac CPU'sunu sesli akışla yarıştırırdı. Model zaten .25'te;
oraya küçük bir HTTP servisi koymak worker'ı BAĞIMLILIKSIZ bırakıyor.
Ölçüm: Mac CPU (ct2 int8) p50 61 ms — .25 CPU + LAN ile aynı büyüklük sınıfında,
ama bağımlılık maliyeti yok.

MOTOR: CTranslate2 (int8, CPU) — GPU'ya DOKUNMAZ. VRAM zaten dolu
(llama-server 5.5G + TTS 4.7G + Whisper 2.4G); çeviri için GPU almaya değmez.

KURULUM (.25):
    pip install --break-system-packages ctranslate2 sentencepiece
    ct2-transformers-converter --model Helsinki-NLP/opus-mt-tc-big-tr-en \
        --output_dir /opt/models/ct2-opusmt-tr-en --quantization int8
    cp <hf-snapshot>/{source.spm,target.spm} /opt/models/ct2-opusmt-tr-en/
    systemctl enable --now candan-translate     # bkz. docs/ROUTER-EXPERIMENTS.md

API:
    POST /translate  {"text": "kombiyi aç"}  →  {"text_en": "Turn on the boiler"}
    GET  /health                             →  {"ok": true}

KIRMIZI ÇİZGİ: bu servis DÜŞERSE router Türkçe-doğrudan moduna düşer (bugünkü
davranış). worker/translate.py bunu garanti eder — burada kahramanlık yapma.
"""
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import ctranslate2
import sentencepiece as spm

MODEL_DIR = os.environ.get("CT2_MODEL_DIR", "/opt/models/ct2-opusmt-tr-en")
PORT = int(os.environ.get("PORT", "8081"))

_tr = ctranslate2.Translator(MODEL_DIR, device="cpu", inter_threads=2, intra_threads=4,
                             compute_type="int8")
_src = spm.SentencePieceProcessor(os.path.join(MODEL_DIR, "source.spm"))
_tgt = spm.SentencePieceProcessor(os.path.join(MODEL_DIR, "target.spm"))


def translate(text: str) -> str:
    # Marian: kaynak dizisi </s> ile BİTMELİ — yoksa model durmaz, kendini tekrarlar.
    toks = [*_src.encode(text, out_type=str), "</s>"]
    res = _tr.translate_batch([toks], beam_size=1, max_decoding_length=96)
    return _tgt.decode(res[0].hypotheses[0]).strip()


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, obj: dict) -> None:
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/health"):
            self._send(200, {"ok": True, "model": MODEL_DIR})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if not self.path.startswith("/translate"):
            self._send(404, {"error": "not found"})
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            text = (json.loads(self.rfile.read(n) or b"{}").get("text") or "").strip()
        except Exception as e:  # noqa: BLE001
            self._send(400, {"error": repr(e)[:120]})
            return
        if not text:
            self._send(200, {"text_en": ""})
            return
        t0 = time.perf_counter()
        try:
            en = translate(text)
        except Exception as e:  # noqa: BLE001 — çeviri patlarsa router TR'ye düşsün
            self._send(500, {"error": repr(e)[:120]})
            return
        self._send(200, {"text_en": en, "ms": round((time.perf_counter() - t0) * 1000, 1)})

    def log_message(self, *a):  # journald'ı doldurma
        pass


if __name__ == "__main__":
    print("translate servisi :%d  model=%s" % (PORT, MODEL_DIR), flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
