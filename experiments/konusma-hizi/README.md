# Konuşma hızı — hangi yol gerçekten değiştiriyor?

Canlı şikâyet (27 Tem 18:21 ve 18:33), üç tur üst üste:

```
Ayhan : Evet, biraz konuşma hızlandırır mısın?
Candan: Tabii Ayhan, konuşma hızımı biraz daha artırıyorum.
Ayhan : Hayır olmadı. İki birim daha arttır.
Candan: Tamam Ayhan, hızımı iki birim daha artırıyorum.
Ayhan : Hayır değil. Hâlâ çok yavaş konuşuyorsun.
```

**Tempo hiç değişmedi ve değişemezdi** — Candan'ın elinde hız kolu yoktu, üstelik
olmayan bir "birim" uydurdu. Bu deney önce **hangi yolun gerçek olduğunu ölçtü**,
sonra kademeleri belirledi.

## Üç aday, ölçüm sırası

| yol | sonuç |
|---|---|
| **(a)** `<|prosody:speed_fast|>` token'ı | **REDDEDİLDİ** — en fazla +%7.2, WER 0.004 → 0.030 |
| **(b)** motorun `speed` gövde parametresi | **YOK** — sözleşmede yazıyor, kod okumuyor |
| **(c)** WSOLA tempo (`worker/tempo.py`) | **SEÇİLDİ** — +%14.8 / +%29.7, WER değişmiyor |

**(b) neden aday değil:** `server/higgs-tts/server.py` docstring'i
`{"speed": 1.0}` diyor ama `do_POST`/`_do_stream` `params.get("speed")`'i HİÇ
okumuyor. Canlı doğrulama da yapıldı (`--speed-param-testi`): `speed` yok / 0.7 /
1.4 → 8.60 / 8.04 / 8.52 s, yani fark örnekleme gürültüsü. Sunucuya parametre
eklemek `higgs-tts` restart'ı isterdi; bu turda ona dokunulmadı.

## Sonuç tablosu

3 metin × 4 örnek = **koşul başına 12 örnek**, canlı `POST /api/tts/stream`,
Whisper (`mlx-community/whisper-large-v3-turbo`) geri-dönüşü. Asıl sayı Δsüre
DEĞİL **kelime/saniye**: kullanıcının sorusu "duyulur biçimde hızlandı mı".

| koşul | kelime/s | kazanç | WER | anlaşılan |
|---|---|---|---|---|
| taban | 2.498 | — | 0.004 | 12/12 |
| `prosody:speed_slow` | 2.324 | -%7.0 | 0.009 | 12/12 |
| `prosody:speed_fast` | 2.646 | **+%5.9** | **0.030** | 12/12 |
| `prosody:speed_very_fast` | 2.677 | **+%7.2** | **0.023** | 12/12 |
| `tempo 0.85` (**slow**) | 2.124 | -%15.0 | 0.004 | 12/12 |
| `tempo 1.15` (**fast**) | 2.868 | **+%14.8** | 0.004 | 12/12 |
| `tempo 1.30` (**very_fast**) | 3.239 | **+%29.7** | 0.004 | 12/12 |
| `tempo 1.45` (aday, kademe DEĞİL) | 3.607 | +%44.4 | 0.004 | 12/12 |

Karar ölçütü "≥%15 kelime/s **ve** anlaşılırlığı bozmadan"dı. Token yolu ikisinde
de kaldı: kazanç eşiğin yarısı kadar ve WER'i tabanın **4-7 katına** çıkarıyor.
WSOLA'da WER hiçbir kademede kıpırdamıyor (0.004 = tabanın kendisi).

## İlk ses gecikmesi — bozulmadı

Kabul şartıydı. Tek cümlede (livekit TTS'e **cümle cümle** gider), canlı akış,
7 tekrar medyanı:

```
ilk ses, filtresiz : 517 ms
ilk ses, tempo=1.30: 517 ms      fark +1 ms
ilk ses, tempo=1.15: 547 → 547 ms   fark  0 ms
```

Sebep: filtre ilk çıktısı için ~55 ms girdi ister, sunucudan gelen **ilk blok
320 ms**. Bekleme ilk bloğun içinde soğuruluyor, ek tur beklenmiyor. Streaming
blok/lookahead yapısına dokunulmadı — filtre onun **çıkışında** duruyor.

⚠️ Yukarıdaki tablodaki 3 cümlelik metinlerin `ilk_ses_s` alanları gecikme için
**yanıltıcıdır** (uzun prompt = uzun prefill). Gecikme yalnız `--gecikme` moduyla,
tek cümlede ölçülür.

## Perde korunuyor mu?

Basit resample tempoyla birlikte perdeyi de kaydırır ve Candan'ın sesi değişirdi —
kabul edilmezdi. WSOLA yalnız temposu değiştirir; otokorelasyonla ölçüldü ve
regresyon testine bağlandı (`worker/tests/test_higgs_tts.py::TempoFilterTest`):
0.85 / 1.15 / 1.30 oranlarında F0 **200.0 → 200.0 Hz**.

## Koşum

```bash
# 1) ölçüm (canlı ses üretir; higgs-tts'e yalnız HTTP isteği atar)
../../worker/.venv/bin/python speed_probe.py --n 4
../../worker/.venv/bin/python speed_probe.py --gecikme --n 7 --oran 1.30
../../worker/.venv/bin/python speed_probe.py --speed-param-testi

# 2) anlaşılırlık (Whisper geri-dönüşü)
../tts-local-bench/venvs/whisper/bin/python speed_eval.py

# 3) KULAK TESTİ — kademe aralığını kullanıcı seçer
./serve.sh                     # → http://localhost:8011/hiz-seti.html
```

## Açık kalan

* **Tavan.** Kademeler `very_fast` = 1.30'da bitiyor. 1.45 ölçüldü ve temiz çıktı
  (+%44.4, WER 0.004) ama kademe yapılMADI — kullanıcı "hâlâ yavaş" derse tavanı
  yükseltmek ölçüm gerektirmez, sayfadaki `1.45 (aday)` satırını dinlemek yeter.
* **Kulak testi bekliyor.** Ölçüm "anlaşılıyor mu"yu söylüyor; "bu kademe *biraz
  daha hızlı* demek mi"yi kullanıcı söyleyecek (`hiz-seti.html`).
