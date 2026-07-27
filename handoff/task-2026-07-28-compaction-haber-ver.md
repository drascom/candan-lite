# Görev — COMPACTION: haber ver ve o sırada komut alma

Sen bir worker'sın. İşi kendin yap, kısa rapor ver. Panel/subagent açma.

⚠️ **ÖNCE KONTROL:** `worker/pi_brain.py`'a başka ajanlar dokunuyordu (gecikme/stall,
kayıt sırası, birleştirme). `git status` temiz değilse ya da birleştirme işi
bitmemişse DUR ve raporla.

## İstek (kullanıcının kendi cümlesi)

> "Sistem compaction yaparken haber verse, ben durumdan haberdar olup bitmesini
> beklerim. Hatta compaction'a girdiğinde yeni sesli komut almasın."

Bugünkü davranış: compaction **arka planda** çalışıyor ve kullanıcıya HİÇBİR ŞEY
söylenmiyor. Kullanıcı sistemin yavaşladığını görüyor ama sebebini bilmiyor.

⚠️ **Compaction'ı senkron yapma.** Arka plana alınmasının sebebi vardı: senkronken
kullanıcı sessizlikte kalıyordu (DEVİR §2). İstenen şey farklı — arka planda kalsın
ama kullanıcı **bilsin** ve o sırada komut **kabul edilmesin**.

## 1. ÖNCE ÖLÇ: compaction ne kadar sürüyor?

**Şu an bunu kimse bilmiyor.** Log'da yalnız başlangıç var:
```
22:09:56  pi tur sonu compaction (reason=threshold) → tur kapatıldı, sıkıştırma ARKA PLANDA
22:21:15  pi tur sonu compaction (reason=threshold) → ...
```
Bitiş satırı YOK. Önce **bitiş + süre** log'la (başlangıç/bitiş token sayısı da:
ölçülen küçülme 28787→15305 ve 29646→15043 idi, o da bu satıra girsin).

Süreyi ölçmeden tasarımı seçme. 3 saniyeyse "bir saniye" demek yeter; 40 saniyeyse
bambaşka bir cümle ve belki ilerleme bildirimi gerekir. Ölçtüğün süreyi raporla.

## 2. Haber verme

* Compaction başlarken **deterministik harness cümlesi** (modele bırakma — aynı
  `truth_check` ilkesi). Ölçülen süreye göre yaz; kısa ve doğal olsun.
* Bitişte de kısa bir işaret ("hazırım" gibi) — kullanıcı ne zaman devam edebileceğini
  bilmeli. Bitiş cümlesi başlangıçtan daha kısa olsun, sürekli tekrarlanacak.
* Süre kısaysa (ör. < 3 sn) haber vermek gürültü olur; eşik koy, `.env`'den ayarlanır
  olsun (`PI_COMPACT_NOTIFY_MIN_S`).

## 3. Compaction sırasında komut almama

* O aralıkta gelen sesli girdi **işlenmesin**. Ama iki tuzak var:
  1. **Sessizce yutma.** Kullanıcı konuşup hiçbir şey olmazsa sistem bozuk sanır.
     Kısa bir işaret gelmeli ("Bir saniye, hazırlanıyorum").
  2. **Kaybolan soru.** DEVİR'de "compaction sırasında kaybolan soru yeniden
     gönderiliyor" diye bir mekanizma var. Bunu OKU ve karar ver: yeni davranışla
     çakışıyor mu? Kullanıcı artık beklemeyi bilerek seçiyor, soruyu tekrar
     göndermeye gerek kalmayabilir — ama mevcut mekanizmayı körlemesine silme,
     ne yaptığını anla ve kararını gerekçesiyle yaz.
* Reddetme yalnız compaction penceresinde olsun; bittiği an normale dönsün.
* Bayrak: `PI_COMPACT_GATE_ENABLED` (varsayılan true). Kapalıyken bugünkü davranış
  bire bir aynı.

⚠️ **`.env` kolu tuzağı:** modül seviyesinde okunan ayarlar `.env`'i görmüyor;
`reload_settings()` deseni zorunlu (bkz. `handoff/2026-07-27-env-kollari.md`).
Yeni bayrakların gerçekten çalıştığını ÖLÇEREK doğrula.

## 4. Test

* Compaction penceresinde gelen girdi işlenmiyor, kısa işaret veriliyor.
* Pencere bitince normale dönüyor.
* Süre eşiğin altındaysa haber verilmiyor.
* Bayrak false → bugünkü davranış.
* Kaybolan-soru mekanizmasıyla ilgili kararın testle kilitlensin.

Birleştirme sonrası test sayısı ne ise ondan başla, yeni sayıyı raporla.

## 5. Deploy

Yedek → gönder → doğrula → **yalnız `candan-worker` restart** → log → md5.
⚠️ `pi-service`, `higgs-tts`, `candan-brain`'e DOKUNMA.

## Belgeleme

`handoff/2026-07-28-compaction-haber-ver.md`: ölçülen compaction süresi (bu ilk kez
ölçülüyor), seçilen cümleler ve gerekçesi, kaybolan-soru kararı, tek blok geri dönüş.
DEVİR §4 madde 2'yi güncelle; başka ajanların maddelerini SİLME.
Tek commit, Türkçe, push YOK.

## Rapor (KISA)

* **Compaction gerçekte kaç saniye sürüyor** (ilk ölçüm)
* Seçilen cümleler ve eşik
* Kaybolan-soru mekanizmasına ne yapıldı ve neden
* Test sayısı, deploy sonucu, tek blok geri dönüş, commit hash
