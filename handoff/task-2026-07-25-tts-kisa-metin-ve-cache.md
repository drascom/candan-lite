# GÖREV: kısa-metin çökmesi + kalıp cümle ses cache'i

Sen bir WORKER'sın. İşi kendin yap, başka panel/worker AÇMA, cmux ile delege ETME.
Bitince KISA rapor ver (madde madde, uzun anlatım yok).

Bağlam: `handoff/2026-07-25-tts-arastirma-ve-server-adimlari.md` §3 madde 3 ve 4.
Kod: `worker/omnivoice_tts.py` (OmniVoice TTS plugin), `worker/trnorm.py`.

## KURALLAR — ihlal etme
- **Sunucuya (`192.168.0.25`, oracle-stage) HİÇBİR yazma isteği gönderme.** `POST /api/set_default`
  ve benzeri YASAK. `GET /api/default` gibi salt-okuma serbest.
- **Görsel test YOK.** Uygulamayı açma, ekran görüntüsü alma, GUI doğrulama yapma.
  Sadece **build/import + unit test** doğrulaması yap. Sesi kullanıcı kendisi dinleyecek.
- Yeni pip bağımlılığı ekleme. stdlib + halihazırda kullanılanlar (aiohttp, websockets, livekit) yeter.

## İŞ 1 — Kısa metin çökmesi
OmniVoice çok kısa metinlerde `ZeroDivisionError` verebiliyor. Canlıda "Tamam.", "Evet."
gibi yanıtlar turu öldürmemeli.

1. **Lokalde tekrar üret.** `experiments/tts-local-bench/` altında OmniVoice runner'ı var
   (`runners/run_omnipick.py`, `venvs/`). Artan uzunlukta metinlerle ("Ha.", "Evet.", "Tamam.",
   "Peki tamam." ...) çağırıp **hangi eşikte patladığını** bul. Eşiği rapora yaz.
   Sunucuya değil, lokale karşı çalış.
2. **Guard ekle** — `worker/omnivoice_tts.py` içinde `_run()`'da, `normalize_tr()` SONRASI:
   - Metin eşiğin altındaysa çökmeyi önle. Tercih sırası: (a) cache'ten çal (bkz. İŞ 2),
     (b) metni zararsız şekilde uzat (ör. sonuna noktalama/boşluk — üretilen sesi bozmayacak
     en az müdahaleyi ÖLÇEREK seç), (c) tek retry.
   - **Her hâlükârda:** TTS hatası (`ZeroDivisionError`, WS `error`, HTTP 5xx) turu ÖLDÜRMESİN.
     Yakala, `logger.warning` ile logla, emitter'ı temiz kapat. Sessiz kalmak kabul,
     agent'ın çökmesi kabul DEĞİL.
3. Guard hem WS hem HTTP yolunda geçerli olsun (ikisi de aynı normalize metni kullanıyor).

## İŞ 2 — Kalıp cümle ses cache'i
Amaç: klon maliyetini (~3.4×) sık tekrar eden kısa yanıtlarda sıfırlamak.
Kullanıcı bu yolu seçti; genel LRU cache İSTENMEDİ — sadece kalıp/kısa cümleler.

- **Anahtar:** `normalize_tr()` sonrası metin + voice + mood (mood `speed`'i değiştiriyor,
  anahtara girmeli) + **pinned referans kimliği**.
  ⚠️ Referans kimliği ŞART: `handoff/...server-adimlari.md` §1.B ile `default-ref.wav`
  değişebilir; ref değişince eski cache YANLIŞ SESLE çalar. `GET /api/default`'tan dönen
  `ref_audio` + `ref_text`'i hash'le anahtara kat. Ref okunamazsa cache'i devre dışı bırak
  (yanlış ses çalmaktansa yeniden üret).
- **Depo:** `worker/data/` altında bir alt dizin. Format 24 kHz mono s16le — emitter'a
  dönüşümsüz push edilebilsin. Dizin ve boyut sınırını makul tut.
- **Kalıp listesi:** `worker/pi_brain.py` içinde scripted/sabit kısa yanıtlar var
  (`'scripted'`, sabit cümle string'leri). Tara, gerçekten tekrar edenleri listele.
  Listeyi tek yerde, `MOOD_PRESETS` gibi düzenlenebilir bir sabitte tut.
- **Doldurma:** çalışma anında ilk üretimde yaz (lazy). Ayrıca elle çalıştırılabilir küçük bir
  prewarm yolu bırak (script veya `python -m` girişi) — otomatik çalıştırma yok.
- Cache miss/hata durumunda normal yola şeffaf düş.

## DOĞRULAMA (yapman gerekenler)
- `python3 -m compileall worker/` temiz.
- `worker/tests/` altında ilgili testler geçiyor; eklediğin davranış için **birim test yaz**
  (guard eşiği, cache anahtarı ref değişince değişiyor mu, mood anahtara giriyor mu).
- `python3 experiments/tts-local-bench/trnorm.py --selftest` → 26/26 (regresyon olmasın).
- Lokal bench ile en az bir kısa cümle uçtan uca üretilebiliyor.

## RAPOR (kısa)
- Çökme eşiği kaç karakter/kelime, seçtiğin guard hangisi ve neden.
- Değişen dosyalar (tek satır açıklama).
- Cache anahtarı formülü + nereye yazıyor.
- Test sonuçları.
- Kullanıcının kulakla dinlemesi gereken şey varsa TEK maddede söyle.
