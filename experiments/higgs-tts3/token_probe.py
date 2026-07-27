"""Higgs kontrol token'larını TEK TEK ölç — hangisi temiz, hangisi bozuk.

NEDEN: `<|emotion:elation|>` geçerli bir katalog etiketi ve tokenizer onu tanıyor,
YİNE DE bozuk (cümle başını yiyor, örneklerin dörtte biri tamamen boş). Yani
**tokenizer'ın tanıması çalıştığı anlamına gelmiyor.** Katalogdaki 43 etiketin
hiçbiri ölçülmeden `HIGGS_TAG_MAP`'e girmeyecek.

YÖNTEM: CANLI `higgs-tts.service`'in **streaming** ucuna (`POST /api/tts/stream`)
token başına N örnek gönderilir, ham PCM wav'a sarılır. Ölçüm canlı yolun ta
kendisinden geçer — ayrı bir model yüklenmez (VRAM'de yer yok: brain 9.5 GB +
whisper 2.4 GB + higgs 10 GB).

İKİ CÜMLE, İKİ YERLEŞİM SINIFI:
  • S1 (cümle başı): emotion / style / prosody-speed-pitch-expressive token'ları
    metnin BAŞINA konur. Taban = etiketsiz aynı cümle.
  • S2 (satır içi):  prosody:pause / long_pause cümlenin İÇİNE konur. Burada
    ek soru var — etiket gerçekten SESSİZLİK ekliyor mu? Taban süresiyle
    karşılaştırılır (`sure_delta_s`).

Boşluk varyantı bilerek ölçülüyor: `sfx` için resmi kural "etiketten sonra BOŞLUK
YOK". `pause` için kural yazılı değil, o yüzden boşluklu ve boşluksuz iki varyant
da koşuluyor.

    python3 token_probe.py                    # tam koşum (out/tokens/)
    python3 token_probe.py --n 4 --only baseline elation   # hızlı deneme

Çıktı: out/tokens/<kosul>/NN.wav + out/token_probe.json (ham ölçüm, ASR'siz).
Anlaşılırlık ikinci adımda: `token_eval.py` (Whisper geri-dönüşü).
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out" / "tokens"

HOST = "192.168.0.25"
PORT = 8809
TIMEOUT_S = 120.0

# Aynı cümle her koşulda — fark yalnız token'dan gelsin. S1 bilerek 2026-07-27
# elation ölçümündeki cümlenin AYNISI: yeni sayılar eski tabloyla kıyaslanabilsin.
S1 = "Bugün harika bir haber var."
# S2 satır içi token'lar için: içinde doğal bir duraklama yeri olan cümle.
S2_HEAD = "Bir saniye"
S2_TAIL = "düşüneyim, sanırım yarın uygun olur."
S2 = f"{S2_HEAD} {S2_TAIL}"
# S3, S2'de çıkan bir soruyu ayırmak için: `pause` cümlenin İLK kelimesini
# yiyordu (24'te 2). Etiket 2. kelimeden hemen sonraydı — suç etikette mi, yoksa
# "Bir" kısa kelimesinin cümle başında olmasında mı? S3'te etiket cümlenin
# ORTASINDA, başlangıçtan uzakta. Baş yeme burada da olursa suç etiketin.
S3_HEAD = "Yarın akşam sinemaya gidelim,"
S3_TAIL = "film sekizde başlıyor."
S3 = f"{S3_HEAD} {S3_TAIL}"

# ── Katalog (resmi PROMPTING.md, 43 etiket) ──────────────────────────────────
EMOTIONS = (
    "affection", "amusement", "anger", "arousal", "awe", "bitterness",
    "confusion", "contemplation", "contentment", "determination", "disgust",
    "elation", "enthusiasm", "fear", "helplessness", "longing", "pride",
    "relief", "sadness", "shame", "surprise",
)
PROSODY_PREFIX = (
    "speed_very_slow", "speed_slow", "speed_fast", "speed_very_fast",
    "pitch_low", "pitch_high", "expressive_high", "expressive_low",
)
PROSODY_INLINE = ("pause", "long_pause")
STYLES = ("singing", "shouting", "whispering")
SFX = ("laughter", "sigh")   # canlıda ZATEN kullanılıyor — taban doğrulaması


def conditions() -> list[dict]:
    """(ad, gönderilecek metin, beklenen sözlü metin, sınıf) listesi."""
    rows: list[dict] = [
        {"ad": "baseline", "metin": S1, "beklenen": S1, "sinif": "taban", "cumle": "S1"},
    ]
    for name in EMOTIONS:
        rows.append({"ad": f"emotion:{name}", "metin": f"<|emotion:{name}|>{S1}",
                     "beklenen": S1, "sinif": "emotion", "cumle": "S1"})
    for name in PROSODY_PREFIX:
        rows.append({"ad": f"prosody:{name}", "metin": f"<|prosody:{name}|>{S1}",
                     "beklenen": S1, "sinif": "prosody-bas", "cumle": "S1"})
    for name in STYLES:
        rows.append({"ad": f"style:{name}", "metin": f"<|style:{name}|>{S1}",
                     "beklenen": S1, "sinif": "style", "cumle": "S1"})
    # sfx: canlıda kullanılan iki etiket. Taklit etikete BİTİŞİK (resmi kural).
    # `onek_serbest`: Whisper kahkahayı/iç çekişi "Haha"/"Hah"/"Aaa" diye ya da HİÇ
    # yazmıyor — bu ölçüm ARTEFAKTI, model kelime düşürmüş değil. Değerlendirme
    # cümlenin İLK KELİMESİNDEN başlar, öncesindeki taklit serbesttir.
    rows.append({"ad": "sfx:laughter", "metin": f"<|sfx:laughter|>Haha, {S1}",
                 "beklenen": S1, "onek_serbest": True, "sinif": "sfx", "cumle": "S1"})
    rows.append({"ad": "sfx:sigh", "metin": f"<|sfx:sigh|>Haah, {S1}",
                 "beklenen": S1, "onek_serbest": True, "sinif": "sfx", "cumle": "S1"})

    # Satır içi: taban + boşluklu/boşluksuz varyantlar.
    rows.append({"ad": "s2-baseline", "metin": S2, "beklenen": S2,
                 "sinif": "taban", "cumle": "S2"})
    for name in PROSODY_INLINE:
        rows.append({
            "ad": f"prosody:{name}",
            "metin": f"{S2_HEAD} <|prosody:{name}|> {S2_TAIL}",
            "beklenen": S2, "sinif": "prosody-ici", "cumle": "S2"})
        rows.append({
            "ad": f"prosody:{name}-bitisik",
            "metin": f"{S2_HEAD}<|prosody:{name}|>{S2_TAIL}",
            "beklenen": S2, "sinif": "prosody-ici", "cumle": "S2"})

    # S3: etiket cümlenin ORTASINDA (bkz. S3 yorumu).
    rows.append({"ad": "s3-baseline", "metin": S3, "beklenen": S3,
                 "sinif": "taban", "cumle": "S3"})
    for name in PROSODY_INLINE:
        rows.append({
            "ad": f"s3-prosody:{name}-bitisik",
            "metin": f"{S3_HEAD}<|prosody:{name}|>{S3_TAIL}",
            "beklenen": S3, "sinif": "prosody-ici", "cumle": "S3"})
    return rows


def _slug(name: str) -> str:
    return name.replace(":", "_")


def _wav(pcm: bytes, sample_rate: int, channels: int = 1) -> bytes:
    """s16le PCM → WAV. (Whisper dosya bekliyor, sunucu ham PCM akıtıyor.)"""
    byte_rate = sample_rate * channels * 2
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate,
                                    byte_rate, channels * 2, 16)
    header += b"data" + struct.pack("<I", len(pcm))
    return header + pcm


def synth(text: str) -> tuple[bytes, int, float]:
    """CANLI streaming ucundan PCM çek → (pcm, sample_rate, duvar saati)."""
    body = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        f"http://{HOST}:{PORT}/api/tts/stream", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        sr = int(resp.headers.get("X-Higgs-Sample-Rate") or 24000)
        pcm = resp.read()
    return pcm, sr, time.perf_counter() - t0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=12, help="token başına örnek (spec: ≥12)")
    ap.add_argument("--only", nargs="*", default=None, help="yalnız bu koşullar")
    args = ap.parse_args()

    rows = conditions()
    if args.only:
        want = set(args.only)
        rows = [r for r in rows if r["ad"] in want or _slug(r["ad"]) in want]
        if not rows:
            raise SystemExit(f"eşleşen koşul yok: {args.only}")

    OUT.mkdir(parents=True, exist_ok=True)
    report = {"host": f"{HOST}:{PORT}", "n": args.n, "S1": S1, "S2": S2, "S3": S3,
              "kosullar": {}}
    path = HERE / "out" / "token_probe.json"
    if path.exists():
        try:
            report["kosullar"] = json.loads(path.read_text(encoding="utf-8")).get(
                "kosullar", {})
        except Exception:  # noqa: BLE001 — bozuk rapor koşumu durdurmasın
            pass

    for idx, row in enumerate(rows, 1):
        d = OUT / _slug(row["ad"])
        d.mkdir(parents=True, exist_ok=True)
        items, fails = [], 0
        for i in range(args.n):
            try:
                pcm, sr, wall = synth(row["metin"])
            except Exception as exc:  # noqa: BLE001 — HTTP hatası koşul bilgisidir
                fails += 1
                items.append({"i": i, "hata": f"{type(exc).__name__}: {exc}"})
                continue
            wav = d / f"{i:02d}.wav"
            wav.write_bytes(_wav(pcm, sr))
            items.append({"i": i, "wav": str(wav.relative_to(HERE)),
                          "bayt": len(pcm), "sure_s": round(len(pcm) / 2 / sr, 3),
                          "duvar_s": round(wall, 2)})
        durs = [it["sure_s"] for it in items if "sure_s" in it]
        report["kosullar"][row["ad"]] = {
            "metin": row["metin"], "beklenen": row["beklenen"],
            "sinif": row["sinif"], "cumle": row["cumle"], "n": args.n,
            "http_hata": fails, "bos": sum(1 for it in items if it.get("bayt") == 0),
            "ort_sure_s": round(sum(durs) / len(durs), 3) if durs else None,
            "min_sure_s": round(min(durs), 3) if durs else None,
            "items": items,
        }
        print(f"[{idx}/{len(rows)}] {row['ad']:34s} "
              f"ort {report['kosullar'][row['ad']]['ort_sure_s']}s "
              f"hata {fails}", flush=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    print(f"\nyazıldı: {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
