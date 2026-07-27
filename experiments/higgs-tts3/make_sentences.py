"""sentences.json'u tts-local-bench kaynaklarından yeniden üretir.

Higgs, OmniVoice ile AYNI cümlelerle ölçülmeli — kullanıcı OmniVoice çıktılarını
zaten dinledi. Bu yüzden cümleler burada elle yazılmaz, tts-local-bench'ten türetilir.

    python3 make_sentences.py

Bağımlılık: yok (stdlib).
"""
from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "tts-local-bench"

COMMENT = (
    "TÜRETİLMİŞ DOSYA — kaynak: experiments/tts-local-bench/sentences.json (15) + "
    "sentences_norm.json (14). Kullanıcı OmniVoice çıktılarını bu cümlelerle dinledi; "
    "Higgs AYNI cümlelerle ölçülüyor ki karşılaştırma adil olsun. id'ler ve sıra "
    "DEĞİŞMEZ. Kaynak dosyalar değişirse: python3 make_sentences.py ile yeniden üret."
)


def main() -> None:
    base = json.loads((SRC / "sentences.json").read_text(encoding="utf-8"))["sentences"]
    norm = json.loads((SRC / "sentences_norm.json").read_text(encoding="utf-8"))["sentences"]
    out = OrderedDict([("_comment", COMMENT), ("sentences", base + norm)])
    (HERE / "sentences.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"sentences.json: {len(base)} + {len(norm)} = {len(base) + len(norm)} cümle")


if __name__ == "__main__":
    main()
