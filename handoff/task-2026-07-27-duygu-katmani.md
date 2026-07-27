# GÖREV: duygu katmanını genişlet (Higgs kontrol token'ları)

**Durum: SIRADA.** Streaming işi bitince başlatılacak — ikisi de `worker/higgs_tts.py` ve
`/opt/higgs-tts/server.py` üzerinde, çakışmasınlar.

## NEDEN
Kullanıcının kendi ifadesi: *"Konuşmaya duygu katmak çok önemli şu an."*
OmniVoice'ta bu mekanizma **ölüydü** — `instruct` yok sayılıyordu, elde yalnız `speed`
değişimi vardı (`handoff/2026-07-25-tts-arastirma-ve-server-adimlari.md` §2). Higgs'te
gerçek kontrol token'ları var ve kullanıcı canlıda doğruladı: gülme, üzgün ton, neşeli
konuşma **çalıştı ve beğenildi** (2026-07-27 13:06-13:10 oturumu).

## ŞU AN KULLANILAN (canlı, `worker/higgs_tts.py` → `HIGGS_TAG_MAP`)
```
[mood:excited]  → <|emotion:enthusiasm|>
[mood:sad]      → <|emotion:sadness|>
[laughter]      → <|sfx:laughter|>Haha,
[sigh]          → <|sfx:sigh|>Haah,
[surprise-oh]   → <|emotion:surprise|>
[question-en], [confirmation-en] → SİLİNİYOR (karşılığı yok)
```
Yani **43 token'ın 5'i** kullanımda. Prosody'nin hiçbiri kullanılmıyor.

## TAM KATALOG (resmi `PROMPTING.md`, 43 etiket)
- **emotion (21, cümle başı):** affection, amusement, anger, arousal, awe, bitterness,
  confusion, contemplation, contentment, determination, disgust, elation, enthusiasm,
  fear, helplessness, longing, pride, relief, sadness, shame, surprise
- **prosody (10):** cümle başı → speed_very_slow, speed_slow, speed_fast, speed_very_fast,
  pitch_low, pitch_high, expressive_high, expressive_low · satır içi → pause, long_pause
- **style (3):** singing, shouting, whispering
- **sfx:** `<|sfx:laughter|>Haha, ...` — etiket önce, ses taklidi bitişik, BOŞLUK YOK

Yerleşim kuralı: emotion/style/prosody-hız-perde **cümle başına**; sfx ve pause **tam yerine**.
Etiketler istiflenebilir: `<|emotion:enthusiasm|><|sfx:laughter|>Haha, ...`

## ⚠️ EN ÖNEMLİ KURAL — HER TOKEN ÖLÇÜLECEK
`worker/higgs_tts.py:107-117`'deki ölçüm bunu kanıtladı:
```
düz (etiketsiz)         12/12     <|emotion:sadness|>    12/12
<|emotion:enthusiasm|>  12/12     <|emotion:surprise|>   12/12
<|emotion:amusement|>   12/12     <|sfx:laughter|>       12/12
<|emotion:elation|>    5-7/12  ← cümle BAŞINI yiyor, 3 örnek TAMAMEN BOŞ
```
`elation` geçerli bir katalog etiketi ve tokenizer tanıyor — yine de bozuk.
**Tokenizer'ın tanıması çalıştığı anlamına gelmez.** Katalogdaki hiçbir token ölçülmeden
kullanıma alınmayacak.

## MİMARİ KARAR (2026-07-27, kullanıcı onayladı) — ÖN SINIFLANDIRICI YOK

Soru soruldu: `truth_check` Katman 3'teki 115 ms'lik sınıflandırıcı, duyguyu ÖNDEN
belirlemek için de kullanılsın mı? **Hayır. Duygu kararı MODELE ait.** Gerekçeler:

1. **Maliyet profili farklı.** truth_check sınıflandırıcısı maliyet kapısının arkasında —
   yalnız araç hatasında çalışır, tipik turda hiç çağrılmaz. Duygu tespiti HER turda ve
   konuşma başlamadan ÖNCE çalışırdı; kullanıcının aktif şikâyeti olan gecikmeye
   115 ms sabit ekler.
2. **Kategori farkı.** Kurulan ilke: *model NE YAPILACAĞINA karar verir, harness NE
   OLDUĞUNU söyler.* Araç sonucu doğrulanabilir bir OLGU. Duygu yaratıcı bir TERCİH —
   karşılaştırılacak gerçeği yok.
3. **Model zaten iyi yapıyor.** 2026-07-27 13:06-13:10 canlı oturumu: fıkrada `[laughter]`,
   üzücü konuda `[mood:sad]`, neşeli anlatımda `[mood:excited]` — kullanıcı onayladı.
   Cümleyi YAZAN model, bitmiş cümleyi gören sınıflandırıcıdan daha çok bağlama sahip.

**Plan: modele bırak → ÖLÇ → tutarsızlık çıkarsa harness'ı ona göre sertleştir.**
21 duyguya genişletince model seçimde tutarsızlaşırsa bu ölçülebilir; o zaman ikinci
geçiş yeniden değerlendirilir. Şu an bir sorun olduğuna dair kanıt YOK — kanıtsız katman
eklenmeyecek.

Not: ikinci geçişin gerçekten değerli olabileceği tek yer duygunun kendisi değil, modelin
duyguyu koymayı UNUTTUĞU durumu yakalamak (bu bir doğrulama işi, harness'a uyar). Ama önce
unutma sıklığı ölçülmeli.

## YAPILACAKLAR
1. **21 emotion + 10 prosody + 3 style token'ının tamamını ölç.** Aynı Türkçe cümle,
   token başına ≥12 örnek, Whisper geri-dönüşüyle anlaşılırlık + kelime düşmesi.
   Yöntem hazır: `experiments/higgs-tts3/` (runner, asr_eval, compare.html).
   **Çıktı: hangi token temiz, hangisi bozuk — tablo halinde.**
2. Temiz çıkanlardan Türkçe sohbete uygun olanları seç, `HIGGS_TAG_MAP`'i genişlet.
   Prompt tarafı (`pi/AGENTS.md`, `pi/personas/candan.md`) modele hangi etiketleri
   üretteceğini söylüyor — yeni etiket eklenecekse ORADA da tarif edilmeli.
3. **`pause` / `long_pause`** özellikle değerli: konuşmanın ritmini doğrudan iyileştirir,
   duygu gerektirmez. Cümle içi doğal duraklamalar için düşün.
4. Kullanıcının dinlemesi için örnek set üret (`compare.html`).

## BİLİNEN AÇIK — bunu da düzelt
Model etiketi **kullanmak** yerine **anlatırken** temizleyici onu siliyor ve cümlede
delik bırakıyor. Canlı örnek (13:09:46):
```
model yazdı: "...şaşırdığımda [surprise-oh] gibi efektlerle tepki verebilirim..."
kullanıcı duydu: "...şaşırdığımda ___ gibi efektlerle..."
```
Silmek yerine okunabilir karşılığa çevirmek daha doğru ("şaşırma efekti" gibi) ya da
bağlamdan ayırt etmek. Dar bir durum ama duyulabiliyor.

## KURALLAR
- Ölçüm GPU kullanır; `candan-brain` ve whisper'ı durdurma, canlı sohbeti bozma.
- `worker/pi_brain.py`, `truth_check.py`, `speaker_tap.py`, `omnivoice_tts.py` DOKUNMA.
- Streaming işinin getirdiği yapıyı BOZMA (o önce gidiyor).
- Ölçülmemiş token'ı canlıya ALMA.
