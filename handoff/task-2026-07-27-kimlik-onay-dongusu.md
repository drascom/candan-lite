# Görev — Kimlik ONAY DÖNGÜSÜ (belirsizken sor, onaylanırsa öğren)

Sen bir worker'sın. İşi kendin yap, kısa rapor ver. Panel/subagent açma.

## Teşhis (canlı log'dan ölçüldü, 27 Tem)

Son 2 saatte **85 konuşma turu**: **43 Bilinmeyen (%51)**, 37 Ayhan, 5 Havi.
Sebep dağılımı: güvenli pencere yok 16 · yetersiz ardışık onay 17 · çelişen pencere 10.

Kullanıcı **Ayhan'dır** (teyit etti). O 5 "Havi" turu ise **Çiğdem'e ait** — evdeki
ÜÇÜNCÜ bir kişi, `speakers.db`'de **hiç profili yok**. Sistem onu reddetmek yerine
en yakın profile (Havi) yapıştırmış.

⚠️ **Bu, "tanıyamıyor"dan farklı ve daha ciddi bir hata: KAPALI KÜME davranışı.**
Sistem "bu kimse tanımadığım biri" diyemiyor, hep en yakın bilinen profili seçiyor.
Log'daki eşik bunu açıklıyor: asnorm eşiği **-1.00**, ve Havi **-0.748** skorla kabul
edilmiş. Yabancı bir ses bu barajı rahatça aşıyor.

Sonuç: Çiğdem'in konuşmaları Havi'nin kimliğine, dolayısıyla **Havi'nin hafızasına ve
persona'sına** yazılmış olabilir. Bu bir mahremiyet sorunu, sadece doğruluk sorunu değil.

`speakers.db`: yalnız 2 profil (Ayhan, Havi), **her biri 6 örnek**, dosya
**22 Temmuz'dan beri değişmemiş**. Model `wespeaker_en_voxceleb_resnet34_LM`.
Tek konuşma içinde skorlar zıplıyor: `Ayhan=3.44 → Ayhan=-0.36 → Havi=-0.75`.

**ASIL KİLİT:** 18 Temmuz planı "emin değilsen kayıt sihirbazını başlatma" diyor
(yanlış kayıt olmasın diye — o gün doğru karardı). Ama bu, sistemin kendini
düzeltmesini de kapattı: *tanıyamıyor → sormuyor → yeni örnek gelmiyor → tanıyamamaya
devam ediyor.* `SPEAKER_LEARN_ENABLED=false`. Kullanıcının "identification/confirmation
yapamıyoruz" dediği şey bu döngü.

**Bu turda kilidi açıyoruz:** belirsizken SOR, onay gelirse O TURUN pencerelerini
profile EKLE.

## Yapılacaklar

### 1. Aday çıkarımı (karar "Bilinmeyen" olsa bile)

Tur kararını üreten yer (`worker/agent.py`'de "speaker turn kararı" log'unu basan
kod; `worker/speaker_tap.py::SpeakerState` ve tur-güvenli çözücü) şu an belirsizlikte
kimlik üretmeden çıkıyor. Buna **aday** kavramı ekle: karar Bilinmeyen olsa da
turun pencerelerinden **skor ağırlıklı çoğunluk** bir aday hesapla
(`aday`, `oran`, `ortalama_skor`, `pencere_sayısı`).

Aday KİMLİK DEĞİLDİR: persona swap'i, `_identity_note()`'a kesin isim enjekte etme,
kişisel session'a geçiş **YAPILMAZ**. Aday yalnız "sormaya değer mi" kararında kullanılır.
Yani mevcut güvenlik davranışı aynen korunur.

### 2. Onay sorusu — ne zaman sorulur

Hepsi sağlanırsa sor:

* tur kararı **Bilinmeyen**, ve
* aday var ve **kabul edilen pencerelerin ≥ %60'ı** aynı adayı gösteriyor
  (`SPEAKER_CONFIRM_MIN_RATIO=0.6`), ve
* en az 2 güncel pencere var, ve
* **soğuma süresi doldu**: son sorudan bu yana ≥ `SPEAKER_CONFIRM_COOLDOWN_S=600`
  (10 dk) geçmiş, ve
* bu turda kullanıcı zaten bir soruya cevap veriyor DEĞİL (soru üstüne soru yok).

⚠️ **Sıklık en kritik tasarım kısıtı.** Her turda "sen Ayhan mısın?" diye soran bir ev
asistanı çekilmez — kullanıcı sistemi kapatır. Soğuma + oran eşiği bunun için var.
Şüphede kalırsak SORMAYIZ.

Soru **deterministik harness cümlesi** olsun, modele bırakılmasın (aynı `truth_check`
ilkesi: model NE YAPILACAĞINA karar verir, harness NE OLDUĞUNU söyler). Kısa ve doğal:
`"Pardon, sesinden emin olamadım — sen Ayhan mısın?"`

### 3. Cevabın işlenmesi

* **Evet** → o turun en iyi skorlu **en fazla `SPEAKER_LEARN_MAX_PER_TURN=3`**
  penceresi (her biri ≥ `SPEAKER_MIN_SECONDS`) profile eklenir,
  `source='confirmed-learn'` etiketiyle. `sample_count` ve `updated_at` güncellenir.
  Candan kısa onaylar ("Tamam, artık sesini daha iyi tanıyacağım.").
* **Hayır** → HİÇBİR ŞEY eklenmez. Aday düşer, tur Bilinmeyen kalır. Bu oturumda
  aynı aday için bir daha sorulmaz.
* **Belirsiz/cevapsız** → hiçbir şey eklenmez, sessizce devam.
* Farklı isim söylenirse (**"hayır, ben Havi'yim"**) otomatik merge YOK — mevcut kural
  aynen geçerli, açık kayıt akışına yönlendirilir.
* **YABANCI durumu (Çiğdem vakası).** "Hayır" cevabı çoğu zaman "ben senin bildiğin
  kimse değilim" demektir. Bu durumda Candan kısa bir tanışma teklifi yapabilsin:
  `"Seni tanımıyorum galiba. İstersen sesini kaydedip tanıyabilirim."` Kullanıcı kabul
  ederse MEVCUT açık kayıt akışı başlar (yeni profil; var olan profile ekleme YOK).
  Reddederse konu kapanır, o oturumda bir daha açılmaz.
  Bayrak: `SPEAKER_OFFER_ENROLL_ON_DENY=true`.

### 3b. Yabancı reddi — bu turda ÖLÇ, değiştirme

Çiğdem'in Havi diye kabul edilmesinin sebebi gevşek eşik (asnorm -1.00). Ama doğru
eşiği **tahminle** seçmek yeni hatalar üretir — elimizde Çiğdem'in etiketli kaydı yok.
Bu turda eşiğe **DOKUNMA**. Bunun yerine **gölge ölçüm** ekle: her kabul kararında
`skor` ve "eşik şu olsaydı reddedilirdi" bilgisi log'lansın (ör. -0.5 / 0.0 / +0.5
barajları için). Bir sonraki turda gerçek dağılıma bakıp eşiği VERİYLE seçeriz.

Ayrıca: onay sorusu sorulan turlarda kullanıcının "hayır"ı **etiketli veri demektir** —
o turun skorlarını `speaker_confirm_log` benzeri bir yere yaz (aday, skor, cevap).
Birkaç gün sonra eşik bu veriyle seçilebilir.

### 4. Emniyet ve geri alınabilirlik

* Öğrenilen örnekler `source='confirmed-learn'` ile ayrı etiketli olacak ki tek
  komutla silinebilsinler:
  `DELETE FROM speaker_samples WHERE source='confirmed-learn';`
  Bu, ilk kayıt örneklerine (`voice-enroll`) DOKUNMADAN geri dönüş demek. Raporda yaz.
* Deploy öncesi sunucudaki `speakers.db` **yedeklensin**:
  `data/speakers.db.bak-onay-20260727`.
* Her soru ve her öğrenme **log'lansın** (aday, oran, skor, eklenen pencere sayısı) —
  bir sonraki turda ölçebilelim.
* Tüm davranış env bayrağı arkasında: `SPEAKER_CONFIRM_ASK_ENABLED=true`.
  Bayrak `false` iken sistem BUGÜNKÜ davranışına birebir dönmeli.
* Pasif öğrenme AÇILMIYOR — `SPEAKER_LEARN_ENABLED` false kalır. Öğrenme YALNIZ
  açık onaydan sonra olur.

### 5. Testler

En az şunlar:

* Aday %60 altındaysa soru sorulmaz.
* Soğuma dolmadıysa soru sorulmaz (art arda iki belirsiz tur → tek soru).
* "Evet" → en fazla 3 örnek eklenir, `source='confirmed-learn'`.
* "Hayır" → hiçbir örnek eklenmez, aynı oturumda tekrar sorulmaz.
* Karar KESİN olduğunda (Ayhan tanındı) soru sorulmaz — bugünkü akış bozulmamış.
* Bayrak `false` → davranış bugünküyle aynı.
* **Yabancı senaryosu:** aday Ayhan, cevap "hayır" → hiçbir örnek eklenmez, tanışma
  teklifi bir kez yapılır, ikinci kez yapılmaz.
* Gölge ölçüm log'u kabul kararını DEĞİŞTİRMİYOR (sadece yazıyor).
* **Kullanıcının gerçek `speakers.db`'sine test yazma YOK** — testler geçici DB
  kullanmalı. (27 Tem'de gerçek hafızaya test notu sızdı, tekrarlamasın.)

Tüm takım koşsun (bugün 243 geçiyordu), sayı raporda olsun.

### 6. Deploy

Kullanıcı yetkilendirdi. Sıra: `speakers.db yedek → kod yedek → gönder → sunucuda
import doğrula → systemctl restart candan-worker → journalctl traceback yok → md5`.

* ⚠️ **`pi-service`'e DOKUNMA** (`candan-worker` ona `Requires=` ile bağlı; durdurursan
  worker da düşer ve geri gelmez).
* ⚠️ **`higgs-tts` servisine DOKUNMA** — şu anda başka bir ajan :8809 üzerinden duygu
  atlası sesleri üretiyor, restart onu keser.
* `.env`'e yeni değişkenler eklenecek (yukarıdakiler). Eski `.env` yedeklensin.

### 7. Belgeleme

* `handoff/2026-07-27-kimlik-onay-dongusu.md`: teşhis sayıları, tasarım kararları
  (özellikle "neden soğuma"), env değişkenleri, **tek blok geri dönüş**
  (kod + .env + `confirmed-learn` satırlarının silinmesi).
* `handoff/2026-07-27-DEVIR.md`'ye kısa madde. ⚠️ Bu dosyaya **başka bir ajan da
  yazıyor olabilir** — düzenlemeden önce oku, çakışma olursa kendi bölümünü ekle,
  onunkini silme.
* Tek commit, Türkçe mesaj. Push ETME.

## Rapor (KISA)

* Değişen dosyalar, test sonucu (sayı)
* Deploy adımlarının sonucu
* Tek blok geri dönüş
* Kullanıcının canlıda ne yapması gerektiği (nasıl tetiklenir, ne duyacak)
* Commit hash
