"""Duygu atlası — katalogdaki HER token, ONA UYGUN bir Türkçe cümlede.

NEDEN: bugüne kadarki ölçüm "anlaşılıyor mu"yu cevapladı (43 token, WER, baş yeme).
"Doğru DUYULUYOR mu"yu cevaplamadı. Şaşırma vakası bunu kanıtladı:
`<|emotion:surprise|>` ölçümde 12/12 tertemizdi ama kulakta şaşkın duyulmuyordu —
kullanıcı dinleyince `<|emotion:awe|>`e geçildi. Üstelik her token şimdiye kadar
AYNI nötr cümlede ("Bugün harika bir haber var.") dinlendi; bir duygu, ona uymayan
bir cümlede kendini zaten gösteremez.

Burada her token kendi duygusuna uyan bir cümlede duyulur. Duygu cümlenin
İÇERİĞİNDE de var — token'ın katkısı ancak o zaman ayırt edilir.

Ses CANLI streaming ucundan alınır (`token_probe.py::synth` → `POST /api/tts/stream`).
Deney koşumu DEĞİL: eski `elation` yanlış teşhisi tam bundan çıkmıştı.

YERLEŞİM (ölçülmüş — `handoff/2026-07-27-duygu-katmani.md` §4):
  • emotion / style / prosody-önek → cümle BAŞINDA, bitişik (boşluksuz)
  • sfx → taklide bitişik (`<|sfx:laughter|>Haha, …`)
  • pause / long_pause → cümlenin ORTASINDA, iki yanı BOŞLUKSUZ, en az 3 kelimeden
    sonra. Cümle başına yakın duraklama ilk kelimeyi yiyor.

Her satırın ETİKETSİZ düz eşi de üretilir; fark yalnız token'dan gelsin.

⚠️ KOMBO bölümü ÖLÇÜLMEMİŞTİR. Atlasta durur, canlıya girmez.

    ../higgs-tts3/../../worker/.venv/bin/python atlas_set.py   # ya da python3
    python3 atlas_set.py --only emotion_awe kombo_awe+expressive_high

Üretilmiş wav yeniden üretilmez (yarım kalırsa kaldığı yerden devam eder);
baştan istenirse `--yenile`.

Çıktı: out/wav/<ad>.wav (+ `-duz`) ve out/atlas.json → `./serve.sh`
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Takımı yeniden yazma, içe aktar: canlı streaming ucu + wav sarmalayıcı.
sys.path.insert(0, str(HERE.parent / "higgs-tts3"))

from token_probe import _wav, synth  # noqa: E402

OUT = HERE / "out"
WAV = OUT / "wav"

# Satır içi duraklamanın yeri. Cümlede bu işaretin İKİ YANINDA BOŞLUK YOK.
ICI = "‖"

# Canlıda (`worker/higgs_tts.py::MOOD_PRESETS` + `HIGGS_TAG_MAP`) GERÇEKTEN
# kullanılan token'lar. Atlas bir katalog, canlı eşleme değil — gerisi yalnız
# dinlemek için var.
CANLI = {
    "emotion:affection", "emotion:contentment", "emotion:pride",
    "emotion:confusion", "emotion:enthusiasm", "emotion:sadness", "emotion:awe",
    "prosody:pause", "prosody:long_pause", "sfx:laughter", "sfx:sigh",
}

# (ad, önek token'ları, satır içi token, cümle, not)
# `ad` = katalogdaki token adı. Kombo satırlarında birleşik ad.
ROWS: list[dict] = []


def _add(kategori: str, ad: str, onek: str, ici: str, cumle: str, nt: str = "") -> None:
    ROWS.append({"kategori": kategori, "ad": ad, "onek": onek, "ici": ici,
                 "cumle": cumle, "not": nt})


# ── emotion (21) ─────────────────────────────────────────────────────────────
# Cümleler Candan'ın ağzına yakışan gündelik ev-asistanı cümleleri. Canlıda
# kullanılmayanlar da girdi (atlas, eşleme değil) — sayfada rozetle işaretli.
_EMO: list[tuple[str, str, str]] = [
    ("affection", "Bugün kendine iyi bakmayı unutma olur mu, ben hep buradayım.", ""),
    ("amusement", "Kedin yine klavyenin tam ortasına oturmuş, şu hâline bak.", ""),
    ("anger", "Yine haber vermeden iptal etmişler, bu kaçıncı oldu böyle.", ""),
    ("arousal", "Hadi kalk bakalım, on dakikaya çıkmamız gerekiyor!", ""),
    ("awe", "Sınavdan tam not almışsın, hem de tek başına çalışarak.",
     "kullanıcının kulakla seçtiği ton — şaşırma buraya bağlandı"),
    ("bitterness", "Bunca emekten sonra bir teşekkür bile etmediler.", ""),
    ("confusion", "Tam anlamadım şimdi, biraz daha açar mısın?", ""),
    ("contemplation", "Şöyle bir düşünüyorum da, belki de en doğrusu beklemek.", ""),
    ("contentment", "Acelesi yok, her şey yolunda. Önce bir nefes al.", ""),
    ("determination", "Bu sefer bitiriyoruz, yarın sabah dokuzda başlıyoruz.", ""),
    ("disgust", "Buzdolabındaki süt bozulmuş, kokusu bütün mutfağa yayılmış.", ""),
    ("elation", "Kabul edilmişsin, gerçekten çok sevindim senin adına!",
     "eski 'bozuk' kaydı YANLIŞTI — canlı yolda 24/24 temiz"),
    ("enthusiasm", "Harika bir fikir, hemen listeyi birlikte hazırlayalım.", ""),
    ("fear", "Alt kattan bir ses geldi, sen de duydun mu?", ""),
    ("helplessness", "Elimden gelen bir şey kalmadı, ne denediysem olmadı.", ""),
    ("longing", "O yaz akşamı yürüyüşlerini çok özledim doğrusu.", ""),
    ("pride", "Gerçekten başardın işte, hem de tek başına.", ""),
    ("relief", "Neyse ki bulunmuş, sabahtan beri içim içimi yiyordu.", ""),
    ("sadness", "Çok üzüldüm bunu duyduğuma, yanındayım.", ""),
    ("shame", "Kusura bakma, ben tamamen unutmuşum.", ""),
    ("surprise", "Vay canına! Kargon tam bir gün erken gelmiş.",
     "ölçümde temiz ama kulakta şaşkın duyulmadı — canlıda yerini awe aldı"),
]
for _ad, _c, _n in _EMO:
    _add("emotion", f"emotion:{_ad}", f"<|emotion:{_ad}|>", "", _c, _n)

# ── prosody, cümle başı (8) ──────────────────────────────────────────────────
_PRO: list[tuple[str, str, str]] = [
    ("speed_very_slow", "Şimdi çok dikkatli dinle, adım adım tarif ediyorum.",
     "ŞÜPHELİ: 24'te 1 kez cümlenin önüne uydurma konuşma ekledi"),
    ("speed_slow", "Acelen yok, cümleleri birlikte tekrar edelim.", ""),
    ("speed_fast", "Çabuk ol, otobüs iki dakika sonra kalkıyor!", ""),
    ("speed_very_fast",
     "Ekmek, süt, yumurta, peynir, zeytin, hepsini listeye ekledim.", ""),
    ("pitch_low", "Herkes uyuyor, sesimi biraz alçaltıyorum.", ""),
    ("pitch_high", "Kapıya biri geldi, bir bakar mısın?",
     "'soru tonu'na benziyor ama AYNI ŞEY DEĞİL — [question-*] hâlâ siliniyor"),
    ("expressive_high", "İnanamıyorum, bunu gerçekten tek başına yapmışsın!", ""),
    ("expressive_low", "Saat on bir oldu, dışarısı on sekiz derece.", ""),
]
for _ad, _c, _n in _PRO:
    _add("prosody", f"prosody:{_ad}", f"<|prosody:{_ad}|>", "", _c, _n)

# ── prosody, satır içi (2) — ölçülmüş yerleşim: orta, bitişik, ≥3 kelime sonra ─
_add("prosody", "prosody:pause", "", "<|prosody:pause|>",
     f"Takvimine şöyle bir baktım{ICI}evet, yarın öğleden sonra boşsun.",
     "cümle ortasında ve bitişik — ölçülmüş tek güvenli yerleşim")
_add("prosody", "prosody:long_pause", "", "<|prosody:long_pause|>",
     f"Şunu bir düşüneyim bakalım{ICI}tamam, sanırım buldum.",
     "aynı kural, daha uzun sessizlik")

# ── style (3) ────────────────────────────────────────────────────────────────
_add("style", "style:singing", "<|style:singing|>", "",
     "Mutlu yıllar sana, mutlu yıllar sana.",
     "ŞÜPHELİ: 2 aşırı uzun örnek (şarkı için normal olabilir)")
_add("style", "style:shouting", "<|style:shouting|>", "",
     "Buradayım, mutfaktayım! Beni duyabiliyor musun?")
_add("style", "style:whispering", "<|style:whispering|>", "",
     "Bebek yeni uyudu, sessizce çıkalım.")

# ── sfx (2) — taklit etikete BİTİŞİK, düz eşinde taklit kalır ────────────────
_add("sfx", "sfx:laughter", "<|sfx:laughter|>", "",
     "Haha, çorabını yine ters giymişsin.", "canlıda [laughter] olarak kullanılıyor")
_add("sfx", "sfx:sigh", "<|sfx:sigh|>", "",
     "Haah, uzun bir gündü, sonunda oturabildim.", "canlıda [sigh] olarak kullanılıyor")

# ── kombo (8) — ⚠️ ÖLÇÜLMEDİ, yalnız atlasta durur ───────────────────────────
_KOMBO: list[tuple[str, str, str, str]] = [
    ("awe+expressive_high", "<|emotion:awe|><|prosody:expressive_high|>", "",
     "Sınavdan tam not almışsın, hem de tek başına çalışarak."),
    ("surprise+expressive_high",
     "<|emotion:surprise|><|prosody:expressive_high|>", "",
     "Vay canına! Kargon tam bir gün erken gelmiş."),
    ("sadness+speed_slow", "<|emotion:sadness|><|prosody:speed_slow|>", "",
     "Çok üzüldüm bunu duyduğuma, yanındayım."),
    ("enthusiasm+speed_fast", "<|emotion:enthusiasm|><|prosody:speed_fast|>", "",
     "Harika bir fikir, hemen listeyi birlikte hazırlayalım."),
    ("affection+pitch_low", "<|emotion:affection|><|prosody:pitch_low|>", "",
     "Bugün kendine iyi bakmayı unutma olur mu, ben hep buradayım."),
    ("contentment+expressive_low",
     "<|emotion:contentment|><|prosody:expressive_low|>", "",
     "Acelesi yok, her şey yolunda. Önce bir nefes al."),
    ("pride+expressive_high", "<|emotion:pride|><|prosody:expressive_high|>", "",
     "Gerçekten başardın işte, hem de tek başına."),
    ("confusion+pause", "<|emotion:confusion|>", "<|prosody:pause|>",
     f"Tam anlamadım şimdi{ICI}biraz daha açar mısın?"),
]
for _ad, _onek, _ici, _c in _KOMBO:
    _add("kombo", f"kombo:{_ad}", _onek, _ici, _c, "ÖLÇÜLMEDİ")


def _slug(ad: str) -> str:
    return ad.replace(":", "_").replace("+", "-")


def _gonderilen(row: dict) -> str:
    """Etiketli hâl: önek bitişik, satır içi token iki yanı boşluksuz."""
    return row["onek"] + row["cumle"].replace(ICI, row["ici"])


def _duz(row: dict) -> str:
    """Etiketsiz eş: aynı cümle, hiç token yok. Fark yalnız token'dan gelsin."""
    return row["cumle"].replace(ICI, " ")


def _sure_s(path: Path) -> float | None:
    """Var olan wav'ın süresi (yeniden üretmeden devam edebilmek için)."""
    try:
        raw = path.read_bytes()
        if len(raw) <= 44:
            return None
        sr = struct.unpack("<I", raw[24:28])[0]
        veri = struct.unpack("<I", raw[40:44])[0]
        return round(veri / 2 / sr, 3) if sr else None
    except Exception:  # noqa: BLE001 — bozuk wav koşumu durdurmasın
        return None


def _uret(metin: str, hedef: Path, yenile: bool) -> float | None:
    """Wav yoksa üret, varsa DOKUNMA (yarım koşum kaldığı yerden devam etsin)."""
    if hedef.exists() and not yenile:
        s = _sure_s(hedef)
        if s:
            return s
    pcm, sr, _ = synth(metin)
    hedef.write_bytes(_wav(pcm, sr))
    return round(len(pcm) / 2 / sr, 3)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None, help="yalnız bu satırlar")
    ap.add_argument("--yenile", action="store_true", help="var olan wav'ı da üret")
    args = ap.parse_args()

    rows = ROWS
    if args.only:
        want = set(args.only)
        rows = [r for r in rows if r["ad"] in want or _slug(r["ad"]) in want]
        if not rows:
            raise SystemExit(f"eşleşen satır yok: {args.only}")

    WAV.mkdir(parents=True, exist_ok=True)
    path = OUT / "atlas.json"
    onceki: dict[str, dict] = {}
    if path.exists():
        try:
            onceki = {r["ad"]: r for r in
                      json.loads(path.read_text(encoding="utf-8"))["satirlar"]}
        except Exception:  # noqa: BLE001
            onceki = {}

    numara: dict[str, int] = {}
    for idx, row in enumerate(rows, 1):
        slug = _slug(row["ad"])
        gonderilen, duz = _gonderilen(row), _duz(row)
        numara[row["kategori"]] = numara.get(row["kategori"], 0) + 1
        # HATA OLURSA DUR: yarım kalan koşum kaldığı yerden devam edebilir.
        sure = _uret(gonderilen, WAV / f"{slug}.wav", args.yenile)
        duz_sure = _uret(duz, WAV / f"{slug}-duz.wav", args.yenile)
        onceki[row["ad"]] = {
            "kategori": row["kategori"], "no": numara[row["kategori"]],
            "ad": row["ad"], "not": row["not"],
            "canli": row["ad"] in CANLI,
            "olculdu": row["kategori"] != "kombo",
            "gonderilen": gonderilen, "duz_metin": duz,
            "wav": f"wav/{slug}.wav", "duz_wav": f"wav/{slug}-duz.wav",
            "sure_s": sure, "duz_sure_s": duz_sure,
            "sure_delta_s": round(sure - duz_sure, 3)
            if sure and duz_sure else None,
        }
        print(f"[{idx}/{len(rows)}] {row['ad']:34s} {sure}s "
              f"(düz {duz_sure}s)", flush=True)
        # Her satırdan sonra yaz: koşum yarıda kalırsa kayıp olmasın.
        sirali = [onceki[r["ad"]] for r in ROWS if r["ad"] in onceki]
        path.write_text(json.dumps({"satirlar": sirali}, ensure_ascii=False,
                                   indent=2), encoding="utf-8")

    print(f"\nyazıldı: {path}  ({len(rows)} satır, {len(rows) * 2} wav)")


if __name__ == "__main__":
    main()
