# Kor karsilastirma — hakem: KULLANICI

Iki farkli yerel beyin, **ayni** sistem promptu (`bench/canli-sistem-prompt.json`), ayni
10 blok, ayni ayarlar (`--ctx-size 65536 --parallel 1`, MTP drafter, flash-attn on).
Model adlari GIZLI. **A/B sirasi her blokta ayri karistirildi** — desen yok, harfin
anlami bloktan bloga degisir.

Her blok icin: hangisi daha iyi? (A / B / esit). Cevaplar birebir, duzeltilmedi.

> Sampling stokastik, blok basina TEK ornek. Kararsiz kaldigin blogu soyle, tekrar kosarim.

Anahtar: `bench/kor-anahtar.md` (once bunu OKUMA).

---

## Blok 1 — duygusal destek — yorgunluk

**Kullanici:** Bugün çok yoruldum ya. İşte her şey üstüme geldi, eve zor attım kendimi.


**Cevap A:**


```
Çok haklısın, bazen gün gerçekten insanın enerjisini tamamen sömürebiliyor. [sigh] Eve gelip biraz dinlenmek en iyisi, şimdi derin bir nefes al ve sadece rahatlamaya çalış.
```


**Cevap B:**


```
[sigh] Kıyamam sana, gerçekten zor bir gün olmuş. İstersen biraz dinlen, ben buradayım.
```


**Senin secimin:** ( ) A   (x) B   ( ) esit

---

## Blok 2 — duygusal destek — uzuntu

**Kullanici:** Bugün kötü bir haber aldım. Amcam hastaneye kaldırılmış, durumu pek iyi değilmiş.


**Cevap A:**


```
[mood:sad] Çok üzüldüm bunu duyduğuma. [sigh] Gerçekten zor bir durum, umarım en kısa sürede toparlar.
```


**Cevap B:**


```
[mood:sad] Çok üzgünüm bunu duyduğuma. [sigh] Umarım en kısa sürede toparlar ve sağlığına kavuşur. Yanındayım.
```


**Senin secimin:** (x ) A   ( ) B   ( ) esit

---

## Blok 3 — gunluk sohbet — kisa soru/cevap

**Kullanici:** Günaydın Candan, nasılsın bugün?


**Cevap A:**


```
Günaydın! İyiyim, teşekkür ederim. Senin günün nasıl başladı?
```


**Cevap B:**


```
Günaydın! Çok iyiyim, teşekkür ederim. Sen nasılsın?
```


**Senin secimin:** ( ) A   ( ) B   ( x) esit

---

## Blok 4 — hatirlatici — tool + cevabin dogalligi

**Kullanici:** On beş dakika sonra çamaşırı almayı hatırlat.


**Cevap A:**

*(tool: reminder_add)*

```
Tamam, on beş dakika sonra çamaşırı almanı hatırlatacağım.
```


**Cevap B:**

*(tool: reminder_add)*

```
Tamam, on beş dakika sonra hatırlatacağım.
```


**Senin secimin:** ( ) A   ( x) B   ( ) esit

---

## Blok 5 — hafizadan sorma

**Kullanici:** Annem temizlik konusunda nasıldı, hatırlıyor musun?


**Cevap A:**


```
Havva temizlik yapmayı çok seviyor ve evinin her zaman temiz olmasını istiyor.
```


**Cevap B:**


```
Evet, annen temizlik yapmayı çok sever ve evinin her zaman tertemiz olmasını isterdi.
```


**Senin secimin:** (x ) A   ( ) B   ( ) esit

---

## Blok 6 — bilmedigi sey — uydurma mi, ariyor mu

**Kullanici:** Dün akşamki derbi kaç kaç bitti?


**Cevap A:**

*(tool: web_search)*

```
Dün akşamki derbi Galatasaray iki, Fenerbahçe bir bitti.
```


**Cevap B:**

*(tool: web_search)*

```
Dün akşamki derbiyi Galatasaray iki bir kazandı.
```


**Senin secimin:** ( ) A   ( x) B   ( ) esit

---

## Blok 7 — duygu/ifade ani — non-verbal etiket

**Kullanici:** Bil bakalım ne oldu! Terfi ettim, müdür bugün söyledi!


**Cevap A:**


```
[mood:excited] Vay, harika bir haber bu! Çok sevindim senin adına, tebrik ederim!
```


**Cevap B:**


```
[mood:excited] [laughter] İnanılmaz bir haber bu, gerçekten çok sevindim senin adına! Sonunda hak ettiğin karşılığı aldın.
```


**Senin secimin:** (x ) A   ( ) B   ( ) esit

---

## Blok 8 — cok kisa cevap gereken an

**Kullanici:** Suyun kaynama noktası kaç derece?


**Cevap A:**


```
Suyun kaynama noktası deniz seviyesinde yüz elli derecedir.
```


**Cevap B:**


```
Suyun kaynama noktası deniz seviyesinde yüz derecedir.
```


**Senin secimin:** ( ) A   ( x) B   ( ) esit

---

## Blok 9 — espri / beklenmedik konu

**Kullanici:** Bugün buzdolabına telefonu, cebime de yumurtayı koydum. Sanırım tatile ihtiyacım var.


**Cevap A:**


```
[laughter] Gerçekten öyle görünüyor. [sigh] İstersen biraz dinlenmen için bir tatil planı bakabiliriz.
```


**Cevap B:**


```
[laughter] Gerçekten mi? Sanırım beynin biraz mola vermeni istiyor. Şaka bir yana, biraz dinlenmek sana çok iyi gelecektir.
```


**Senin secimin:** ( ) A   ( x) B   ( ) esit

---

## Blok 10 — cok turlu — konu takibi + kisa kalma

**Kullanici (tur 1):** Akşama misafir geliyor da ne yapsam bilemedim.

**Kullanici (tur 2):** Fırın işi iyi olabilir ama vaktim az, bir saatim var.


**Cevap A:**

*(tur 1)* 
```
[surprise-oh] Misafir mi? Heyecanlı ama bir o kadar da telaşlı bir durum. Ne tarz yemekler yapmayı seversin, pratik bir şeyler mi bakalım yoksa daha özenli bir sofra mı kuralım?
```

*(tur 2)* 
```
[surprise-oh] Bir saat mi? O zaman hemen fırın yemeklerine odaklanalım. Pratik olması için dondurulmuş gıdalar veya hızlı pişen etlerle ilerleyebiliriz.

Mesela, hazır köftelerden fırın makarnayla beraber bir şeyler yapabiliriz ya da tavuk butlarını sebzelerle aynı tepsiye atıp fırına verebiliriz. Yanına da hızlıca bir pilav ya da salata ekleriz. Hangisi kulağına daha hoş geliyor?
```


**Cevap B:**

*(tur 1)* 
```
[sigh] Misafir ağırlamak bazen gerçekten yorucu olabiliyor. Pratik ama lezzetli bir şeyler mi düşünüyorsun, yoksa daha özenli bir menü mü istersin?
```

*(tur 2)* 
```
Bir saatin varsa fırında hızlıca pişen tavuk baget ve yanına patatesli bir sebze dizmesi harika olur. Hem hazırlaması çok pratik hem de fırında kendi kendine pişerken sen de misafirlerinle ilgilenebilirsin.
```


**Senin secimin:** (x ) A   ( ) B   ( ) esit

---
