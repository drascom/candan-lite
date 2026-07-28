# Görev — KAYIT: isim alma adımı döngüye giriyor

Sen bir worker'sın. İşi kendin yap, kısa rapor ver. Panel/subagent açma.

**Yarının ilk işi.** 28 Tem 01:09-01:12 canlı testinde bulundu.

## Canlı kanıt

```
[01:09:43] Ayhan : Kimlik döngüsünü test etmek istiyorum. Yeni bir ses tanıma kaydı açar mısın?
[01:09:46] Candan: Yeni bir ses tanıma kaydı açabilirim Ayhan. Kimin sesini tanıtmak istiyorsun?
                   İstersen önce adını söyleyebilirsin, sonra kayıt sürecini başlatalım.
[01:09:58] (Bilinmeyen): Baba.
[01:09:59] Candan: Pardon, sesinizi henüz tanıyamadım. Adınızı tek kelime olarak söyler misiniz?
[01:10:07] (Bilinmeyen): Baba.
[01:10:08] Candan: Pardon, sesinizi henüz tanıyamadım. Adınızı tek kelime olarak söyler misiniz?   ← AYNI CÜMLE
[01:10:17] (Bilinmeyen): Adım Ayhan.
[01:10:18] Candan: Ayhan dediğini duydum ama ses kimliğini güvenle doğrulayamadım.
```

Worker log'u, aynı pencere:
```
01:09:58  speaker turn kararı: Bilinmeyen (sebep=bu dönüşte güvenli ses penceresi yok, kabul=0/1)
01:10:07  speaker turn kararı: Bilinmeyen (sebep=yetersiz ardışık onay (1/2), kabul=1/1)
01:10:07  kimlik onayı: soru YOK (aday=Ayhan oran=1.00 pencere=1 embed=1)
```

**`enroll_speaker` o üç dakikada HİÇ ÇAĞRILMADI** — tek bir "enroll_speaker sinyali
yakalandı" satırı yok. Kayıt akışı hiç başlamadı, konuşma sohbet olarak akıp gitti.

## Kök sebep — tasarım çelişkisi

Akış **tek kelimelik isim** istiyor. Tek kelime ≈ 1 saniye ses ≈ **tek pencere**.
Kimlik kararı ise **en az iki ardışık pencere** istiyor (`SPEAKER_CONFIRM_HITS=2`).
Yani sistem, kendi kuralının asla karşılayamayacağı bir cevap talep ediyor:

> tek kelime iste → tek pencere gelir → "tanıyamadım" de → tekrar tek kelime iste → …

27 Tem'de kayıt sırasını düzelttik ("karar verme, toplamayı başlat") ama **isim alma
adımı** o düzeltmenin dışında kaldı. Aynı hatanın kardeşi.

## Çözüm yönü (uygulamadan önce OKU ve ölç)

Asıl mesele şu ayrımı yapmak: **isim METİNDEN gelir, ses KİMLİĞİ başka turlardan.**
İkisini aynı tek-kelimelik cevaptan çıkarmaya çalışmak çelişkinin kaynağı.

* İsim alma adımı **tek kelime İSTEMESİN**; doğal bir cümle istesin
  ("Adını söyleyip birkaç kelime daha konuşur musun?"). Uzun cevap hem ismi hem
  yeterli pencereyi verir.
* İsim zaten kısa geldiyse (kullanıcı "Baba" dedi) **ismi kabul et**, sesi
  sonraki turlardan toplamaya devam et — 27 Tem'de kurulan "karar erteleme"
  mekanizması tam bunun için var, isim adımına da bağlanmalı.
* **Aynı cümleyi iki kez arka arkaya kurma.** İkinci denemede farklı bir şey söylensin
  (ne istendiğini açıklayan). Aynı cümlenin tekrarı kullanıcıya "sistem kilitlendi"
  hissi veriyor — canlıda tam olarak bu oldu.
* Döngü sayacı: N denemede ilerleme yoksa akış kendini kapatsın ve durumu söylesin.

⚠️ **Ölç, tahmin etme:** tek kelimelik bir cevabın kaç pencere ürettiğini ve kaç saniye
ses gerektiğini ÖLÇ (log'da `pencere=` alanı var). Eşiği körlemesine düşürme —
`SPEAKER_CONFIRM_HITS=2` yanlış tanımayı engellemek için konmuştu.

## İkinci madde — yetenek inkârının yeni kalıbı

```
[01:11:07] Candan: "şu anki teknik kısıtlamalarım nedeniyle sesinin biyometrik analizini
                    yapıp seni tanıma yeteneğine sahip değilim"
```
27 Tem'deki inkâr düzeltmesi TUTTU — 01:09:46'da doğru cevap verdi ("açabilirim").
İnkâr ancak **üst üste başarısızlıktan sonra** geldi: model, akış tıkanınca
"demek ki yapamıyorum" sonucuna varıyor. Yani asıl ilaç 1. maddeyi düzeltmek.
Yine de prompt'ta "tanıma/analiz yeteneğim yok" kalıbı da kapatılsın —
**prompt satır sayısını şişirmeden**.

## Bağlam: tanıma tarafı ÇALIŞIYOR

Aynı oturumda uzun cümlelerde skorlar 2.3 / 3.3 / 4.5, kabul 5/5, 6/6, 3/3.
Sorun tanıma kalitesinde değil, **kısa cevaplarda**. Eşiklere dokunma.

## Sınırlar

* Deploy: yalnız `candan-worker` restart; prompt değiştiyse **`systemctl reload pi-service`**
  (restart DEĞİL). `higgs-tts`, `candan-brain`'e dokunma.
* Kullanıcının gerçek `speakers.db`'sine test yazma yok; geçici DB kullan.
* Şu an **402 test** geçiyor.

## Rapor (KISA)

* Tek kelimelik cevap kaç pencere üretiyor (ölçüm)
* İsim alma adımının yeni akışı
* Döngü koruması
* Test sayısı, deploy sonucu, tek blok geri dönüş, commit hash
