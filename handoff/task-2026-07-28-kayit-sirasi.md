# Görev — KAYIT SIRASI ters + iki dürüstlük hatası

Sen bir worker'sın. İşi kendin yap, kısa rapor ver. Panel/subagent açma.

⚠️ **ÖNCE KONTROL:** `worker/pi_brain.py`'a başka bir ajan (gecikme/stall işi)
dokunuyor olabilir. `git status` temiz değilse ve o iş bitmemişse DUR, raporla.
`worker/truth_check.py` bu görevin; o ajana "dokunma" denildi.

## Canlı kanıt — Aslı denemesi (27 Tem 22:23-22:27)

Kullanıcı üçüncü bir kişiyi (Aslı) kaydetmeye çalıştı. Tam döküm elimizde.

### A) Kayıt sırası TERS

```
22:25:40,131  enroll_speaker sinyali yakalandı (isim='Aslı')
22:25:40,668  kayıt havuzu BOŞ (gördü=0): toplayıcı hiç pencere görmedi
22:25:40,668  çekirdek < 3 → KAYIT YAPILMIYOR
22:25:40,668  enroll REDDEDİLDİ
22:26:31,256  açık kayıt isteği — ses 'Havi' olarak eşleşmiş olsa da toplama BAŞLIYOR
```

Sinyalden redde **537 ms**. Aslı daha konuşmadan karar verilmiş. Toplama ise
**51 saniye SONRA** başlamış.

Yani `enroll_speaker` bir "şimdi karar ver" komutu gibi işliyor; olması gereken
"**toplamayı başlat**" komutu olması. Doğru sıra:

1. istek görülür → **toplama başlar**
2. kaydedilecek kişi konuşurken yeterli pencere birikir (çekirdek ≥ 3 kuralı zaten var)
3. **sonra** karar verilir ve kullanıcıya söylenir

Bugünkü kök sebep düzeltmesi (`current` dolu olsa da toplama başlasın) ÇALIŞIYOR —
22:26:31 satırı onun kanıtı. Ama sıra sorununa dokunmamıştı; o zaman bu kanıt yoktu.

Tasarım notları:
* Model aracı ne zaman çağırırsa çağırsın, karar **veri biriktikten sonra** verilmeli.
  Araç sonucu zaten "Kayıt isteği alındı; sonucu worker bildirecek" diyor — sözleşme
  doğru, uygulama onu tutmuyor.
* Yeterli pencere birikmeden ret verme; **bekle**. Üst sınır olsun (ör. ~20-30 sn ya da
  N tur), dolarsa "sesini alamadım" de.
* Toplama sırasında **kimin konuştuğu** kritik: kayıt edilen kişi konuşmalı. Odada
  başkası konuşursa havuza onun sesi girer. Bugün açtığımız riskin ta kendisi bu —
  en azından log'la, mümkünse "tek konuşmacı" kontrolü koy.

### B) `truth_check` YANLIŞ ALARM (canlıda ilk kez yakalandı)

```
[22:25:24] Candan: "Aslı, seni NOT ALDIM. Şimdi ses kaydınızı alacağım. Lütfen normal
           bir sesle: Bugün kendimi iyi hissediyorum, deyin.
           Aslında bunu kaydetmedim, kusura bakma."
```

Model burada hiçbir şey kaydettiğini İDDİA ETMİYORDU — talimat veriyordu. "Not aldım"
kelime listesine takılmış, düzeltme haksız yere eklenmiş. Sonuç kullanıcı için
kafa karıştırıcı: doğru cümlenin arkasına yanlış bir özür geliyor.

DEVİR §4 madde 3'teki `"ekledim"` yanlış-pozitifi bu; artık canlı örneği var.

* Kelime listesini daraltmak tek başına yetmez (yarın başka bir ifade takılır).
  **Bağlam şartı** ekle: düzeltme yalnız o turda gerçekten bir yazma ARACI çağrılmış
  ve BAŞARISIZ olmuşsa çıksın. Araç hiç çağrılmadıysa metin işaretine bakıp düzeltme
  EKLEME — ya da en azından "araç yok + gelecek zaman kipi" durumunda susma.
* ⚠️ Ama kapsamı daraltırken 27 Tem'de kapatılan gerçek hatayı geri açma:
  `memory_add` guest'te `isError` OLMADAN "kaydedilmedi." dönüyordu, metin işareti
  o yüzden okunuyor. O yolu koru. Testle kilitle.

### C) Model sahip olduğu aracı İNKÂR ediyor

```
[22:24:07] Candan: "Ses tanıma özelliğini şu an için doğrudan bir araç olarak
                    çalıştıramıyorum..."
[22:24:51] Candan: "Ses tanıma özelliğini doğrudan bir araç gibi çalıştıramıyorum
                    ama yeni bir ses kaydetmemiz mümkün..."
```
Halbuki `enroll_speaker` elinde ve 49 saniye sonra onu çağırıyor.

Bu, sabah düzelttiğimiz "yetenek yalanı"nın AYNASI: o zaman olmayan yeteneği var
sanıyordu, şimdi olan yeteneği yok sanıyor. `pi/AGENTS.md` ve
`pi/personas/candan.md`'de kayıt akışının nasıl anlatıldığına bak — model aracı
"doğrudan çalıştıramadığı" bir şey sanıyorsa tarif yanlış ya da eksik. Düzelt,
**prompt satır sayısını şişirme**.

## Ayrıca kayda geçsin (bu turda ÇÖZÜLMEYECEK)

Kullanıcı (Ayhan) bu oturumda **kendi turlarında da "Havi" sanıldı** (22:24:47 →
Candan "Selam Havi!" dedi). Aslı da baştan sona Havi sanıldı. Yani iki mevcut profil
birbirine karışıyor ve profili olmayan üçüncü kişi de onlara yapışıyor. Eşik işi
gölge ölçüm verisi birikince ayrı turda yapılacak — **bu turda eşiğe DOKUNMA**.

## Sınırlar

* Ölç, tahmin etme. Değişiklikten sonra kayıt akışını uçtan uca testle doğrula.
* `higgs-tts`, `pi-service`, `candan-brain`'e DOKUNMA. Deploy'da yalnız `candan-worker`.
* Kullanıcının gerçek `speakers.db`'sine test yazma YOK; testler geçici DB kullansın.
* Deploy öncesi `speakers.db` yedeklensin.

## Belgeleme

`handoff/2026-07-28-kayit-sirasi.md`: üç maddenin her biri, öncesi/sonrası akış,
tek blok geri dönüş. DEVİR §4 madde 3 (`"ekledim"` yanlış-pozitifi) artık canlı
örnekli — güncelle, başka ajanların maddelerini silme. Tek commit, Türkçe, push YOK.

## Rapor (KISA)

* Kayıt akışının yeni sırası, üst sınır kaç saniye/tur
* truth_check daralması: hangi şart eklendi, eski hata nasıl korundu (test adı)
* Prompt'ta kaç satır değişti
* Test sayısı, deploy sonucu, tek blok geri dönüş, commit hash
