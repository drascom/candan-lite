"""ASR geri-dönüş testi — üretilen sesi Whisper'la yazıya çevirip beklenenle karşılaştır.

Kulakla değil ÖLÇEREK doğrulama. Her wav transkribe edilir, `expected_spoken` ile
kıyaslanır; WER + ATLANAN KELİME raporlanır ("başlıyor" tipi düşmeleri ikincisi yakalar).

    venvs/whisper/bin/python runners/asr_eval.py omnipick-trnorm omnipick-norm2

Çıktı: out/asr_eval.json + ekrana tablo.

TABAN GÜRÜLTÜSÜ: Whisper'ın kendisi de hata yapar. Onu ölçmek için `refs/omnipick.wav`
(metni kesin biliniyor) da transkribe edilip WER'i "taban" olarak raporlanır — set
WER'leri bunun ÜSTÜNDEKİ kısım kadar anlamlıdır.

Bağımlılık: mlx_whisper (venvs/whisper) + stdlib.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

import mlx_whisper

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from trnorm import normalize_tr  # noqa: E402
OUT = ROOT / "out"
MODEL = "mlx-community/whisper-large-v3-turbo"

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def norm_words(text: str) -> list[str]:
    """Karşılaştırma için sadeleştir.

    KRİTİK: Whisper konuşulan sayıları RAKAMA geri çeviriyor ("üç bin beş yüz lira" →
    "3500 lira", "yüzde yirmi beş" → "%25"). Ham karşılaştırma bu yüzden DOĞRU konuşmayı
    hatalı sayıyordu (WER 0.485 → gerçekte çok daha düşük). Bu yüzden hipotez de
    `normalize_tr`'den geçirilir; expected zaten sözlü formda olduğu için orada etkisiz.
    Kalan '.' gibi ayraçlar ("on dört.otuzda") noktalama temizliğinde düşer.
    """
    text = normalize_tr(text)
    # Python'un .lower()'ı Türkçe için yanlış: 'I'→'i' (olması gereken 'ı'), 'İ'→'i̇'
    text = text.replace("I", "ı").replace("İ", "i")
    text = _PUNCT.sub(" ", text.lower())
    return text.split()


def wer(ref: list[str], hyp: list[str]) -> tuple[float, int, int, int]:
    """Kelime bazlı Levenshtein → (WER, ikame, silme, ekleme). Harici kütüphane yok."""
    n, m = len(ref), len(hyp)
    if n == 0:
        return (0.0 if m == 0 else 1.0), 0, 0, m
    # d[i][j] = (maliyet, S, D, I)
    prev = [(j, 0, 0, j) for j in range(m + 1)]
    for i in range(1, n + 1):
        cur = [(i, 0, i, 0)] + [(0, 0, 0, 0)] * m
        for j in range(1, m + 1):
            if ref[i - 1] == hyp[j - 1]:
                cur[j] = prev[j - 1]
            else:
                sub = (prev[j - 1][0] + 1, prev[j - 1][1] + 1, prev[j - 1][2], prev[j - 1][3])
                dele = (prev[j][0] + 1, prev[j][1], prev[j][2] + 1, prev[j][3])
                ins = (cur[j - 1][0] + 1, cur[j - 1][1], cur[j - 1][2], cur[j - 1][3] + 1)
                cur[j] = min(sub, dele, ins, key=lambda t: t[0])
        prev = cur
    cost, s, d, i = prev[m]
    return cost / n, s, d, i


def missing_words(ref: list[str], hyp: list[str]) -> list[str]:
    """Beklenende olup transkriptte olmayan kelimeler (çokluk farkı)."""
    return sorted((Counter(ref) - Counter(hyp)).elements())


def transcribe(path: Path) -> str:
    r = mlx_whisper.transcribe(str(path), path_or_hf_repo=MODEL, language="tr")
    return r["text"].strip()


def main() -> None:
    sets = sys.argv[1:] or ["omnipick-trnorm", "omnipick-norm2"]
    expected = {
        s["id"]: s["expected_spoken"]
        for s in json.loads((ROOT / "sentences_norm.json").read_text(encoding="utf-8"))["sentences"]
    }

    # ── Taban gürültüsü: metni kesin bilinen referans klip ──
    ref_wav, ref_txt = ROOT / "refs" / "omnipick.wav", ROOT / "refs" / "omnipick.txt"
    base_hyp = transcribe(ref_wav)
    base_ref_w, base_hyp_w = norm_words(ref_txt.read_text(encoding="utf-8")), norm_words(base_hyp)
    base_wer = wer(base_ref_w, base_hyp_w)[0]
    print(f"TABAN (refs/omnipick.wav, metni kesin biliniyor): WER {base_wer:.3f}")
    print(f"  transkript: {base_hyp}\n")

    report: dict = {
        "model": MODEL,
        "taban": {"wav": "refs/omnipick.wav", "wer": round(base_wer, 4), "transkript": base_hyp},
        "setler": {},
    }

    for name in sets:
        d = OUT / name
        if not d.is_dir():
            print(f"ATLANDI: {name} yok", file=sys.stderr)
            continue
        rows, wers, miss_total = [], [], 0
        print(f"── {name} " + "─" * 40)
        for wav in sorted(d.glob("*.wav")):
            exp = expected.get(wav.stem)
            if exp is None:
                continue
            hyp = transcribe(wav)
            rw, hw = norm_words(exp), norm_words(hyp)
            w, s, dele, ins = wer(rw, hw)
            miss = missing_words(rw, hw)
            wers.append(w)
            miss_total += len(miss)
            rows.append({
                "id": wav.stem, "wer": round(w, 4), "sub": s, "del": dele, "ins": ins,
                "atlanan": miss, "beklenen": exp, "transkript": hyp,
            })
            flag = f"  ATLANAN: {miss}" if miss else ""
            print(f"  {wav.stem:20s} WER {w:.3f}{flag}")
        avg = sum(wers) / len(wers) if wers else None
        report["setler"][name] = {
            "ortalama_wer": round(avg, 4) if avg is not None else None,
            "toplam_atlanan_kelime": miss_total,
            "cumle": len(rows),
            "items": rows,
        }
        print(f"  → ortalama WER {avg:.3f} | toplam atlanan kelime {miss_total}\n")

    (OUT / "asr_eval.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("yazıldı: out/asr_eval.json")


if __name__ == "__main__":
    main()
