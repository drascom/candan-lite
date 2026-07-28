# ARAŞTIRMA E — Güvenlik sınıfı biyometrik ses tanıma: bize ne yarar?

Sen bir worker'sın. İşi kendin yap, delege etme. Panel açma.

**ARAŞTIRMA / SALT-OKUNUR.** Kod yazma, deploy etme, model indirme, canlıya yazma YOK.
Dış kaynak araştırması serbest (WebSearch/WebFetch).

## Amaç

Kullanıcı: *"İleri düzey güvenlik sistemlerinde kullanılan biyometrik ses tanımayı
araştırsak, neler kullanılmış bakalım."*

Bu bir literatür dökümü DEĞİL. Soru şu: **bankacılık/çağrı merkezi/adli sınıf ses
biyometrisinde kullanılan hangi teknik bizim evdeki asistanda işe yarar?**

## ⚠️ Çalışma noktası çevirisi — bunu her öneride uygula

Güvenlik sistemleri **yanlış kabulü (false accept)** sıfırlamak için tasarlanır;
şüphede REDDEDER. Bizim önceliğimiz TAM TERSİ: kullanıcı evde, tehdit modeli yok,
**yanlış ret (false reject) pahalı**, yanlış kabul ucuz (tek maliyeti hafızaya yanlış
atfetme — o da ayrıca korunuyor).

Dolayısıyla: **tekniği al, çalışma noktasını alma.** Bir yöntem "güvenlik için şart"
diye önerilmişse bizde gereksiz olabilir; tersine, onların "yeterince güvenli değil"
diye elediği bir yöntem bizim için ideal olabilir. Her maddede bunu açıkça değerlendir.

## Bizim durumumuz (veri — tekrar araştırma)

* Canlı model: `wespeaker_en_voxceleb_resnet34_LM.onnx`, **256d, İngilizce VoxCeleb**,
  tipik ≥2-3 sn segment için eğitilmiş. Biz **Türkçe** konuşuyoruz, ilk pencere 1.0 sn.
* Ölçüm: 790 kararın %70.5'i "Bilinmeyen". Eşik sorunu YOK (kabul medyanı +4.17,
  red −3.71, örtüşme yok). Sorun kısa/az veri.
* Kayıtlı kişi sayısı: 2 (Ayhan 21 örnek, Havi 6 örnek). Aile ölçeği, 3-5 kişi.
* Donanım: 6 CPU çekirdeği **boş** (yük 0.02), 14 GB RAM boş.
  ⚠️ GPU RTX 3090 ama **VRAM 21.2/24 GB DOLU** — ikinci büyük model SIĞMAZ. CPU düşün.
* AS-norm zaten var (cohort 120×256), kalite kapısı sadece RMS.

## Araştır

1. **Daha iyi gömme modeli — EN SOMUT ÇIKTI.** VoxCeleb-İngilizce ResNet34'ün yerine
   ne konabilir? Bak: ECAPA-TDNN, ReDimNet, NeMo TitaNet, WavLM/wav2vec2 tabanlı SV,
   çok dilli (multilingual) eğitilmiş modeller. Her aday için:
   - Türkçe/çok dilli veri görmüş mü? Dil uyuşmazlığı ne kadar zarar veriyor?
   - **Kısa segment (1-2 sn) performansı** — bizim ölü bölgemiz tam burası
   - ONNX var mı, CPU'da gerçek zamanlı koşar mı, kaç MB
   - Kayıtlı 2 kişiyi yeniden kaydetmek gerekir mi (embedding boyutu değişirse EVET)
2. **Metne bağlı (text-dependent) doğrulama.** Güvenlik sistemleri sabit parola cümlesi
   kullanıyor ("sesim şifremdir") ve kısa segmentte çok daha iyi sonuç alıyor. Bizde
   karşılığı olur mu — örneğin uyandırma kelimesi ("Candan") her turda zaten söyleniyorsa
   o sabit parçadan metne-bağlı doğrulama yapılabilir mi?
3. **Kalite ölçüleri ve kalibrasyon.** Güvenlik sınıfı sistemler segment kalitesini
   (SNR, süre, konuşma oranı) skora yan-bilgi olarak katıyor. Bizde RMS'ten ibaret.
4. **Sürekli doğrulama (continuous authentication).** Oturum boyunca kimliği sürekli
   tazeleme — bizim "iki katmanlı" tasarımımızla (Araştırma D) örtüşüyor mu?
5. **Çoklu model füzyonu.** İki farklı gömme modelinin skorunu birleştirmek. CPU bütçemiz
   buna yeter mi, kazanç değer mi?
6. **Kayıt (enrollment) standartları.** Güvenlik sistemleri kaç saniye kayıt istiyor,
   kaç farklı oturumdan? Bizde Ayhan 21, Havi 6 örnek — bu yeterli mi, az mı?
7. **Sahtecilik önleme (anti-spoofing / liveness, ASVspoof).** Bunu araştır ama
   **muhtemelen bizde gereksiz** — ev ortamı, tehdit yok. Değerlendir ve büyük ihtimalle
   "atla" de. Maliyetini boşuna ödemeyelim.

## Kısıtlar

* Model İNDİRME, kurma, çalıştırma YOK. Sadece araştır ve öner.
* Kod yazma, deploy, canlıya yazma YOK.
* Kişisel ses verisine dokunma.
* **Kaynak göster.** Ezberden performans rakamı verme; iddia varsa linki olsun.
  Emin değilsen "doğrulanmadı" yaz.
* Bizim koşullarımızda çalışmayacak bir şeyi "literatürde var" diye önerme.
  VRAM'e sığmayan, CPU'da gerçek zamanlı koşmayan aday elenir.

## Rapor (KISA — 25 satırı geçme)

* **Model adayları tablosu:** ad / boyut / çok dilli mi / kısa segment başarısı /
  ONNX+CPU uygun mu / yeniden kayıt gerekir mi / kaynak linki
* **En iyi 1 model önerisi** ve neden — mevcut modele göre beklenen kazanç
* Metne-bağlı doğrulama bizde uygulanabilir mi (EVET/HAYIR + gerekçe)
* Benimsemeye değer diğer 2 teknik
* **Elenenler ve neden** (özellikle anti-spoofing)
* Bilmediklerin / doğrulanmamış rakamlar
