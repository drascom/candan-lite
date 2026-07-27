# Görev — SÖZÜNÜ KESME: yeni komut mu, sadece sohbet mi?

Sen bir worker'sın. İşi kendin yap, kısa rapor ver. Panel/subagent açma.

⚠️ **ÖNCE KONTROL ET:** bu dosyaya başlamadan `git status` ve `git diff --stat` bak.
`worker/pi_brain.py` üzerinde başka bir ajanın yarım işi duruyorsa DUR ve raporla —
bugün aynı dosyada iki ajanın çakışması ucundan dönüldü.

## İstek (kullanıcının kendi cümlesi)

> "Sesini kestiğim zaman, eğer yeni bir komut verdiysem eskisini iptal edip yenisine
> başlayacak. Eğer sadece sohbet ettiysem kaldığından devam edecek."

Şu an ikisi de aynı muameleyi görüyor: kesildi mi cevap ölüyor.

Canlı örnek (27 Tem 18:37): Candan duygu örneklerini sayarken kullanıcı araya girip
"Beni duydun mu?" dedi. Cevap "Harika bir haber" de kesildi, Candan da *"Evet, seni
duydum. Az önceki örnekleri dinledin sanırım."* diye YANLIŞ varsaydı — kullanıcı
hiçbir örneği duymamıştı ve baştan istemek zorunda kaldı.

## Kullanıcının verdiği iki karar (tasarımı bunlar belirler)

1. **Sohbet durumunda devam: KALDIĞI CÜMLENİN BAŞINDAN.** Yarım kalan cümle baştan
   söylenip devam edilir. Küçük bir tekrar olur ama kopukluk olmaz — insan da böyle yapar.
   Tam kesildiği heceden devam etmek ve kalanı özetletmek ELENDİ.
2. **Şüphede: YENİ KOMUT SAY, SUSSUN.** Kararsızsa konuşmayı keser ve dinler.
   Yanlış kararın telafisi kolay ("devam et" demek yeter); tersinde kullanıcının sözünü
   ikinci kez kesmesi gerekir.

## Yapılacaklar

### 1. Kesme sınıflandırması

Kesen kullanıcı ifadesi iki sınıftan biri: **yeni-istek** (iptal et, yenisine başla) ya da
**sohbet/geri-bildirim** (devam et). Sohbet tarafı tipik olarak kısa onay/tepki
sözleridir: "hı hı", "evet", "tamam", "anladım", "peki", "doğru", "aynen", "güzel",
"hmm", kahkaha, isim seslenişi ("Candan?").

Sıra ÖNEMLİ — ucuzdan pahalıya:
* Önce **deterministik kapı**: çok kısa ifadeler (≤2-3 kelime) ve geri-bildirim
  sözlüğü. Bu tipik durumu bedava çözer.
* Soru işareti, emir kipi, yeni bir konu adı, "yap/söyle/aç/kapat/anlat/dur/bekle"
  gibi eylem fiilleri → **yeni-istek**.
* Kalan belirsiz durumda **mevcut sınıflandırıcı desenini** kullan (`truth_check`
  katman-3'te zaten var, 84-130 ms). Yeni model/mekanizma getirme.
* Sınıflandırıcı da kararsızsa → **yeni-istek** (kullanıcının kararı).

⚠️ Kesme anı gecikmeye duyarlı. Deterministik kapı çoğu turu kapatmalı;
sınıflandırıcının tipik turda çağrılmaması hedef (`truth_check`'teki gibi, testle kanıtla).

### 2. Devam mekaniği

* Cevap metni cümle sınırlarıyla takip edilsin; kesme anında **hangi cümlenin
  seslendirildiği** ve **hangilerinin kaldığı** bilinsin.
* "Sohbet" kararında: kesilen cümle **baştan** + kalan cümleler seslendirilir.
  Metin YENİDEN ÜRETİLMEZ — modele tekrar gidilmez, elde olan metin kullanılır.
  (Yeniden üretim gecikme ekler ve içeriği değiştirir.)
* Kesilen cümle çok kısaysa (birkaç kelime) ya da neredeyse bitmişse tekrar etmeden
  sonrakinden devam etmek daha doğal olabilir — bunu ölç/dene ve kararını raporla.
* "Yeni-istek" kararında bugünkü davranış aynen: kalan metin atılır, yeni tur başlar.
* Devam ederken kullanıcı TEKRAR keserse aynı kurallar işler (durum tutarlı kalsın).
* Devam bekleyen metnin ömrü sınırlı olsun (ör. ~30 sn) — kullanıcı konuyu değiştirdiyse
  eski cevabın yarısı sonradan ortaya çıkmamalı. Süreyi env ile ayarlanabilir yap.

### 3. Model yanlış varsaymasın

Yukarıdaki canlı örnekte model, kesilen cevabın kullanıcı tarafından DUYULDUĞUNU
varsaydı. Kesilen metin **konuşma geçmişine söylenmiş gibi girmemeli**: gerçekte
seslendirilen kısım neyse geçmişte o durmalı, kalanı "söylenmedi" olarak işaretlenmeli.
Bu `truth_check` ilkesinin aynısı — harness NE OLDUĞUNU bilir, model tahmin etmez.

### 4. Bayrak, test, deploy

* Bayrak: `BARGE_RESUME_ENABLED` (varsayılan true). Kapalıyken davranış BUGÜNKÜYLE
  bire bir aynı olmalı.
* Testler: kısa onay → devam · yeni soru → iptal · kararsız → iptal (yeni-istek) ·
  devam sırasında ikinci kesme · zaman aşımı → kalan atılır · kesilen kısım geçmişe
  söylenmiş gibi girmiyor · bayrak false → bugünkü davranış.
  Tüm takım koşsun (şu an 271+), sayı raporda.
* Deploy: yedek → gönder → doğrula → **yalnız `candan-worker` restart** → log → md5.
  ⚠️ `pi-service`'e ve `higgs-tts`'e DOKUNMA.

### 5. Belgeleme

`handoff/2026-07-27-sozunu-kesme.md`: iki tasarım kararı ve gerekçesi, sınıflandırma
sırası, ölçülen gecikme, tek blok geri dönüş. DEVİR'e kısa madde (başka ajanların
eklediklerini SİLME). Tek commit, Türkçe mesaj, push YOK.

## Rapor (KISA)

* Sınıflandırma nasıl kuruldu, tipik turda sınıflandırıcı çağrılıyor mu (ölçüm)
* Kesme→devam gecikmesi
* Test sayısı, deploy sonucu, tek blok geri dönüş
* Kullanıcının canlıda ne deneyip ne görmesi gerektiği
* Commit hash
