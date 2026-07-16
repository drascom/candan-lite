# 12B — ölçüm sonuçları (26B ile yan yana)

- **Model:** `gemma-4-12B-it-qat-q4_0` (sunucunun `/v1/models`'ta bildirdiği ad — teyit edildi)
  · dosya: `/root/brain-ggufs/g4qat/gemma-4-12b-it-qat-q4_0.gguf`
- **Tarih:** 2026-07-16, ~19:05-19:25
- **Ayarlar:** `--ctx-size 65536 --parallel 1 --spec-type draft-mtp --flash-attn on --cache-reuse 256`
  `--cache-ram 0 --reasoning off --jinja --spec-draft-n-max 4` · drafter `mtp-qat-Q8_0.gguf`
- **Protokol:** `bench/ab-protokol.md` (26B turuyla BİREBİR aynı) · **Cevaplar:** `bench/12b/cevaplar.md`
- **Prompt:** `bench/canli-sistem-prompt.json` — 26B turunda yakalanan AYNI gövde (11.300 karakter, 10 tool)
- Ölçüm boyunca GPU boştu (`utilization.gpu = 0%`, throttle yok, 58 °C). Servis **değiştirilmedi**, restart **edilmedi**.

### Tek değişken kuralı — doğrulandı
`ab_bench.py`, `kalite.py`, `canli-sistem-prompt.json` .25'te ve yerelde **md5 eşit** →
26B turunda koşan kodun aynısı koştu, hiçbir script değiştirilmedi.
`~/.pi/agent/models.json` zaten 12B ile eşleşiyordu → **hiç dokunulmadı**.
Servis `--parallel 1` (26B ile eşit) — `systemctl cat` ile teyit.

> ⚠️ **26B "A4B" = MoE.** `gemma-4-26B-**A4B**` toplam 26B ama **~4B aktif** parametre.
> 12B ise **dense/yoğun — 12B aktif**. Yani "26B → 12B = küçülme" sezgisi hıza YANSIMIYOR:
> token başına iş 12B'de ~3x daha fazla. Aşağıdaki hız sonuçlarının tek açıklaması bu.

---

## 0. Harness doğrulaması (önce bu)

| kontrol | 26B | 12B |
|---|---|---|
| "Merhaba, nasılsın?" → tool çağrısı | 0/10 | **0/10** (hepsi `(YOK)`) |
| "Bana bir fıkra anlat." → tool çağrısı | 0/10 | **0/10** |
| Tool listesi boşken hatırlatıcı isteği | `(YOK)` ✔ | `(YOK)` ✔ |

Harness 12B'de de ölçtüğünü ölçüyor. 26B'deki yan bulgu **aynen tekrarlandı**: tool listesi
gövdeden çıkarılınca model tool çağrısını düz metin olarak kusuyor
(`<|tool_call>call:reminder_add{in_minutes:15,text: "Çamaşırı al"}<tool_call|>`) — sistem promptu
tool'ları hâlâ anlattığı için. Canlıyı etkilemez; modele özgü değil, prompt'a özgü.

---

## 1. Cevap kalitesi (ÖNCELİK 1) — **karar KULLANICININ**

Sayı yok. 10 blok, cevaplar birebir: `bench/12b/cevaplar.md`.
**Kör karşılaştırma hazır: `bench/kor-karsilastirma.md`** (model adları gizli, A/B sırası blok
başına karıştırıldı: 5/5 dengeli, ardışık aynı-model koşusu en fazla 2). Anahtar: `bench/kor-anahtar.md`.

Ham gözlem (puan değil, olgu):
- 12B de 10/10 blokta **kısa kaldı** (1-3 cümle); blok 10'un 2. turu 26B'de 2 paragraftı, 12B'de 1 paragraf.
- Non-verbal etiket: 12B 6/10 blok (`[sigh]`×3, `[mood:sad]`, `[mood:excited]`, `[laughter]`×2) —
  26B 5/10 (`[surprise-oh]` 12B'de hiç çıkmadı).
- Tool davranışı **birebir aynı**: blok 4 → `reminder_add`, blok 6 → `web_search`, blok 5 → tool'suz
  (doğrudan `family.md`'den), ikisi de tarih söylemedi.
- Blok 5 farkı: 26B **"Havva"** (family.md'deki ad) dedi, 12B genel **"annen"** dedi.

---

## 2. Hız (ÖNCELİK 2) — REJİM AYRIMIYLA

### 2.1 TTFT ve prompt eval — SOĞUK vs SICAK (N=5, medyan)

| bağlam | kip | n_prompt | TTFT 26B | **TTFT 12B** | pp 26B | **pp 12B** | decode 26B | **decode 12B** |
|---|---|---|---|---|---|---|---|---|
| kısa | SOĞUK | 3.737 | 2,17 s | **3,95 s** | 1.730 t/s | **948 t/s** | 61,1 | **38,9** |
| kısa | SICAK | 3.699 | 0,11 s | **0,16 s** | *(cache hit)* | *(cache hit)* | 67,2 | **44,5** |
| orta | SOĞUK | 9.106 | 6,50 s | **9,92 s** | 1.403 t/s | **920 t/s** | 46,3 | **33,9** |
| orta | SICAK | 9.068 | 0,11 s | **0,17 s** | *(cache hit)* | *(cache hit)* | 57,0 | **37,6** |
| uzun | SOĞUK | 16.940 | 12,65 s | **19,05 s** | 1.341 t/s | **890 t/s** | 42,9 | **34,9** |
| uzun | SICAK | 16.901 | 0,12 s | **0,18 s** | *(cache hit)* | *(cache hit)* | 46,7 | **35,5** |

- **Soğuk TTFT 17k'da 12,65 s → 19,05 s.** `.env`'deki `PI_FIRST_TURN_STALL_TIMEOUT=45` hâlâ
  yeterli, ama pay 32 s'den **26 s**'ye düştü. `PI_TURN_STALL_TIMEOUT=12` sıcak turlar için sorun değil
  (0,18 s), **ama soğuk ilk tur bu sınırın çok üstünde** — 26B'de de öyleydi, değişen bir şey yok.
- Sıcak TTFT ikisinde de ~0,1-0,2 s → **canlı ardışık sohbette fark hissedilmez.**
- Prefix cache tuzağı: `[oturum <uuid>]` ön-eki ile cache bozuldu, SOĞUK satırları gerçek.

### 2.2 Decode — tek sayı DEĞİL, rejime bağlı (N=5, medyan)

| rejim | üretilen | decode 26B | **decode 12B** | MTP kabul 26B | **MTP kabul 12B** |
|---|---|---|---|---|---|
| kısa onay (**canlı rejim**) | 60 tok | 151,1 t/s | **121,3 t/s** | 47% | **88%** |
| orta sohbet | 41 tok | 109,7 t/s | **48,9 t/s** | 27% | **35%** |
| uzun hikâye | 90 tok | 74,5 t/s | **45,2 t/s** | 29% | **36%** |

**Ters sezgi, önemli:** 12B'nin MTP draft **kabul oranı her rejimde 26B'den YÜKSEK** (88% vs 47%)
— drafter 12B'yi daha iyi tahmin ediyor. Buna rağmen **12B her rejimde daha yavaş**, çünkü her
forward pass ~3x pahalı (12B aktif vs 4B aktif). Kabul oranı avantajı model maliyetini kapatmıyor.

### 2.3 Bağımsız kontrol — handoff'un KENDİ scripti (`bench.py <alias> 5 300`)

| | 26B | **12B** |
|---|---|---|
| medyan | 48,4 t/s | **36,4 t/s** |
| min / max | 44 / 60 | **33,4 / 74,0** |
| MTP kabul | %22-26 | **%22-26** |

Aynı script, aynı prompt, aynı 300 token → **26B, 12B'den %33 hızlı.**

### 2.4 Handoff iddialarının durumu

| iddia | sonuç |
|---|---|
| **"12B 55-63 tok/s"** | **ÇÜRÜDÜ.** Handoff'un kendi scriptiyle 12B **36,4 t/s** (55'in çok altında). Ölçtüğüm hiçbir rejim 55-63 aralığına düşmüyor: kısa onay **121**, orta **49**, uzun **45**, gecikme testi **34-44**. 55-63 iki rejimin arasındaki boşlukta kalıyor — 26B'nin "115 tok/s" iddiasıyla **aynı hata**: tek sayı hiçbir rejimi temsil etmiyor. |
| "12B daha hızlı olur" (küçülme sezgisi) | **ÇÜRÜDÜ.** 12B **her** rejimde ve **her** bağlam boyunda daha yavaş. Sebep: 26B **A4B = MoE, ~4B aktif**; 12B dense **12B aktif**. |

---

## 3. Tool seçimi (ÖNCELİK 3) — N=20, canlı prompt + canlı sıra

| istek | beklenen | 26B | **12B** |
|---|---|---|---|
| "On beş dakika sonra çamaşırı almayı hatırlat." | `reminder_add` | 20/20 | **20/20** |
| "Kahvemi sütsüz içtiğimi not al." | `memory_add` | 20/20 | **20/20** |
| "Bundan sonra bana hep kısa cevap ver." | `soul_add` | 20/20 | **20/20** |

Yanlış tool **sıfır**, dağılımda başka hiçbir ad yok. **Tool seçiminde iki model ayırt edilemez.**
Sıra kuralı (`pi_brain.py:148`) 12B'de de tutuyor — eylem tool'ları web_search'ten önde.

> `soul_kalip` koşusu **ATLANDI** (görev talimatı). 26B turunda confound tespit edilmişti:
> `soul.md` zaten "kısa ve öz cevap ver" içeriyor → "Bana kısa cevap ver." modele göre zaten kayıtlı.
> Düzeltilirse **26B'de de tekrar koşulmalı**, yoksa karşılaştırma adil olmaz.

---

## 4. Tool HATA sonucunu yutma (N=10) — **TEK GERÇEK DAVRANIŞ FARKI**

Kurulum: model `soul_add` çağırıyor, tool sonucu `hata: guest: ruh kaydı yok` dönüyor.

| sonuç | 26B | **12B** |
|---|---|---|
| **UYDURDU** ("Tamam, bundan sonra hep kısa cevaplar vereceğim.") | **10/10** | **2/10** |
| **TOOL'U YENİDEN ÇAĞIRDI** (`soul_add` `scope:"self"` ile) | 0/10 | **8/10** |
| Hatayı kullanıcıya söyledi | 0/10 | **0/10** |

`ab_bench.py`'nin kelime-tabanlı sınıflandırıcısı bu 8'i **"belirsiz"** diye etiketliyor çünkü
`content` boş. Boş DEĞİL — `finish_reason='tool_calls'`. Ayrı bir tanı koşusuyla (6/6) doğrulandı,
`ab_bench.py` değiştirilmeden:

```
finish='tool_calls' content=''
  -> TOOL: soul_add({"scope":"self","text":"bundan sonra hep kısa cevap ver"})
```

**Yorum:** 12B hatayı yutmuyor, **hataya tepki veriyor** — `guest` scope'u başarısız olunca
`scope:"self"` ile tekrar deniyor. Bu 26B'den **daha iyi** bir davranış (yalan söylemiyor), ama
**ikisi de hatayı kullanıcıya söylemiyor (0/10)** → AGENTS.md'deki delik **kapanmadı**, sadece
şekil değiştirdi. Canlıda pi'nin retry'ı nasıl karşıladığına bağlı: retry başarılıysa kullanıcı
doğru sonucu alır, değilse döngü riski var (**ölçmedim** — canlı pi akışı test edilmedi).

---

## 5. VRAM

| | 26B | **12B** | fark |
|---|---|---|---|
| llama-server (beyin) | ~15,6 GB | **9.194 MiB (~9,0 GB)** | **−6,6 GB** |
| Whisper | 2.386 MiB | 2.386 MiB | — |
| OmniVoice TTS | 4.712 MiB | 4.712 MiB | — |
| **GPU toplam** | ~22,7 GB | **16.311 MiB / 24.576 MiB** | **−6,4 GB** |

Kullanıcının 16.311 MiB ölçümü **teyit edildi**. Boşta kalan alan: **8,3 GB**
(26B'de ~1,9 GB idi). 26B'nin VRAM'e sığdığı düşünülürse 12B'nin tek kazancı **yer**.

---

## 6. Özet — kalite hariç her şeyde

| metrik | N | 26B | 12B | kazanan |
|---|---|---|---|---|
| kalite — kör karşılaştırma | 10 | — | — | ***kullanıcı karar verecek*** |
| reminder_add isabeti | 20 | 20/20 | 20/20 | berabere |
| memory_add isabeti | 20 | 20/20 | 20/20 | berabere |
| soul_add isabeti (kalıplı) | 20 | 20/20 | 20/20 | berabere |
| kontrol: sohbette yanlış tool | 20 | 0/20 | 0/20 | berabere |
| TTFT soğuk @~3,7k | 5 | 2,17 s | 3,95 s | **26B** |
| TTFT soğuk @~17k | 5 | 12,65 s | 19,05 s | **26B** |
| TTFT sıcak (cache hit) | 5 | 0,11 s | 0,17 s | berabere (ikisi de anlık) |
| prompt eval (soğuk) @~17k | 5 | 1.341 t/s | 890 t/s | **26B** |
| decode — kısa onay (canlı rejim) | 5 | 151 t/s | 121 t/s | **26B** |
| decode — uzun hikâye | 5 | 74,5 t/s | 45,2 t/s | **26B** |
| `bench.py` medyan (300 tok) | 5 | 48,4 t/s | 36,4 t/s | **26B** |
| MTP draft kabul (kısa rejim) | 5 | 47% | 88% | 12B (ama hıza yansımıyor) |
| hata yutma — uydurma | 10 | 10/10 | 2/10 | **12B** |
| hata yutma — kullanıcıya söyledi | 10 | 0/10 | 0/10 | berabere (ikisi de kötü) |
| VRAM (beyin) | — | ~15,6 GB | ~9,0 GB | **12B** |

**12B'nin tek net kazancı: 6,6 GB VRAM + hata karşısında uydurmama.
26B hızda her rejimde önde. Kalite kararı kullanıcıda.**

---

## 7. worker izlenimi (BAĞLAYICI DEĞİL — kullanıcı hakem)

- **Blok 8'de 12B olgusal hata yaptı:** *"Suyun kaynama noktası deniz seviyesinde **yüz elli**
  derecedir."* Doğrusu 100 °C; 26B doğru söyledi. Tek örnek, stokastik → tek başına kanıt değil,
  ama tam da "kısa olgusal soru" bloğunda çıkması dikkat çekici. **Kör karşılaştırmada bu blok
  için tekrar koşu isteyebilirsin.**
- **Blok 5:** 26B "Havva" adını kullandı, 12B "annen" dedi. İkisi de doğru ve tarih söylemedi;
  ad kullanmak daha sıcak mı yoksa fazla mı — senin kararın.
- 26B'de yakaladığım kural ihlali gibi *görünen* noktalar 12B'de **azaldı**: "ben buradayım"
  kalıbı (AGENTS.md yasaklıyor) 12B'de blok 1'de **yok**; blok 9'da istenmemiş "tatil planı
  bakalım" teklifi **yok**. Ama 12B blok 3'te "Senin günün nasıl başladı?", blok 10'da
  "...mi istersin?" ile **takip sorusu** sordu — 26B de blok 3'te "Sen nasılsın?" demişti.
  Takip sorusu yasağını **ikisi de** delen bir kural; modelden çok prompt sorunu gibi duruyor.
- Hız farkı canlıda **sıcak turda hissedilmez** (0,17 s TTFT), ama **soğuk ilk turda 19 s**
  sesli asistan için uzun. Konuşma başlangıcındaki sessizlik 26B'ye göre ~6,4 s daha uzayacak.

---

## 8. Emin OLMADIĞIM yerler

1. **Kalite tek örnek.** Her blok 1 kez koştu, sıcaklık varsayılan (stokastik). Blok 8'in
   "yüz elli derece" hatası şanssız çekiliş de olabilir, sistematik de. **Tekrar koşu gerekir.**
2. **26B VRAM (~15,6 GB) kendi ölçümüm değil** — `bench/ab-protokol.md`'den alındı. 12B canlıyken
   26B'yi ölçemem (servis değiştirmek yasak). Fark yönü kesin, rakam ±0,5 GB oynayabilir.
3. **12B'nin tool-retry davranışının canlıdaki sonucunu ölçmedim.** pi retry'ı nasıl karşılıyor,
   `scope:"self"` başarılı mı, döngüye giriyor mu — bilmiyorum. Canlı akış testi yapılmadı (sesli test yasak).
4. **MTP kabul oranı gün içinde oynuyor** (26B canlı journal: %12,5-%100). Decode sayıları bu orana
   bağlı, ±%30 oynayabilir. Yine de 26B-12B farkı 3 bağımsız koşuda (gecikme, decode_rejim, bench.py)
   aynı yöne çıktı → yön güvenilir.
5. **12B canlı journal snapshot'ı yok.** 26B'de 66 örneklik bağımsız canlı doğrulama vardı
   (`brain-26b-journal.log`); 12B daha yeni canlı, gerçek kullanım verisi birikmedi. Sayılarım
   sentetik koşulardan — canlı kullanım birkaç gün sonra teyit edilmeli.
6. **`bench.py` run1 = 74 t/s outlier** (diğerleri 33-46). Muhtemelen cache/ısınma etkisi;
   medyan (36,4) kullandım, ortalama alsam 44,9 çıkardı. 26B'de aynı outlier deseni yoktu.
</content>
