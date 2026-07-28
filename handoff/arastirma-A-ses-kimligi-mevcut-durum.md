# ARAŞTIRMA A — Ses kimliği: mevcut durum ve GERÇEK başarısızlık oranı

Sen bir worker'sın. İşi kendin yap, delege etme. Panel açma.

**SALT-OKUNUR.** Kod değiştirme, deploy etme, sunucuya YAZMA. Okuma serbest.
Bu bir ölçüm görevi — çözüm önerme, **mevcut durumu sayılarla ortaya koy**.

## Önce codebase-memory

`codebase-memory-mcp` ile başla: `search_graph`, `trace_path`, `get_code_snippet`,
`search_code`. İndeks yoksa `index_repository`. Grep/Glob ancak bundan sonra.

## Neden bu görev

Kullanıcı: *"İki konuşmada bir sesi tanıyamayacaksa olmaz bu iş."* Şu an kimlik
her turda sıfırdan hesaplanıyor; kısa turlarda düşüyor. Ama **gerçek başarısızlık
oranını kimse ölçmedi.** Önce onu bil.

## Cevaplanacaklar — SAYIYLA

1. **Boru hattını çıkar:** ses → pencere → embedding (campplus.onnx) → skor → karar.
   Her aşamanın parametreleri ne, nerede tanımlı (`dosya:satır`)?
   - Pencere uzunluğu / hop, minimum ses süresi
   - `SPEAKER_CONFIRM_HITS`, eşik değerleri, skor metriği (kosinüs? mesafe?)
   - `resolve_turn` (`worker/speaker_tap.py:221-300`) kararı tam olarak nasıl veriyor
2. **Skor dağılımı:** log'lardan (`logs/`, canlı `.25`'te `journalctl`, transcript'ler)
   mümkün olduğunca çok `speaker turn kararı` / `kimlik onayı` satırı topla.
   Çıkar: doğru kişi skorları vs yanlış/bilinmeyen skorları dağılımı. **Örtüşüyor mu?**
   Eşik iyi bir yerde mi, yoksa dağılımlar iç içe mi?
3. **Süre-başarı ilişkisi:** turun ses süresi (veya pencere sayısı) ile tanıma başarısı
   arasındaki ilişkiyi tablola. Kaç saniyenin altında çöküyor? Kullanıcı "iki saniye"
   diyor — DOĞRULA veya çürüt.
4. **28 Tem 16:23-16:27 oturumu:** transcript'te etiket tur tur `Ayhan`↔`Bilinmeyen`
   zıplıyor. O turların her biri için süre + skor + karar sebebini çıkar. Zıplamanın
   deseni ne?
5. **speakers.db içeriği:** kaç kişi kayıtlı, kişi başına kaç embedding/örnek,
   ne zaman kaydedilmişler, kayıt ses süreleri ne. (**SADECE OKU.** Yazma, kopyalama
   yaparken kişisel ses verisini repoya çıkarma.)
6. **campplus modeli:** hangi model, hangi örnekleme hızı/pencere için eğitilmiş,
   beklenen minimum ses süresi nedir? Kullandığımız şekil modelin varsayımlarıyla
   uyuşuyor mu?

## Kısıtlar

* Canlı `.25`'e **YAZMA**. `journalctl`, `cat`, `sqlite3 ... "SELECT"` gibi salt-okuma
  sorguları serbest. `systemctl` YOK.
* Kişisel ses kayıtlarını (`*.opus`, `*.wav`) repoya kopyalama, dinleme, taşıma.
  Depo PUBLIC.
* Çözüm/mimari önerme — o Araştırma B'nin işi. Sen **ölçersin**.

## Rapor (KISA — 20 satırı geçme, tablo serbest)

* Boru hattı şeması, her parametre `dosya:satır`
* Skor dağılımı: doğru vs yanlış, örtüşme var mı
* Süre-başarı tablosu — hangi eşiğin altında çöküyor
* 16:23-16:27 oturumunun tur tur dökümü
* speakers.db özeti (kişi sayısı, örnek sayısı — ses verisi DEĞİL)
* **Ölçemediklerin** — açıkça listele, uydurma
