# ARAŞTIRMA D — İki katmanlı kimlik: hızlı yol + arka plan yeniden değerlendirme

Sen bir worker'sın. İşi kendin yap, delege etme. Panel açma.

**SALT-OKUNUR / TASARIM.** Kod yazma, deploy etme, canlı `.25`'e YAZMA (okuma serbest).
Çıktın bir tasarım + fizibilite raporu.

## Önce codebase-memory

`codebase-memory-mcp` ile başla (`search_graph`, `trace_path`, `get_code_snippet`,
`search_code`). İndeks yoksa `index_repository`. Grep/Glob sonra.

## Bağlam — önceki üç araştırmanın sonucu

Bunları tekrar araştırma, VERİ olarak kullan:

* **A (ölçüm):** 790 kararın %70.5'i "Bilinmeyen". Sebep eşik DEĞİL — kabul medyanı
  +4.17, red medyanı −3.71, örtüşme yok. Sebep **tur uzunluğu**: 0 pencere → %0 isim,
  1 pencere → %7, 2 pencere → %59, ≥5 → %82. Turların %38'i ölü bölgede.
  Canlı model `wespeaker_en_voxceleb_resnet34_LM.onnx` (256d, İngilizce VoxCeleb,
  ≥2-3 sn için eğitilmiş) — ilk pencere 1.0 sn, modelin varsayımının ALTINDA.
  Mimari hata: `TURN_MAX_SECONDS=8` — 16:25:05'te 15 pencere hepsi Ayhan ort. 5.69
  iken tavana takılıp KOMPLE atıldı.
* **B (yöntem):** kanıt üretiliyor ama atılıyor. `speaker_id.identify()`
  (`speaker_id.py:232-274`) eşik altı skoru atıp `None` dönüyor; `resolve_turn`
  (`speaker_tap.py:221-300`) sadece "2 ardışık" sayıyor; turlar arası tek hafıza
  12 sn continuity (`speaker_tap.py:122-127`).
* **C (hafıza):** `memory_add` kimliği turun ses etiketiyle AYNI değişkenden okuyor
  (`pi_brain.py:3505` → `speaker_state.current`). Belirsizse not sessizce kayboluyor.
  Öneri: `pending/` spool + sor, oturum kilidi YOK.

## Kullanıcının fikri — tasarımın ÇEKİRDEĞİ

Birebir: *"Devam eden bir konuşmada en son gelen sesi tanıyamazsak, bu sesi bir önceki,
iki önceki, üç önceki konuşmayla karşılaştırıp yakınlık oranını ölçsek daha doğru tanım
yapmış olmaz mıyız? Referans değer sessiz ortamda kaydolmuş olabilir ama konuşma
gürültülü bir yerde geçiyor olabilir, ya da o an daha kısık sesle konuşuyor olabilir.
Referans tek bir duygu durumunu anlatıyorken akıcı konuşma o anki ruh haline göre
değişen ses konfigürasyonunu da tutar."*

Yani: **sabit referansla değil, aynı oturumun yakın geçmişiyle karşılaştır.** İki taraf
aynı kanal/ortam/ruh halini paylaştığı için koşul farkı denklemden düşer.

Doğrulayan kanıt (A'dan, 16:25 oturumu): 16:25:05'te 15 pencere hepsi Ayhan ort. 5.69.
Hemen sonraki 6 tur tek pencereli (2.04 / 4.82 / 0.44 / 0.44 / 4.38 / 4.38) ve HEPSİ
"1/2" ile reddedildi — oysa 5 saniye önceki güçlü kanıt yanı başlarındaydı.

## Kullanıcının ikinci girdisi — kaynak serbest

*"Hâlâ hazırda yeterli işlem gücümüz var. Gerekirse altta bir arka plan prosesi
çalıştırıp bunu sürekli olarak yapabiliriz."*

Ölçülen gerçek (28 Tem 16:47, canlı): 6 çekirdek, **yük ort. 0.02** (boş), 14 GB RAM
kullanılabilir. GPU RTX 3090 **kullanım %0 ama VRAM 21.2/24 GB DOLU** (llama-server +
higgs-tts). ⚠️ **İkinci büyük model VRAM'e SIĞMAZ.** Tasarımın CPU'da koşmalı.

## Tasarla

### 1. İki katman
* **Hızlı yol:** gerçek zamanlı, gecikmesiz, temkinli. Konuşmanın akması için yeter.
* **Yavaş yol:** arka planda sürekli. Oturumun biriken pencerelerini yeniden kümeler,
  komşu-karşılaştırması yapar, kararları **geriye dönük düzeltir**.

Sınırı net çiz: hangi karar hangi katmanda, hızlı yol yavaş yolu ne kadar bekler
(cevap: HİÇ — kullanıcı beklememelidir).

### 2. Geriye dönük düzeltmenin sonuçları
* Bekleyen hafıza notu (`pending/`, C'nin tasarımı) çözülünce **otomatik** atansın.
* Kullanıcıya ne söylenir? Sessizce düzeltmek mi, "pardon o Ayhan'mış" demek mi?
  Hangi durumda hangisi — öner.
* Zaten söylenmiş yanlış hitap ("Bilinmeyen'e" davranış) geri alınabilir mi?

### 3. Çapa ve zincir güvenliği
* Zincir yalnız **yüksek güvenli** turdan başlasın (çok pencereli, yüksek skorlu).
  Tek pencereli tur ÇAPA OLAMAZ.
* Komşuya yakınlık tek başına yetmesin: aday **başka bir kayıtlı kişiye daha yakın
  olmamalı**. Çelişkide zincir kopsun.
* Odaya ikinci kişi girme senaryosunu açıkça ele al — ev ortamında sık.

### 4. FİZİBİLİTE — bunu mutlaka cevapla
* **Pencere embedding'leri şu an saklanıyor mu?** (`_log_shadow`
  `speaker_id.py:276-296`, `speaker_confirm_log.jsonl`, başka yer?) Saklanmıyorsa
  yavaş yol için tutulmaları gerekir — nerede, ne kadar süre, hangi boyutta?
* ⚠️ **Embedding biyometrik veridir.** Sunucuda kalsın; repoya, log'a, transcript'e
  ÇIKMASIN. `.gitignore` referans wav/pt'yi zaten biyometrik sayıyor — aynı muamele.
* **Çevrimdışı doğrulama mümkün mü?** Geçmiş oturumların pencere skorları/embedding'leri
  varsa, bu yöntemin "kaç turu kurtarırdı"nı canlıya hiç dokunmadan hesaplayabilir miyiz?
  Mümkünse nasıl — somut plan. Değilse önce neyin loglanması gerekir?
* CPU maliyeti: oturum başına kaç pencere birikir, kümeleme ne kadar sürer?

### 5. Ölçüm
A'nın ölçemediği şey **yanlış tanıma oranı** — turların kimin konuştuğu etiketli değil.
Bu tasarım kaçırmayı düşürüp yanlış tanımayı yükseltebilir. Nasıl ölçeriz? Etiketli
veri nasıl toplanır (kullanıcıyı yormadan)?

## Kısıtlar

* Kod YAZMA. Deploy YOK. Canlı `.25`'e yazma (okuma serbest).
* Kişisel ses kaydını açma/kopyalama/dinleme. Embedding içeriğini rapora yazma.
* VRAM'e yeni model koymayı önerme (yer yok) — CPU tasarımı yap.
* Gerçek `speakers.db`'ye ve `memory/`'ye YAZMA.

## Rapor (KISA — 25 satırı geçme)

* İki katmanın sınırı: hangi karar nerede
* Zincir/çapa kuralları ve ikinci-kişi senaryosunun cevabı
* Geriye dönük düzeltme: hafıza + kullanıcıya ne denir
* **Fizibilite:** embedding saklanıyor mu, çevrimdışı doğrulama mümkün mü (EVET/HAYIR + plan)
* CPU maliyeti tahmini
* Ölçüm planı ve etiketli veri toplama fikri
* Uygulama sırası: en küçük çalışan ilk adım ne
