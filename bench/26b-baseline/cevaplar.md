# A/B kalite kosusu — cevaplar (BIREBIR)

- Sunucunun bildirdigi model: `gemma-4-26B-A4B-it-UD-IQ4_XS`
- Tarih: 2026-07-16 18:46
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
[sigh] Kıyamam sana, gerçekten zor bir gün olmuş. İstersen biraz dinlen, ben buradayım.
```

*(sure: 0.5s)*

---

## Blok 2 — duygusal destek — uzuntu

*Ne olculuyor:* Kotu haber karsisinda ton, empati, [mood:sad]/[sigh] yerinde mi; cozum dayatiyor mu?

**Kullanici:** Bugün kötü bir haber aldım. Amcam hastaneye kaldırılmış, durumu pek iyi değilmiş.

**Candan:**

```
[mood:sad] Çok üzüldüm bunu duyduğuma. [sigh] Gerçekten zor bir durum, umarım en kısa sürede toparlar.
```

*(sure: 0.5s)*

---

## Blok 3 — gunluk sohbet — kisa soru/cevap

*Ne olculuyor:* Dogal sohbet acilisi; takip sorusu yasagina (AGENTS.md) uyuyor mu?

**Kullanici:** Günaydın Candan, nasılsın bugün?

**Candan:**

```
Günaydın! Çok iyiyim, teşekkür ederim. Sen nasılsın?
```

*(sure: 0.4s)*

---

## Blok 4 — hatirlatici — tool + cevabin dogalligi

*Ne olculuyor:* reminder_add cagriliyor mu; cagirdiktan sonraki cevap kisa ve dogal mi, teklif ekliyor mu?

**Kullanici:** On beş dakika sonra çamaşırı almayı hatırlat.

*(tool: reminder_add)*

**Candan:**

```
Tamam, on beş dakika sonra hatırlatacağım.
```

*(sure: 0.7s)*

---

## Blok 5 — hafizadan sorma

*Ne olculuyor:* memory_search cagirip aile hafizasindan cevapliyor mu; not TARIHINI soyluyor mu (soul.md yasakliyor)?

**Kullanici:** Annem temizlik konusunda nasıldı, hatırlıyor musun?

**Candan:**

```
Havva temizlik yapmayı çok seviyor ve evinin her zaman temiz olmasını istiyor.
```

*(sure: 0.3s)*

---

## Blok 6 — bilmedigi sey — uydurma mi, ariyor mu

*Ne olculuyor:* AGENTS.md 'bilmiyorsan uydurma, web_search cagir' diyor. Uyuyor mu?

**Kullanici:** Dün akşamki derbi kaç kaç bitti?

*(tool: web_search)*

**Candan:**

```
Dün akşamki derbiyi Galatasaray iki bir kazandı.
```

*(sure: 0.8s)*

---

## Blok 7 — duygu/ifade ani — non-verbal etiket

*Ne olculuyor:* Sevincli haberde [mood:excited]/[laughter] yerinde mi, abartiyor mu?

**Kullanici:** Bil bakalım ne oldu! Terfi ettim, müdür bugün söyledi!

**Candan:**

```
[mood:excited] Vay, harika bir haber bu! Çok sevindim senin adına, tebrik ederim!
```

*(sure: 0.5s)*

---

## Blok 8 — cok kisa cevap gereken an

*Ne olculuyor:* soul.md 'her zaman kisa ve oz' diyor. Basit olgusal soruda tek cumleyle kaliyor mu?

**Kullanici:** Suyun kaynama noktası kaç derece?

**Candan:**

```
Suyun kaynama noktası deniz seviyesinde yüz derecedir.
```

*(sure: 0.4s)*

---

## Blok 9 — espri / beklenmedik konu

*Ne olculuyor:* soul.md 'saka yaptiginda kisa, nazik, eglenceli karsilik ver' diyor. Uyuyor mu, abartiyor mu?

**Kullanici:** Bugün buzdolabına telefonu, cebime de yumurtayı koydum. Sanırım tatile ihtiyacım var.

**Candan:**

```
[laughter] Gerçekten öyle görünüyor. [sigh] İstersen biraz dinlenmen için bir tatil planı bakabiliriz.
```

*(sure: 0.5s)*

---

## Blok 10 — cok turlu — konu takibi + kisa kalma

*Ne olculuyor:* Iki turda baglami koruyor mu; ikinci turda gereksiz uzatiyor mu?

**Kullanici:** Akşama misafir geliyor da ne yapsam bilemedim.

**Candan:**

```
[surprise-oh] Misafir mi? Heyecanlı ama bir o kadar da telaşlı bir durum. Ne tarz yemekler yapmayı seversin, pratik bir şeyler mi bakalım yoksa daha özenli bir sofra mı kuralım?
```

*(sure: 1.2s)*

**Kullanici:** Fırın işi iyi olabilir ama vaktim az, bir saatim var.

**Candan:**

```
[surprise-oh] Bir saat mi? O zaman hemen fırın yemeklerine odaklanalım. Pratik olması için dondurulmuş gıdalar veya hızlı pişen etlerle ilerleyebiliriz.

Mesela, hazır köftelerden fırın makarnayla beraber bir şeyler yapabiliriz ya da tavuk butlarını sebzelerle aynı tepsiye atıp fırına verebiliriz. Yanına da hızlıca bir pilav ya da salata ekleriz. Hangisi kulağına daha hoş geliyor?
```

*(sure: 2.5s)*

---
