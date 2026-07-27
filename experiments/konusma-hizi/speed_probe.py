"""Konuşma hızı — iki yolu AYNI ölçekte ölç: Higgs token'ı vs. WSOLA tempo.

SORU (canlı şikâyet, 27 Tem 18:21): kullanıcı "biraz daha hızlı konuş" dedi,
Candan "hızlandırıyorum" dedi, **tempo değişmedi**. Hangi yol gerçekten değiştirir?

  (a) `<|prosody:speed_fast|>` / `speed_very_fast` / `speed_slow` — bedava.
      27 Tem duygu ölçümünde Δsüre |≤0.2 s| çıkmıştı ama o ölçüm TEK KISA cümledeydi
      ve Δsüre ölçüyordu. Burada canlı cevap uzunluğunda (2-3 cümle) metinlerde,
      **kelime/saniye** cinsinden yeniden ölçülüyor. Asıl soru "duyulur hızlandı mı".
  (b) `worker/tempo.py` (WSOLA) — üretilen PCM'in temposu, PERDE KORUNARAK.
      Ek sentez YOK: tabanın wav'ları dönüştürülür, yani (a) ile BİREBİR aynı ses
      üzerinde karşılaştırılır. Fark yalnız yoldan gelir.

Motorun kendi `speed` parametresi ADAY DEĞİL: `server/higgs-tts/server.py`
sözleşmesinde yazıyor ama kod onu hiç okumuyor (`params.get("speed")` YOK) —
gövdeye `speed` koymak sessizce yok sayılır. Doğrulaması `--speed-param-testi`.

Ölçüm CANLI yoldan: `POST /api/tts/stream`, referans klonu, aynı blok/lookahead.
Servise, streaming yapısına DOKUNULMAZ — yalnız HTTP isteği atılır.
İlk ses gecikmesi de ölçülür (ilk chunk'a kadar geçen süre): 0.55 s bozulmamalı.

    python3 speed_probe.py                 # tam koşum
    python3 speed_probe.py --n 2           # hızlı deneme
    python3 speed_probe.py --speed-param-testi   # motor speed kabul ediyor mu

Çıktı: out/wav/<kosul>/<metin>-NN.wav + out/speed_probe.json
Anlaşılırlık ikinci adımda: `speed_eval.py` (Whisper geri-dönüşü).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "higgs-tts3"))
sys.path.insert(0, str(HERE.parent.parent / "worker"))

from token_probe import _wav  # noqa: E402  — wav sarmalayıcıyı yeniden yazma

import tempo  # noqa: E402  — canlıya girecek olan FİLTRENİN TA KENDİSİ

OUT = HERE / "out"
WAV = OUT / "wav"

HOST = "192.168.0.25"
PORT = 8809
TIMEOUT_S = 180.0

# Canlı cevap uzunluğu: 2-3 cümle. Tek kısa cümlede hız farkı ölçüm gürültüsünde
# kayboluyordu — şikâyet de zaten uzun cevaplarda çıktı.
METINLER: dict[str, str] = {
    "M1": ("Yarın hava on sekiz derece ve öğleden sonra yağmur bekleniyor. "
           "Şemsiyeni almayı unutma. Toplantın da saat üçte başlıyor."),
    "M2": ("Alışveriş listene süt, ekmek ve yumurta ekledim. "
           "Peynir zaten listede vardı. Başka bir şey eklemek istersen söyle."),
    "M3": ("Bu hafta üç gün spora gitmişsin, geçen haftaya göre bir gün fazla. "
           "Gayet iyi gidiyorsun. Yarın için de bir hatırlatma kurdum."),
}

# (a) token yolu — cümle BAŞINA, bitişik (ölçülmüş yerleşim kuralı).
TOKENLAR = ("speed_slow", "speed_fast", "speed_very_fast")
# ⚠️ `speed_very_slow` ADAY DEĞİL: 27 Tem ölçümünde 24'te 1 kez cümlenin önüne
# uydurma konuşma ekledi ve ilk heceyi kırptı (WER 0.075). Şüpheli token girmez.

# (b) WSOLA oranları. Uçlar bilerek geniş: anlaşılırlığın nerede bozulduğunu
# ölçmeden kademe aralığı seçilemez.
ORANLAR = (0.85, 1.15, 1.30, 1.45)


def _slug(x: str) -> str:
    return str(x).replace(":", "_").replace(".", "")


def synth(text: str) -> tuple[bytes, int, float, float]:
    """Canlı streaming ucundan PCM → (pcm, sample_rate, ilk_ses_s, toplam_s).

    `token_probe.synth`'ten farkı: İLK CHUNK'a kadar geçen süreyi de ölçer.
    İlk ses gecikmesi bu işin kabul ölçütlerinden biri (bugün 0.55 s).
    """
    body = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        f"http://{HOST}:{PORT}/api/tts/stream", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.perf_counter()
    parts: list[bytes] = []
    first = None
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        sr = int(resp.headers.get("X-Higgs-Sample-Rate") or 24000)
        while True:
            chunk = resp.read1(1 << 16) if hasattr(resp, "read1") else resp.read(1 << 16)
            if not chunk:
                break
            if first is None:
                first = time.perf_counter() - t0
            parts.append(chunk)
    return b"".join(parts), sr, first or 0.0, time.perf_counter() - t0


def speed_param_testi() -> None:
    """Motor `speed` parametresini kabul ediyor mu? (Kod okuması + canlı doğrulama.)

    Aynı metni `speed` YOK / 0.7 / 1.4 ile üç kez ister. Süreler AYNI çıkarsa
    parametre sessizce yok sayılıyordur — `server.py` kodu da öyle diyor.
    """
    metin = METINLER["M1"]
    for etiket, ek in (("yok", {}), ("0.7", {"speed": 0.7}), ("1.4", {"speed": 1.4})):
        body = json.dumps({"text": metin, **ek}).encode("utf-8")
        req = urllib.request.Request(
            f"http://{HOST}:{PORT}/api/tts/stream", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            sr = int(resp.headers.get("X-Higgs-Sample-Rate") or 24000)
            pcm = resp.read()
        print(f"  speed={etiket:4s} → {len(pcm) / 2 / sr:.2f} s ses")
    print("  (süreler birbirine yakınsa parametre YOK SAYILIYOR demektir)")


# Gecikme ölçümünün cümlesi. livekit TTS'e CÜMLE CÜMLE gider (`streaming=False`),
# yani canlıdaki ilk ses gecikmesi tek cümle üzerinden ölçülür — yukarıdaki 3
# cümlelik metinler HIZ için doğru, GECİKME için yanıltıcıdır.
GECIKME_CUMLESI = "Yarın hava on sekiz derece ve öğleden sonra yağmur bekleniyor."


def gecikme_testi(n: int, oran: float) -> None:
    """İlk ses gecikmesi: filtre VARKEN ve YOKKEN, canlı akış üzerinde.

    Kritik soru: WSOLA ilk sesi geciktiriyor mu? Filtre ilk çıktısı için
    `delta + N + Hs` ≈ 55 ms girdi ister; sunucudan gelen İLK BLOK 320 ms.
    Beklenti: ek bekleme YOK, yalnız filtrenin CPU'su (birkaç ms). Ölçelim.
    """
    body = json.dumps({"text": GECIKME_CUMLESI}).encode("utf-8")
    ham, filtreli = [], []
    for _ in range(n):
        req = urllib.request.Request(
            f"http://{HOST}:{PORT}/api/tts/stream", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        ts = tempo.TempoStream(oran)
        t0 = time.perf_counter()
        ilk_ham = ilk_filtreli = None
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            while True:
                chunk = resp.read1(1 << 16)
                if not chunk:
                    break
                if ilk_ham is None:
                    ilk_ham = time.perf_counter() - t0
                out = ts.feed(chunk)
                if out and ilk_filtreli is None:
                    ilk_filtreli = time.perf_counter() - t0
        ham.append(ilk_ham or 0.0)
        filtreli.append(ilk_filtreli or 0.0)
    med = lambda v: sorted(v)[len(v) // 2]  # noqa: E731
    print(f"  ilk ses, filtresiz : {med(ham) * 1000:.0f} ms  (n={n}, medyan)")
    print(f"  ilk ses, tempo={oran}: {med(filtreli) * 1000:.0f} ms")
    print(f"  fark               : {(med(filtreli) - med(ham)) * 1000:+.0f} ms")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=4, help="metin başına örnek (3 metin × n)")
    ap.add_argument("--speed-param-testi", action="store_true")
    ap.add_argument("--gecikme", action="store_true", help="ilk ses gecikmesi ölçümü")
    ap.add_argument("--oran", type=float, default=1.30)
    args = ap.parse_args()

    if args.speed_param_testi:
        print("motor `speed` parametresi testi:")
        speed_param_testi()
        return
    if args.gecikme:
        print(f"ilk ses gecikmesi (tek cümle, canlı akış):")
        gecikme_testi(max(args.n, 5), args.oran)
        return

    WAV.mkdir(parents=True, exist_ok=True)
    path = OUT / "speed_probe.json"
    rapor: dict = {"host": f"{HOST}:{PORT}", "n": args.n, "metinler": METINLER,
                   "kosullar": {}}
    if path.exists():
        try:
            rapor["kosullar"] = json.loads(path.read_text(encoding="utf-8"))["kosullar"]
        except Exception:  # noqa: BLE001 — bozuk rapor koşumu durdurmasın
            pass

    def kaydet() -> None:
        path.write_text(json.dumps(rapor, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── (a) taban + token koşulları: CANLI sentez ────────────────────────────
    canli = [("taban", "")] + [(f"prosody:{t}", f"<|prosody:{t}|>") for t in TOKENLAR]
    for ad, onek in canli:
        d = WAV / _slug(ad)
        d.mkdir(parents=True, exist_ok=True)
        items: list[dict] = []
        for mad, metin in METINLER.items():
            for i in range(args.n):
                hedef = d / f"{mad}-{i:02d}.wav"
                if hedef.exists():          # yarım koşum kaldığı yerden devam etsin
                    raw = hedef.read_bytes()
                    items.append({"metin": mad, "i": i, "wav": str(hedef.relative_to(HERE)),
                                  "sure_s": round((len(raw) - 44) / 2 / 24000, 3),
                                  "ilk_ses_s": None})
                    continue
                pcm, sr, ilk, _ = synth(onek + metin)
                hedef.write_bytes(_wav(pcm, sr))
                items.append({"metin": mad, "i": i, "wav": str(hedef.relative_to(HERE)),
                              "sure_s": round(len(pcm) / 2 / sr, 3),
                              "ilk_ses_s": round(ilk, 3)})
        rapor["kosullar"][ad] = {"yol": "token", "gonderilen_onek": onek, "items": items}
        _ozet(ad, items)
        kaydet()

    # ── (b) WSOLA: TABANIN wav'ları dönüştürülür (ek sentez YOK) ─────────────
    # Aynı ses üzerinde karşılaştırma: iki yol arasındaki fark yalnız YOLDAN gelsin.
    taban = rapor["kosullar"]["taban"]["items"]
    for oran in ORANLAR:
        ad = f"tempo:{oran}"
        d = WAV / _slug(ad)
        d.mkdir(parents=True, exist_ok=True)
        items = []
        for it in taban:
            kaynak = (HERE / it["wav"]).read_bytes()
            sr = int.from_bytes(kaynak[24:28], "little")
            t0 = time.perf_counter()
            out = tempo.change(kaynak[44:], oran, sr)
            cpu = time.perf_counter() - t0
            hedef = d / Path(it["wav"]).name
            hedef.write_bytes(_wav(out, sr))
            items.append({"metin": it["metin"], "i": it["i"],
                          "wav": str(hedef.relative_to(HERE)),
                          "sure_s": round(len(out) / 2 / sr, 3),
                          # Gecikme katkısı: filtrenin KENDİ işlem süresi. Blok
                          # beklemesi YOK (bkz. tempo.py — 55 ms 320 ms bloğun içinde).
                          "filtre_cpu_s": round(cpu, 4),
                          "ilk_ses_s": it.get("ilk_ses_s")})
        rapor["kosullar"][ad] = {"yol": "wsola", "oran": oran, "items": items}
        _ozet(ad, items)
        kaydet()

    print(f"\nyazıldı: {path}", file=sys.stderr)


def _ozet(ad: str, items: list[dict]) -> None:
    durs = [it["sure_s"] for it in items if it.get("sure_s")]
    ilk = [it["ilk_ses_s"] for it in items if it.get("ilk_ses_s")]
    print(f"{ad:22s} n={len(items):3d}  ort süre {sum(durs) / len(durs):.2f}s"
          + (f"  ilk ses {sum(ilk) / len(ilk):.3f}s" if ilk else ""), flush=True)


if __name__ == "__main__":
    main()
