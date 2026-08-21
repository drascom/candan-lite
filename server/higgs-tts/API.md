# Higgs TTS — HTTP API

Başka bir uygulamanın bu sunucuyu ses motoru olarak kullanması için referans.
**19 Ağustos 2026'da canlıda doğrulandı.**

**Adres:** `http://192.168.0.25:8809`
**Kimlik doğrulama: YOK.** Ağa erişen herkes kullanabilir.
**Çıktı:** 24 kHz · mono · signed 16-bit little-endian

---

## Uçlar

| yöntem | yol | döner |
|---|---|---|
| GET | `/health` | durum JSON'u; model hazır değilse **503** |
| GET | `/api/default` | `engine`, `model`, `ref_fingerprint`, `sample_rate` |
| POST | `/api/tts` | tam **WAV** (`format=pcm` ile ham PCM) |
| POST | `/api/tts/stream` | **chunked ham PCM** — ilk blok hazır olunca akmaya başlar |

Gövde **JSON** veya **form-urlencoded** olabilir.

## Alanlar

| alan | uç | not |
|---|---|---|
| `text` | ikisi | **zorunlu** — boşsa `400 {"error": "text boş"}` |
| `format` | `/api/tts` | `wav` (varsayılan) \| `pcm` |
| `block_frames`, `lookahead` | `/api/tts/stream` | akış tanecikliği; boş bırak |
| `mood` | ikisi | kabul edilir, **sese ETKİSİ YOK** — aşağıya bak |

## Örnek

```bash
curl -s -X POST http://192.168.0.25:8809/api/tts \
  -H 'Content-Type: application/json' \
  -d '{"text":"Merhaba, bu bir deneme."}' \
  --output ses.wav
```

Yanıt başlıkları: `X-Higgs-Rtf`, `X-Higgs-Audio-Seconds`, `X-Higgs-Sample-Rate`, `X-Higgs-Ref`.

## ⚠️ Duygu `mood` ile verilmez — metne gömülür

Sunucu `mood` alanını okur ve loglar ama **sese yansıtmaz**. Duygu, metnin içindeki
token'la taşınır ve token **cümle başına** konur:

```json
{"text": "<|emotion:enthusiasm|>Harika bir haber!"}
```

Duygular: `enthusiasm` `sadness` `affection` `confusion` `amusement` `contemplation`
`determination` `relief` `pride` `contentment`
Diğerleri: `<|sfx:laughter|>` `<|style:whispering|>` `<|prosody:pause|>`

**Tanınmayan bir etiket harfi harfine OKUNUR** — yani yanlış yazılan token sese
"emotion enthusiasm" diye karışır. Etiket üreten taraf listeye sadık kalmalı.

## Hata kodları

`400` boş text / bozuk gövde · `503` model hazır değil · `500` sentez hatası ·
`502` model 0 kare üretti · `404` bilinmeyen uç

---

## Sınırlar — entegrasyondan önce oku

**1. Eşzamanlılık yok.** Sentez `_SYNTH_LOCK` ile serileştirilmiş, dinleme kuyruğu
(backlog) **5**. Arka arkaya istek atan bir istemci hem kuyruğu doldurur hem de
**Candan'ın kendi konuşmasını bekletir**. Ölçülen RTF ~0.5 → 10 sn ses ~5 sn sürer;
o süre boyunca asistan sessiz kalır.

**2. Ses kimliği sabit.** Referans ses ve metni sunucuda tanımlı (`HIGGS_REF_AUDIO`,
`HIGGS_REF_TEXT`); istek başına değiştirilemez. Yani çağıran uygulama da **Candan'ın
sesiyle** konuşur. Farklı ses için ayrı bir örnek gerekir.

**3. Kimlik doğrulama yok, `0.0.0.0`'a bağlı.** Ağdaki herkes kullanabilir ve
tüketebilir. Dışarı açılacaksa önüne bir kapı koymak gerekir.
