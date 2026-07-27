# Görev — şaşırma tonu: aday token kulak testi seti

Sen bir worker'sın. Görevi kendin yap, kısa rapor ver. Panel/subagent açma.

## Bağlam

Kullanıcı `experiments/higgs-tts3/demo.html` setini dinledi: **şaşırma hariç hepsi iyi.**
İki ayrı sorun çıktı:

1. Sette şaşırmanın **gerçek kullanımı yok** — tek şaşırma satırı "anlatılan-etiket"
   testiydi ve `"Şaşırdığımda şaşırma gibi efektlerle tepki verebiliyorum."` diye
   sakil bir cümle olarak okunuyordu. Kullanıcı: *"bu cümle kötü bir örnek olmuş."*
2. Asıl şikâyet: **`<|emotion:surprise|>` şaşkın DUYULMUYOR.** Ölçüm de bunu
   destekliyor — 21 emotion içinde Δsüre'si en düşük olan o (-0.02 s, ötekiler
   +0.1…+0.7 s). Anlaşılıyor ama iş yapmıyor olabilir.

## Ne yapacaksın

`experiments/higgs-tts3/` içine **şaşırmaya odaklı kulak testi** seti üret. Bu bir
A/B karşılaştırma: aynı cümle, farklı aday token'lar, yan yana dinlenecek.

### Cümleler (gerçekten şaşırılacak içerik — mevcut sakil cümleyi KULLANMA)

* **A — ünlemli** (kelime zaten şaşkınlığı taşıyor):
  `"Vay canına! Kargon bir gün erken gelmiş."`
* **B — ünlemsiz** (şaşkınlığı YALNIZ ton taşımak zorunda):
  `"Sınavdan tam not almışsın, hem de tek başına çalışarak."`

### Adaylar (her cümle için, sırasıyla)

| ad | gönderilen |
|---|---|
| `duz` | (token yok — taban) |
| `surprise` | `<\|emotion:surprise\|>` ← şu an canlıda olan |
| `awe` | `<\|emotion:awe\|>` |
| `arousal` | `<\|emotion:arousal\|>` |
| `elation` | `<\|emotion:elation\|>` |
| `expressive_high` | `<\|prosody:expressive_high\|>` |
| `surprise+pitch_high` | `<\|emotion:surprise\|><\|prosody:pitch_high\|>` |
| `surprise+expressive_high` | `<\|emotion:surprise\|><\|prosody:expressive_high\|>` |

8 koşul × 2 cümle = **16 wav**. Token cümle başında ve **bitişik** (boşluksuz) —
mevcut `_HUG_INLINE_RE` / sfx kuralıyla aynı.

Adayların tekil hâlleri 27 Tem ölçümünde 12/12 TEMİZ çıktı (bkz.
`handoff/2026-07-27-duygu-katmani.md` §2). **İki kombo ölçülmemiştir** — kulak
testinde denenmesi serbest, ama biri seçilirse canlıya girmeden önce
`token_probe.py`/`token_eval.py` ile ölçülmesi gerekir. Raporunda bunu yaz.

### Nasıl

* `demo_set.py` + `demo.html`'i örnek al: yeni `surprise_set.py` ve `surprise.html`.
  `./serve.sh surprise.html` ile açılabilmeli, her satırda ad + gönderilen metin
  görünmeli, wav'lar yan yana çalınabilmeli.
* Ses üretimi **canlı streaming ucundan**: `token_probe.py::synth`
  (`POST http://192.168.0.25:PORT/api/tts/stream`). Deney koşumu KULLANMA — eski
  `elation` yanlış teşhisi tam bundan çıkmıştı.
* Çalıştırma: `../../worker/.venv/bin/python surprise_set.py` (worker venv şart).
* Çıktılar `out/surprise/` ve `out/surprise.json`.

## Sınırlar

* **Sunucuya HİÇBİR değişiklik yapma.** Yalnız HTTP isteği at. Servis
  başlatma/durdurma, dosya kopyalama, systemctl YOK.
* `worker/higgs_tts.py` eşlemesine **dokunma** — kazanan kulakla seçilecek,
  değişiklik ondan sonra.
* **Görsel/işitsel test SENİN işin değil** — wav'ları üret, dinlemeyi kullanıcı yapar.
* Ölçülmemiş token canlıya girmez kuralı ayakta.

## Rapor (KISA)

* 16 wav üretildi mi, hata var mı
* `out/surprise.json` yolu ve `./serve.sh surprise.html` komutu
* Kombo token'ların ölçülmemiş olduğu notu
