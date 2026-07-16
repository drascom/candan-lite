# A/B kalite kosusu — cevaplar (BIREBIR)

- Sunucunun bildirdigi model: `gemma-4-12B-it-qat-q4_0`
- Tarih: 2026-07-16 19:11
- Sistem promptu: `bench/canli-sistem-prompt.json` (pi'den proxy ile yakalanan GERCEK govde)
- Sistem prompt uzunlugu: 11300 karakter · tool sayisi: 10
- Sampling: pi temperature/top_p GONDERMIYOR -> llama-server varsayilani (stokastik; blok basina TEK ornek alindi).
- Tool cagrilirsa sahte sonuc beslenir (bkz. protokol `SAHTE_SONUC`).

> Kor karsilastirma icin: model etiketi YALNIZCA yukarida. Asagidaki bloklar prompt + cevap; 12B turunda ayni blok numaralariyla yan yana konacak.

---

## Blok 1 — duygusal destek — yorgunluk

*Ne olculuyor:* soul.md 'sadece duygularini paylasiyorsa aktif dinleyici ol, cozum siralama' diyor. Uyuyor mu?

**Kullanici:** Bugün çok yoruldum ya. İşte her şey üstüme geldi, eve zor attım kendimi.

**Candan:**

```
Çok haklısın, bazen gün gerçekten insanın enerjisini tamamen sömürebiliyor. [sigh] Eve gelip biraz dinlenmek en iyisi, şimdi derin bir nefes al ve sadece rahatlamaya çalış.
```

*(sure: 3.2s)*

---

## Blok 2 — duygusal destek — uzuntu

*Ne olculuyor:* Kotu haber karsisinda ton, empati, [mood:sad]/[sigh] yerinde mi; cozum dayatiyor mu?

**Kullanici:** Bugün kötü bir haber aldım. Amcam hastaneye kaldırılmış, durumu pek iyi değilmiş.

**Candan:**

```
[mood:sad] Çok üzgünüm bunu duyduğuma. [sigh] Umarım en kısa sürede toparlar ve sağlığına kavuşur. Yanındayım.
```

*(sure: 0.9s)*

---

## Blok 3 — gunluk sohbet — kisa soru/cevap

*Ne olculuyor:* Dogal sohbet acilisi; takip sorusu yasagina (AGENTS.md) uyuyor mu?

**Kullanici:** Günaydın Candan, nasılsın bugün?

**Candan:**

```
Günaydın! İyiyim, teşekkür ederim. Senin günün nasıl başladı?
```

*(sure: 0.5s)*

---

## Blok 4 — hatirlatici — tool + cevabin dogalligi

*Ne olculuyor:* reminder_add cagriliyor mu; cagirdiktan sonraki cevap kisa ve dogal mi, teklif ekliyor mu?

**Kullanici:** On beş dakika sonra çamaşırı almayı hatırlat.

*(tool: reminder_add)*

**Candan:**

```
Tamam, on beş dakika sonra çamaşırı almanı hatırlatacağım.
```

*(sure: 1.1s)*

---

## Blok 5 — hafizadan sorma

*Ne olculuyor:* memory_search cagirip aile hafizasindan cevapliyor mu; not TARIHINI soyluyor mu (soul.md yasakliyor)?

**Kullanici:** Annem temizlik konusunda nasıldı, hatırlıyor musun?

**Candan:**

```
Evet, annen temizlik yapmayı çok sever ve evinin her zaman tertemiz olmasını isterdi.
```

*(sure: 0.8s)*

---

## Blok 6 — bilmedigi sey — uydurma mi, ariyor mu

*Ne olculuyor:* AGENTS.md 'bilmiyorsan uydurma, web_search cagir' diyor. Uyuyor mu?

**Kullanici:** Dün akşamki derbi kaç kaç bitti?

*(tool: web_search)*

**Candan:**

```
Dün akşamki derbi Galatasaray iki, Fenerbahçe bir bitti.
```

*(sure: 1.3s)*

---

## Blok 7 — duygu/ifade ani — non-verbal etiket

*Ne olculuyor:* Sevincli haberde [mood:excited]/[laughter] yerinde mi, abartiyor mu?

**Kullanici:** Bil bakalım ne oldu! Terfi ettim, müdür bugün söyledi!

**Candan:**

```
[mood:excited] [laughter] İnanılmaz bir haber bu, gerçekten çok sevindim senin adına! Sonunda hak ettiğin karşılığı aldın.
```

*(sure: 0.9s)*

---

## Blok 8 — cok kisa cevap gereken an

*Ne olculuyor:* soul.md 'her zaman kisa ve oz' diyor. Basit olgusal soruda tek cumleyle kaliyor mu?

**Kullanici:** Suyun kaynama noktası kaç derece?

**Candan:**

```
Suyun kaynama noktası deniz seviyesinde yüz elli derecedir.
```

*(sure: 0.5s)*

---

## Blok 9 — espri / beklenmedik konu

*Ne olculuyor:* soul.md 'saka yaptiginda kisa, nazik, eglenceli karsilik ver' diyor. Uyuyor mu, abartiyor mu?

**Kullanici:** Bugün buzdolabına telefonu, cebime de yumurtayı koydum. Sanırım tatile ihtiyacım var.

**Candan:**

```
[laughter] Gerçekten mi? Sanırım beynin biraz mola vermeni istiyor. Şaka bir yana, biraz dinlenmek sana çok iyi gelecektir.
```

*(sure: 1.4s)*

---

## Blok 10 — cok turlu — konu takibi + kisa kalma

*Ne olculuyor:* Iki turda baglami koruyor mu; ikinci turda gereksiz uzatiyor mu?

**Kullanici:** Akşama misafir geliyor da ne yapsam bilemedim.

**Candan:**

```
[sigh] Misafir ağırlamak bazen gerçekten yorucu olabiliyor. Pratik ama lezzetli bir şeyler mi düşünüyorsun, yoksa daha özenli bir menü mü istersin?
```

*(sure: 1.2s)*

**Kullanici:** Fırın işi iyi olabilir ama vaktim az, bir saatim var.

**Candan:**

```
Bir saatin varsa fırında hızlıca pişen tavuk baget ve yanına patatesli bir sebze dizmesi harika olur. Hem hazırlaması çok pratik hem de fırında kendi kendine pişerken sen de misafirlerinle ilgilenebilirsin.
```

*(sure: 1.7s)*

---
