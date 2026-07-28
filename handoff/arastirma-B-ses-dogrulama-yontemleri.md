# ARAŞTIRMA B — Konuşmalar arası biriktirme: yöntem araştırması

Sen bir worker'sın. İşi kendin yap, delege etme. Panel açma.

**SALT-OKUNUR / ARAŞTIRMA.** Kod yazma, deploy etme. Çıktın bir **yöntem raporu**.

## Neden bu görev

Kullanıcı: *"Her konuşmayı o anlık denemek her zaman güvenilir sonuç vermiyor.
Konuşmalar arası verileri toplayıp sentezleyebilecek bir yöntem geliştirelim."*

Şu anki tasarım: her tur bağımsız, sıfırdan karar. Kısa turda veri yetmiyor → düşüyor.
Aranan: **zaman içinde biriken, kendini güçlendiren** bir kimlik modeli.

## Araştır (dış kaynak kullan — WebSearch/WebFetch serbest)

Konu: kısa konuşmalarda konuşmacı doğrulama (speaker verification) ve oturumlar arası
uyarlama. Bak:

1. **Skor kalibrasyonu ve normalizasyon** — AS-norm / S-norm / T-norm. Ham kosinüs
   skoru yerine kalibre edilmiş skor eşiği neden daha kararlı?
2. **Kayıt zenginleştirme (enrollment augmentation / incremental enrollment)** —
   güvenle tanınan turlardan embedding biriktirip kişinin merkezini (centroid)
   güncelleme. Kayma (drift) ve zehirlenme nasıl önlenir?
3. **Çoklu örnek / kalite kapısı** — kısa veya gürültülü segmenti karara HİÇ sokmama;
   SNR / süre / konuşma oranı ile ön eleme.
4. **Kanıt biriktirme** — tek turda karar vermek yerine oturum boyunca log-likelihood
   ratio biriktirme (sıralı karar / SPRT benzeri). Kısa turlar tek başına yetmez ama
   TOPLAMI yeter.
5. **Diarization + kimlik ayrımı** — "kaç kişi konuşuyor" ile "kim konuşuyor" ayrı
   problemler. Oturum içi kümeleme (aynı ses = aynı küme) kimlik bilinmeden de yapılabilir;
   sonra kümeye tek bir kimlik atanır. Bu, kısa turları uzun turların kanıtıyla kurtarır.
6. **Test-time adaptation** — oturum sırasında modeli/eşiği o ortama uydurma.

Her yöntem için: **bizim koşullarımızda uygulanabilir mi?** Koşullar: campplus.onnx
embedding modeli, CPU/GPU bütçesi sınırlı, gerçek zamanlı sesli asistan, ev ortamı,
az sayıda kayıtlı kişi (aile), gürültülü/kısa cümleler, Türkçe.

## Bizim bağlamımız

* Mevcut boru hattı ve ölçümler: Araştırma A'da çıkıyor. Onun raporunu bekleme,
  paralel çalış; ama koda bakarak mevcut yapıyı anla (`worker/speaker_tap.py`,
  `worker/agent.py`, campplus kullanımı).
* Tehdit modeli: **YOK denecek kadar zayıf.** Ev, aile. Kullanıcı açıkça dedi:
  *"Güvenlikten ziyade zaten bunu evde kullanıyoruz. Diğer insanların erişeceği bir
  tehlike yaratmaz."* Yani yanlış kabul (false accept) maliyeti DÜŞÜK,
  yanlış ret (false reject) maliyeti YÜKSEK. Eşikleri buna göre değerlendir.
  ⚠️ AMA: yanlış kabul, **hafızaya yanlış kişi adına yazma** riski üretir — bu
  Araştırma C'nin konusu, sen sadece skor tarafına odaklan ve bu takası NOT DÜŞ.

## İstenen çıktı

Öneri **sırala**, hepsini savunma. Her biri için:
* Ne yapar, bizde nasıl karşılık bulur (`dosya:satır` seviyesinde nereye girer)
* Beklenen kazanç — hangi başarısızlık türünü çözer
* Maliyet: CPU/GPU, gecikme, karmaşıklık
* Risk: neyi bozabilir
* **Ölçüm planı:** bu yöntemin işe yaradığını nasıl kanıtlarız (hangi metrik, hangi veri)

En az bir "ucuz ve hızlı kazanç", en az bir "doğru ama büyük" seçenek olsun.

## Kısıtlar

* Kod YAZMA. Deploy YOK. Canlı `.25`'e yazma.
* Kişisel ses verisini repoya çıkarma, dinleme.
* Kaynak gösterdiğin dış makale/yöntem varsa **linkini ver**; ezberden iddia etme.
* Bizim koşullarımızda çalışmayacak bir yöntemi "literatürde var" diye önerme —
  uygulanabilirliği açıkça değerlendir.

## Rapor (KISA — 25 satırı geçme)

* Sıralı öneri listesi (yukarıdaki alanlarla)
* En çok işe yarayacak İKİ yöntem ve neden
* Ölçüm planı — "düzeldi" demeyi neye bağlıyoruz
* Bilmediklerin / doğrulanmamış varsayımların
