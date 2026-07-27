"""Kullanıcının KULAKLA dinleyeceği örnek set — canlı yolun ta kendisi.

`token_probe.py` token'ları TEK TEK ve ölçüm için koşuyor. Burası farklı: modelin
gerçekten yazacağı cümleler, worker'ın GERÇEK dönüştürücüsünden
(`worker/higgs_tts.py::_to_higgs_markup`) geçirilip canlı streaming ucuna
gönderiliyor. Yani duyulan şey canlıda duyulacak şeyin aynısı — etiket yerleşimi,
duraklama kuralı, mood öneki dahil.

Her satırın ETİKETSİZ eşi de üretilir; fark yalnız etiketten gelsin.

    ../../worker/.venv/bin/python demo_set.py     # worker venv ŞART (livekit importu)

Çıktı: out/demo/<ad>.wav (+ `-duz` eşleri) ve out/demo.json → `./serve.sh demo.html`
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "worker"))

from higgs_tts import _extract_mood, _to_higgs_markup  # noqa: E402
from token_probe import _wav, synth  # noqa: E402

OUT = HERE / "out" / "demo"

# Modelin ÜRETEBİLECEĞİ cümleler (prompt'taki tarife uygun). Duyulacak şey bu.
CASES: list[tuple[str, str, str]] = [
    ("mood-warm", "şefkat / destek",
     "[mood:warm] Yanındayım, merak etme. Bunu birlikte çözeriz."),
    ("mood-calm", "sakinleştirme",
     "[mood:calm] Acelesi yok, her şey yolunda. Önce bir nefes al."),
    ("mood-proud", "kullanıcı bir şey başardığında",
     "[mood:proud] Gerçekten başardın işte, hem de tek başına."),
    ("mood-confused", "tam anlamadım",
     "[mood:confused] Tam anlamadım şimdi, biraz daha açar mısın?"),
    ("mood-excited", "canlıda ONAYLI — taban",
     "[mood:excited] Harika haber, gerçekten çok sevindim senin adına!"),
    ("mood-sad", "canlıda ONAYLI — taban",
     "[mood:sad] Çok üzüldüm bunu duyduğuma. [sigh] Yanındayım."),
    ("pause", "cümle içi duraklama (ASIL YENİLİK)",
     "Takvimine şöyle bir baktım [pause] evet, yarın öğleden sonra boşsun."),
    ("long-pause", "daha uzun duraklama",
     "Şunu bir düşüneyim bakalım [long_pause] tamam, buldum galiba."),
    ("pause-erken", "duraklama cümle başına çok yakın → ATILIR (koruma)",
     "Bir saniye [pause] düşüneyim, sanırım yarın uygun."),
    ("anlatilan-etiket", "etiketi ANLATIRKEN delik kalmıyor (canlı hata düzeltmesi)",
     "Şaşırdığımda [surprise-oh] gibi efektlerle tepki verebiliyorum."),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, note, raw in CASES:
        mood, text = _extract_mood(raw)
        markup = _to_higgs_markup(text, mood)
        # Etiketsiz eş: aynı cümle, TÜM işaretler atılmış hâli.
        plain = _to_higgs_markup(text, None)
        for suffix, payload in (("", markup), ("-duz", _strip(plain))):
            pcm, sr, _ = synth(payload)
            (OUT / f"{name}{suffix}.wav").write_bytes(_wav(pcm, sr))
        rows.append({"ad": name, "not": note, "ham": raw, "gonderilen": markup})
        print(f"{name:20s} {raw!r}\n{'':20s} → {markup!r}", flush=True)

    (HERE / "out" / "demo.json").write_text(
        json.dumps({"satirlar": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nyazıldı: {HERE / 'out' / 'demo.json'}")


def _strip(markup: str) -> str:
    """Kontrol token'larını at — "etiketsiz eş" bu."""
    import re

    return re.sub(r"<\|[a-z_]+:[a-z_]+\|>", " ", markup).replace("  ", " ").strip()


if __name__ == "__main__":
    main()
