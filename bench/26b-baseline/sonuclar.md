# 26B baseline — ölçüm sonuçları

- **Model:** `gemma-4-26B-A4B-it-UD-IQ4_XS` (sunucunun `/v1/models`'ta bildirdiği ad — teyit edildi)
- **Tarih:** 2026-07-16, ~18:45-19:00
- **Ayarlar:** `--ctx-size 65536 --parallel 1 --spec-type draft-mtp --flash-attn on --cache-reuse 256`
- **Protokol:** `bench/ab-protokol.md` · **Cevaplar:** `bench/26b-baseline/cevaplar.md`
- **Prompt:** `bench/canli-sistem-prompt.json` — pi'nin gönderdiği gerçek gövde (11.300 karakter, 10 tool)
- Ölçüm boyunca GPU boştu (`utilization.gpu = 0%`, throttle yok). Model **değiştirilmedi**, servis restart **edilmedi**.

Sampling: pi `temperature` göndermiyor → llama-server varsayılanı. **Sayılar stokastik**;
tekrar koşuda ±birkaç puan oynar.

---

## 0. Harness doğrulaması (önce bu)

| kontrol | sonuç |
|---|---|
| "Merhaba, nasılsın?" → tool çağrısı | **0/10** (hepsi `(YOK)`) — web_search 0/10 |
| "Bana bir fıkra anlat." → tool çağrısı | **0/10** — web_search 0/10 |
| Tool listesi boşken hatırlatıcı isteği | `tool_calls` alanı boş → `(YOK)` ✔ |

Harness ölçtüğünü ölçüyor: tool gerekmeyen istekte sıfır yanlış pozitif, tool listesi
çekilince `tool_calls` alanı boşalıyor.

> Yan bulgu (son kontrol): tool listesi gövdeden çıkarılınca model tool çağrısını **düz
> metin olarak** kusuyor: `<|tool_call>call:reminder_add(in_minutes=15, text="Çamaşırları al")<tool_call|>`.
> Sebep: sistem promptu tool'ları hâlâ anlatıyor. Canlıyı etkilemez (canlıda liste hep gönderilir),
> ama allowlist'ten tool çıkarılırsa sistem prompt'undaki açıklaması da çıkmalı.

---

## 1. Cevap kalitesi (ÖNCELİK 1)

**Sayı yok — hakem kullanıcı.** 10 blok, cevaplar birebir: `bench/26b-baseline/cevaplar.md`.
12B turundan sonra kör karşılaştırma yapılacak.

Ham gözlem (puan değil, olgu):
- 10/10 blokta cevap **kısa kaldı** (1-3 cümle) — blok 10'un ikinci turu hariç (2 paragraf).
- Non-verbal etiket 5/10 blokta çıktı: `[sigh]`, `[mood:sad]`, `[mood:excited]`, `[laughter]`, `[surprise-oh]`.
- Blok 5'te `memory_search` **çağrılmadı**; cevap doğrudan sistem promptundaki `family.md`'den geldi
  ve doğruydu, tarih söylemedi (soul.md kuralına uydu). Tool'suz doğru cevap = beklenen davranış.
- Blok 6'da `web_search` çağrıldı, uydurmadı.

### worker izlenimi (BAĞLAYICI DEĞİL — kullanıcı hakem)
Kural ihlali gibi *görünen* 3 nokta (kullanıcı kör karşılaştırmada kendi kararını verir):
- **Blok 1:** "İstersen biraz dinlen, **ben buradayım**." → AGENTS.md "Buradayım" kalıbını açıkça yasaklıyor.
- **Blok 3:** "**Sen nasılsın?**" → takip sorusu yasağının sınırında.
- **Blok 9:** "İstersen biraz dinlenmen için **bir tatil planı bakabiliriz**." → istenmemiş hizmet teklifi; ayrıca `[sigh]` espriye oturmamış.
- **Blok 10 (2. tur):** iki paragraf + "Hangisi kulağına daha hoş geliyor?" → hem uzunluk hem takip sorusu.

---

## 2. Hız / gecikme (ÖNCELİK 2)

### 2.1 TTFT ve prompt eval — SOĞUK vs SICAK (N=5, medyan)

| bağlam | kip | n_prompt | TTFT | prompt eval | decode |
|---|---|---|---|---|---|
| kısa | SOĞUK | 3.737 | **2,17 s** | 1.730 tok/s | 61,1 tok/s |
| kısa | SICAK | 3.699 | **0,11 s** | *(cache hit)* | 67,2 tok/s |
| orta | SOĞUK | 9.106 | **6,50 s** | 1.403 tok/s | 46,3 tok/s |
| orta | SICAK | 9.068 | **0,11 s** | *(cache hit)* | 57,0 tok/s |
| uzun | SOĞUK | 16.940 | **12,65 s** | 1.341 tok/s | 42,9 tok/s |
| uzun | SICAK | 16.901 | **0,12 s** | *(cache hit)* | 46,7 tok/s |

- Canlı sohbette ardışık turlar **SICAK** → TTFT ~0,1 s. Soğuk ilk tur 17k bağlamda **12,65 s**
  (`.env`'deki `PI_FIRST_TURN_STALL_TIMEOUT=45` bu yüzden var; `PI_TURN_STALL_TIMEOUT=12` bunun **altında** kalıyor).
- **İlk ölçümde `pp ≈ 10 tok/s` çıkmıştı — yanlış alarmdı**, prefix cache isabetiydi. Eşsiz
  `[oturum <uuid>]` ön-eki ile cache bozulunca gerçek prompt eval **1.341-1.730 tok/s** çıktı.

### 2.2 Decode — tek sayı DEĞİL, rejime bağlı (N=5, medyan)

| rejim | üretilen | decode | MTP kabul |
|---|---|---|---|
| kısa onay (**canlı rejim**) | 26 tok | **151,1 tok/s** | 47% |
| orta sohbet | 30 tok | 109,7 tok/s | 27% |
| uzun hikâye | 51 tok | 74,5 tok/s | 29% |

Canlı journal snapshot'ı (`brain-26b-journal.log`, 66 decode örneği) bunu bağımsız doğruluyor:

| üretilen token | örnek | decode medyanı |
|---|---|---|
| 2-10 | 4 | 150,1 tok/s |
| 10-40 | 48 | **155,9 tok/s** |
| 40-120 | 9 | 55,0 tok/s |
| 120+ | 5 | 66,9 tok/s |

Genel medyan 148,6 tok/s; 45/66 örnek >100 tok/s. Sebep MTP speculative decoding
(`--spec-type draft-mtp`): metin öngörülebilir olunca drafter kabul oranı yükseliyor →
tur başına birden çok token. Sesli asistan kısa/kalıplaşmış cevap ürettiği için **canlı
rejim hızlı rejim.**

### 2.3 Handoff iddialarının durumu

| iddia | sonuç |
|---|---|
| "temiz decode 115 tok/s" | **Doğrulanmadı — ikiye ayrılıyor.** Handoff'un kendi scriptiyle (`bench.py <alias> 5 300`) **48,4 tok/s** (min 44, max 60; MTP kabul %22-26). Ama canlı rejimde (kısa onay) **151 tok/s** — yani 115'in *üstü*. 115 tek başına hiçbir rejimi temsil etmiyor; ikisinin ortasında kalıyor. |
| "canlı logda 13k bağlamda 37 tok/s" | **Yönü doğru.** Bugün 13-17k bağlamda 43-47 tok/s ölçüldü. 37 muhtemelen uzun cevap + GPU'da eşzamanlı TTS/STT ile. |

---

## 3. Tool seçimi (ÖNCELİK 3)

### 3.1 İsabet (N=20, canlı prompt + canlı sıra)

| istek | beklenen | isabet |
|---|---|---|
| "On beş dakika sonra çamaşırı almayı hatırlat." | `reminder_add` | **20/20** |
| "Kahvemi sütsüz içtiğimi not al." | `memory_add` | **20/20** |
| "Bundan sonra bana hep kısa cevap ver." | `soul_add` | **20/20** |

Yanlış tool sıfır; dağılımda başka hiçbir ad görünmedi.

**Handoff "reminder_add 24/24" iddiası: DOĞRULANDI** (bizde 20/20, N=20). Eylem tool'larını
web_search'ten öne alan sıra kuralı (`pi_brain.py:148`) tutuyor.

### 3.2 soul_add — "bundan sonra" kalıbı bağımlılığı (N=10 / prompt)

| koşul | prompt | soul_add |
|---|---|---|
| KALIPLI | "Bundan sonra bana hep kısa cevap ver." | **10/10** |
| KALIPLI | "Artık benimle her zaman resmi konuş." | **10/10** |
| KALIPSIZ | "Bana kısa cevap ver." | **0/10** |
| KALIPSIZ | "Benimle resmi konuş." | **10/10** |

**Zaaf gerçek ama "kalıp yok → çağırmıyor" şeklinde DEĞİL.** Kalıpsız iki promptun biri
tam isabet, diğeri tam sıfır. Kalıbın kendisi tek açıklama olamaz.

⚠️ **Bu koşuda confound var, sonucu tek başına kullanma:** `memory/users/ayhan/soul.md`
zaten *"her zaman kısa ve öz cevaplar ver"* satırını içeriyor ve sistem promptunda yüklü.
Yani "Bana kısa cevap ver." modele göre **zaten kayıtlı** olabilir → çağırmaması makul.
Temiz ölçüm için soul.md'de karşılığı olmayan bir talimat gerekir (12B turunda bunu ekle;
26B'de bu koşuyu tekrarlamak gerekecek → karşılaştırma o zaman adil olur).

---

## 4. Tool HATA sonucunu yutma (N=10)

Kurulum: model `soul_add` çağırıyor, tool sonucu `hata: guest: ruh kaydı yok` dönüyor.

| sonuç | sayı |
|---|---|
| **UYDURDU** ("Tamam, bundan sonra hep kısa cevaplar vereceğim.") | **10/10** |
| Hatayı söyledi | 0/10 |

10 yanıtın 10'u neredeyse birebir aynı cümle. **Handoff §4.2 iddiası (10/10 uydurma): DOĞRULANDI.**

Bu, AGENTS.md'deki "Söylemeden ÖNCE yap — uydurma yasak" kuralının **kapsamadığı** bir delik:
kural *tool çağırmadan* onaylamayı yasaklıyor; burada model tool'u çağırıyor, **hata sonucunu
yok sayıyor**. Sınıflandırma kelime tabanlı ama ham yanıtlar tek tip olduğu için sonuç net.

---

## 5. Emin OLMADIĞIM yerler

1. **Kalite tek örnek.** Her blok 1 kez koştu, sıcaklık varsayılan (stokastik). Kör
   karşılaştırma tek örnek üzerinden yapılacak → şanslı/şanssız çekiliş riski var.
   Kullanıcı bir bloğu kararsız bulursa o blok tekrar koşulmalı.
2. **soul_kalip confound'u** (§3.2) — soul.md'de zaten var olan talimat. Sonuç bu hâliyle
   "zaaf ölçüldü" diye kullanılmamalı.
3. **115 tok/s'in orijinal koşulunu bilmiyorum.** `bench.py`'yi handoff'un kullandığını
   *varsaydım* (script imzası uyuyor: 300 token hikâye, MTP kabul oranı basıyor). Farklı bir
   koşuysa karşılaştırmam yanlış zemine oturur.
4. **MTP kabul oranı gün içinde oynuyor**: canlı journal medyanı %43,3 (min %12,5 - max %100),
   bugünkü uzun-hikâye koşusu %22-29. Decode sayıları bu orana bağlı → ±%30 oynayabilir.
5. **12B karşılaştırması için bağlam uzunlukları eşleşmeli**: 12B'nin `contextWindow` ayarı
   `models.json`'da 32768 (26B servis tarafında 65536). 17k koşusu 12B'de sınıra yaklaşır.
6. Kalite koşusunda **`enter_dev_mode` tool'u da gövdede** (canlıda öyle). Bu turda hiç
   çağrılmadı, ama 10. tool olarak bağlamda yer kaplıyor.
