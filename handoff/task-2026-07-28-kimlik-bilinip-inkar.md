# Görev — KİMLİK DOĞRULANDI AMA İNKÂR EDİLDİ (kök sebep analizi)

Sen bir worker'sın. İşi kendin yap, kısa rapor ver. Panel/subagent açma.

**Bu görev SALT-OKUNUR analizdir.** Kod DEĞİŞTİRME, deploy ETME, sunucuya DOKUNMA.
Çıktın: kök sebep + önerilen düzeltme noktası. Uygulama ayrı bir turda yapılacak.

## Önce codebase-memory

Kod aramaya Grep/Glob ile BAŞLAMA. Önce `codebase-memory-mcp` araçlarını kullan:
`search_graph`, `trace_path`, `get_code_snippet`, `get_architecture`, `search_code`.
Depo indeksli değilse önce `index_repository` çalıştır. Grep/Glob ancak bundan sonra.

## Canlı kanıt — 28 Tem 14:17

```
[14:17:07] Bilinmeyen: Can'dan günaydın.
[14:17:18] Bilinmeyen: Sesimi duyuyor musunuz?
[14:17:29] Candan: ... şu an seni sistemimde tanımıyorum. İstersen sesini tanıtmak
                   için kayıt sürecini başlatabiliriz.
[14:17:47] Ayhan: Başlatalım ama önce sesime bak bakalım ben kimmişim tanıyor musun
                  sonra devam edelim.                    ← İSMİNİ SÖYLEMEDİ
[14:17:51] Candan: Selam Ayhan!  Şu an sesini teknik olarak sisteme tanıtmadığın için
                   seni doğrudan tanımıyorum. ...        ← İSMİ BİLİYOR AMA İNKÂR EDİYOR
[14:18:06] Ayhan: Beni tanımadığına emin misin?
[14:18:09] Candan: Evet Ayhan, eminim. Sistemimdeki ses kayıtlarıyla şu anki sesin
                   eşleşmiyor.                           ← YANLIŞ OLGU İDDİASI
```

## Neden bu YENİ bir hata

`task-2026-07-28-isim-alma-dongusu.md`'deki hata: kısa cevap → tek pencere →
onay HİÇ gerçekleşmiyor → döngü.

Buradaki hata FARKLI ve onay **BAŞARILI**:
* 14:17:07 ve 14:17:18'de etiket `Bilinmeyen`.
* 14:17:47'de etiket **`Ayhan`**'a döndü. Kullanıcı o turda adını SÖYLEMEDİ.
  Dolayısıyla etiketin tek kaynağı **ses eşleşmesi**. Uzun cümle → yeterli pencere → onay.
* Yani Ayhan `speakers.db`'de KAYITLI ve sesi EŞLEŞTİ.
* Buna rağmen LLM "tanımıyorum" ve "sesin eşleşmiyor" diyor.

İsim LLM'e ULAŞMIŞ ("Selam Ayhan!"). Demek ki context'te hem isim var, hem de
"bu kişi tanınmıyor/kayıtlı değil" anlamına gelen ikinci bir sinyal var. Çelişki orada.

## Cevaplanacak sorular (ölç/oku, tahmin etme)

1. **Etiketi kim üretiyor?** Transkriptteki `Bilinmeyen` / `Ayhan` etiketini yazan kod
   yolu hangisi? (`worker/` içinde speaker turn kararı / diarization / speaker-ID)
2. **LLM'e ne gidiyor?** Aynı turda LLM prompt/context'ine kimlik hangi alan(lar)la
   enjekte ediliyor? İsim ve "tanındı mı" bayrağı AYNI kaynaktan mı geliyor?
3. **Çelişkinin tam yeri hangisi?** Muhtemel adaylar — hangisi olduğunu KANITLA:
   a. Etiket yolu ile prompt yolu farklı state okuyor (biri güncel, biri bayat).
   b. "tanındı" (voice match) ile "kayıtlı/enrolled" ayrı bayraklar; prompt yanlış
      olanı okuyor — kişi eşleşse bile `enrolled=false` görünüyor.
   c. Kimlik turun BAŞINDA enjekte ediliyor, eşleşme tur ORTASINDA oluşuyor →
      o tur bayat context ile cevaplanıyor.
   d. Prompt'ta "tanımıyorsan tanımadığını söyle" kuralı, isim dolu olsa bile
      tetikleniyor.
4. **Log kanıtı:** 14:17-14:18 penceresinde `speaker turn kararı`, `kimlik onayı`
   satırları ne diyor? `logs/` altına bak. Etiketin `Ayhan`a döndüğü an ne loglanmış?

## Sınırlar

* Kod DEĞİŞTİRME. Deploy YOK. `systemctl` YOK. Canlı `.25` ve oracle-stage'e DOKUNMA.
* Kullanıcının gerçek `speakers.db`'sine YAZMA. Okumak serbest.
* Görsel/canlı test YAPMA — kullanıcı kendi yapar.
* Test çalıştırman gerekirse sadece mevcut suite'i koştur (402 test geçiyordu).

## Rapor (KISA — madde madde, 15 satırı geçme)

* Etiketi üreten kod yolu (`dosya:satır`)
* LLM context'ine kimliği enjekte eden kod yolu (`dosya:satır`)
* **Kök sebep:** yukarıdaki a/b/c/d'den hangisi — ve kanıtı
* Log'da 14:17-14:18 ne görünüyor
* Önerilen düzeltme noktası (tek cümle, kodu YAZMA)
* Bu hata `isim-alma-dongusu` göreviyle aynı yerde mi düzeltilmeli, ayrı mı
