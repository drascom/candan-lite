# Speaker-ID: konuşma dönüşü ile kimlik eşleştirme hatası

## Durum

Canlı worker: `root@192.168.0.25:/opt/candan-lite`, servis
`candan-worker.service`. Son üretim kodu 20:08'de deploy edildi.

Havi 20:45:31'de başarıyla kaydedildi:

- `speakers`: Ayhan (14 `voice-enroll` örneği), Havi (5 `voice-enroll` örneği)
- Havi için 6 ifade WAV + JSON oluştu:
  `worker/data/expression-samples/havi/20260718T204554...204718/`
- Yerel arşiv güncel: `worker/data/speakers.db` ve
  `worker/data/expression-samples/havi/` (SHA-256 ile sunucuya karşı doğrulandı).

## Gözlenen hata ve kanıt

`20:48` testinde Havi önce konuştu, ardından Ayhan "Peki ben kimim?" dedi.
Sistem Havi yanıtını verdi. Bu, Ayhan'ın sesinin Havi diye sınıflandırılması
değildi; geçmiş konuşmacı durumunun yeni dönüş için kullanılmasıydı.

Canlı günlük sırası (`AJ_XKLsb6LuGGCb`):

1. 20:48:10 ve 20:48:11: Havi sesi yanlışlıkla iki kez Ayhan kabul edildi
   (`asnorm` 1.485, 1.864). Mevcut iki-onay kuralı Ayhan durumunu açtı.
2. 20:48:12: Havi kabul edildi (11.643), fakat 20:48:13'te Pi zaten
   `candan/candan -> ayhan/ayhan` swap'ini yapmıştı; ilk yanıt Ayhan bağlamından
   üretildi.
3. 20:48:34 ve 20:48:35: Havi iki kez doğru tanındı (8.880, 12.197) ve Havi
   bağlamına geçti.
4. Ayhan "Peki ben kimim?" derken 20:48:46'da yalnız bir Ayhan penceresi vardı;
   sonraki pencere marjda kararsız kaldı. Sticky state eski Havi'yi tuttu ve
   `_identity_note()` modele "kimlik KESİN = Havi" enjekte etti.

Yani temel hata: `SpeakerTap` sürekli, bağımsız pencerelerden global
`SpeakerState.current` üretirken `PiStream._run()` STT final geldiğinde bu global
değeri doğrudan persona swap'i ve kesin kimlik notu için kullanıyor. Bu değer yeni
konuşma dönüşüne ait olmayabilir; hem gecikmiş hem de önceki konuşmacıya ait olabilir.

İlgili kod:

- `worker/speaker_tap.py`: `SpeakerState.observe()` ve `_consume()`
- `worker/pi_brain.py`: `PiStream._run()`, `_current_client()`, `_target()`,
  `_maybe_greet()`, `_identity_note()`, `_enrollment_line()`
- `worker/agent.py`: `user_state_changed` kancası (şu an yalnız wake/ack'e gidiyor)

## Mevcut güvenlik değişiklikleri (deploy edildi)

- Tek profil varken otomatik eşleştirme kapalı:
  `SPEAKER_MIN_PROFILES_FOR_AUTO_MATCH=2`.
- Aynı kişi/switch için iki ardışık kabul zorunlu (`SPEAKER_CONFIRM_HITS=2`).
- Eski kişi prewarm'ı varsayılan kapalı.
- Farklı isimle enrollment, benzerlik bulsa bile otomatik merge yapmaz; açık
  "Sen Ayhan mısın?" onayı ister.

Bu önlemler tek profil/tek pencere hatasını kapattı, fakat dönüş-ilişkisiz
`current` kullanımını kapatmaz.

## Uygulama planı

1. **Dönüş sınırı ekle.** `user_state_changed` ile konuşma başlangıcını ve bitişini
   `SpeakerState`e bildir. Tap pencerelerini zaman damgası ile kaydet.
2. **Turn-safe identity çöz.** STT final için yalnız o dönüşün başlangıcından sonra
   oluşan pencerelerden kimlik üret. Önceki `current` bu dönüşe delil sayılmasın.
3. **Belirsizliği güvenli ele al.** Yeni dönüşte yeterli kanıt yoksa:
   - `_identity_note()` kesin bir isim enjekte etmesin;
   - `_target()` kişisel persona/session'a swap etmesin;
   - kayıt sihirbazını da hemen başlatmasın (geçici aday için yanlış kayıt sorusu
     sormamalı). Genel/unknown yanıt verilsin.
4. **Adayı daha sağlam yap.** İlk tanıma ve kişi değişimi için üç kabul penceresi,
   kısa ve sınırlı bir zaman aralığında istenmeli. Tek ara kararsız pencere aday
   bilgisini anında eski kimliğe çevirmemeli; fakat aday da birkaç saniyeden fazla
   taşınmamalı.
5. **Persona swap'i sadece turn-safe karardan sonra yap.** Bu, Ayhan/Havi hafıza
   sızıntısını da kapatır.
6. **Regresyon testleri.** En az şu dizileri `SpeakerState`/turn resolver üzerinde
   test et:
   - Havi confirmed -> Ayhan tek pencere -> final: `unknown`, kesin Havi değil.
   - başlangıçta yanlış Ayhan, ardından Havi: Ayhan persona swap'i yok.
   - üç güncel Havi penceresi: Havi persona swap'i var.
   - eski/turn öncesi pencereler yeni finali etkileyemez.
7. **Kalibrasyon ikinci faz.** İndirilen Ayhan/Havi ifade WAV'larıyla aynı mikrofon
   koşulunda offline değerlendirme yap. MOSS diarization anonim konuşmacı segmenti
   verir; kişi kimliği kararını tek başına çözmez. Önce turn-safe kapı, sonra
   AS-norm/raw çift eşiği ve MOSS entegrasyonu değerlendirilmelidir.

## Doğrulama notu

Yerel expression WAV'ları aynı encoderla yeniden embed edildiğinde Havi'nin altı
ifade kaydı Havi, Ayhan'ın çoğu ifade kaydı Ayhan çıktı. Dolayısıyla model tamamen
ayrışmaz değil; canlıdaki problem esas olarak kısa/karışık pencereler + yanlış
zamanda persona kararının birleşimi.
