# Görev — `.env` KOLLARI GERÇEKTEN ÇALIŞIYOR MU? (denetim + düzeltme)

Sen bir worker'sın. İşi kendin yap, kısa rapor ver. Panel/subagent açma.

## Bulgu (27 Tem, kesme deploy'unda ortaya çıktı)

`candan-worker.service` `.env`'i **bilerek** `EnvironmentFile=` ile yüklemiyor
(unit'te gerekçe yazılı: boşluklu değerler systemd ayrıştırıcısını kırıyor).
Bunun yerine `agent.py` kendi `load_dotenv()`'ini çağırıyor — **ama 38. satırda,
import'lar (24-33) BİTTİKTEN SONRA.**

Sonuç: `pi_brain`, `higgs_tts`, `speaker_id`, `barge` gibi modüller import edilirken
`.env` HENÜZ OKUNMAMIŞ oluyor. **Modül seviyesinde** okunan her ayar `.env` değerini
hiç görmez, kod içindeki varsayılanda kalır. Fonksiyon içinde okunanlar sorunsuz
(çağrı anında `.env` yüklenmiş oluyor) — bu yüzden `SPEAKER_THRESHOLD` gibi ayarlar
çalışıyor ve sorun bugüne kadar fark edilmedi.

Kesme işinde ölçüldü ve kanıtlandı:
```
import aninda RESUME_ENABLED : True
.env okundu, environ         : false
modul degeri                 : True   ← BARGE_RESUME_ENABLED=false ETKİSİZ
```
`barge.py` `reload_settings()` ile düzeltildi; **geri kalanı denetlenmedi.**

⚠️ **Bu bir belge hatası değil, sessiz bir arıza.** Kullanıcıya "şu satırı ekle,
kapanır" diye verilen geri dönüş komutları çalışmıyor olabilir ve **çalışmadığı
ancak acil bir durumda anlaşılır** — yani en kötü anda.

## 1. DENETİM (asıl iş)

`worker/` altındaki TÜM modüllerde env okumalarını çıkar ve ikiye ayır:

* **modül seviyesi** (import anında bağlanır) → `.env` ETKİSİZ, riskli
* **fonksiyon/metot içi** (çağrı anında okunur) → sorunsuz

Çıktı bir tablo olsun: değişken · dosya:satır · seviye · `.env`'de tanımlı mı ·
**kullanıcıya söz verilmiş bir kol mu**.

Özellikle şunları TEK TEK doğrula — bunlar kullanıcıya "geri dönüş" diye verildi,
`handoff/` belgelerinde yazılı:

| kol | nerede | söz verilen davranış |
|---|---|---|
| `SPEECH_SPEED=0` | hız katmanı | tempo uygulanmaz, ses bugünküyle aynı |
| `SPEAKER_CONFIRM_ASK_ENABLED=false` | kimlik onayı | "sen Ayhan mısın?" sorusu sorulmaz |
| `SPEAKER_OFFER_ENROLL_ON_DENY` | kimlik onayı | tanışma teklifi kapanır |
| `BARGE_RESUME_ENABLED=false` | kesme | düzeltildi — doğrula, regresyonu koru |
| `BARGE_RESUME_NEAR_END_RATIO` | kesme | tekrar eşiği |
| `TTS_ENGINE=omnivoice` | TTS geri dönüşü | **Higgs'ten OmniVoice'a dönüş** |
| `HIGGS_STREAM=0` | TTS | streaming kapanır |
| `SPEAKER_ID_ENABLED` / eşikler | speaker-ID | bugün çalışıyor görünüyor, teyit et |
| `CLAIM_CHECK_*` | truth_check | önceki ajan aynı tuzağı BİLDİRDİ, dokunmadı |
| `WAKE_ENABLED` | wake | kalabalık ortam planının kolu |

`TTS_ENGINE` özellikle kritik: OmniVoice'a dönüş belgelenmiş ana geri dönüş yolu.
Çalışmıyorsa kullanıcı en kötü anda öğrenir.

**Doğrulama TAHMİNLE değil ÖLÇÜMLE olsun:** sunucuda, gerçek `.venv` ile,
`barge`'da yapılanın aynısı — import anındaki değer / `.env` değeri / etkin değer.
Salt-okuma; `.env`'e yazma YAPMA.

## 2. DÜZELTME

Kapsamı DAR tut. `load_dotenv`'i import'ların üstüne taşımak TÜM modüllerin
davranışını aynı anda değiştirir — cazip ama riskli, **yapma** (önceki ajan da
bilerek yapmadı). Bunun yerine:

* Gerçekten kol olan (kullanıcıya söz verilmiş, geri dönüşte kullanılan) değişkenler
  için `barge.reload_settings()` desenini uygula.
* `agent.py`'de `load_dotenv()`'den HEMEN SONRA ilgili modüllerin `reload_settings()`
  fonksiyonları çağrılsın; sıra tek yerde ve görünür olsun.
* Kol OLMAYAN, tek seferlik yapılandırma değerlerini olduğu gibi bırak — ama
  raporda "bunlar `.env`'den ayarlanamaz" diye açıkça listele ki belgeye girsin.
* Varsayılan argümanların tanım anında bağlanması tuzağına dikkat
  (`def f(x=SETTING)` → `def f(x=None)`), `barge`'da bunun örneği var.

## 3. Testler

* Her düzeltilen kol için: `.env` değeri varsayılandan FARKLI olduğunda etkin
  değerin gerçekten değiştiğini gösteren test.
* `reload_settings()` çağrılmadan önceki/sonraki değer farkı.
* Şu an 339 test geçiyor; sayı raporda olsun.

## 4. Deploy

Yedek → gönder → sunucuda **her kolu tek tek doğrula** (import değeri vs etkin değer)
→ **yalnız `candan-worker` restart** → log → md5.
⚠️ `pi-service`'e ve `higgs-tts`'e DOKUNMA.

Deploy sonrası **kullanıcının elindeki geri dönüş komutlarının artık gerçekten
çalıştığını** kanıtla — hangi kolu nasıl doğruladığını yaz.

## 5. Belgeleme

`handoff/2026-07-27-env-kollari.md`: denetim tablosu, düzeltilenler, `.env`'den
ayarlanamayanlar, deploy doğrulaması. Bu tuzağı DEVİR'in "çalışma notları"na kalıcı
kural olarak yaz: **"`worker/` içinde modül seviyesinde env okuma = `.env` etkisiz;
kol olacaksa `reload_settings()` deseni şart."** Başka ajanların DEVİR maddelerini silme.

Tek commit, Türkçe mesaj, push YOK.

## Rapor (KISA)

* Denetim tablosu (kaç değişken modül seviyesinde, kaçı gerçek kol, kaçı kırıktı)
* **Kullanıcıya verilmiş hangi komutlar çalışmıyordu** — bu en önemli satır
* Düzeltilenler, test sayısı, deploy doğrulaması
* Commit hash
