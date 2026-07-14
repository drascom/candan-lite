# Router deneyleri — model, motor ve şema seçiminin kaydı

Bu dosya bir **kaynak belgesidir**. "Neden bu modeli seçtik, neyi denedik, ne öğrendik"
sorusuna geri dönüp bakmak için tutuluyor. Buradaki bütün sayılar
`experiments/router-bench/` altındaki ham `results*.json` dosyalarından ve `.25`
sunucusundaki `/root/router-bench/` arşivinden okunmuştur; hiçbiri hafızadan yazılmadı.

Son güncelleme: 2026-07-14.

---

## 0. Karar kutusu

| | |
|---|---|
| **Model** | **Qwen3.5-4B-Instruct, Q8_0 (GGUF)** |
| **Motor** | **llama.cpp / `llama-server`** (Ollama değil) — systemd: `candan-router.service`, port 8080 |
| **Çıktı kontrolü** | `response_format: json_schema` → GBNF grammar. Şema: `{tool: <enum>\|null, args: {}, multi_intent: bool}` |
| **Katalog** | Router'a **yalnızca 23 low-tier tool** gösterilir. 7 high-tier tool katalogdan **çıkarılır** (prompt'ta yasaklanmaz — hiç gösterilmez). |
| **Gecikme** | Lab p50 **400 ms**; canlı gölge modda ölçülen **197–483 ms** (çoğu 200–330 ms) |
| **VRAM** | **5.500 MiB** (ölçüldü, `nvidia-smi`). Whisper + TTS + diarize ile birlikte 14.852 / 24.576 MiB |
| **Başarısızlık modu** | Router **her** hatada (servis kapalı, timeout, bozuk JSON, abstain, `multi_intent`) **sessizce ana modele düşer**. Güvenli başarısızlık. |

**Neden Qwen3.5-4B:** tek başına hem yüksek recall'ı hem yüksek abstain'i hem de
**Türkçeyi** aynı anda tutturan tek aday. Rakipleri (xLAM-2-3b, Nemotron-3-Nano-4B,
Ministral-3-3B) ya tuzaklarda çöktü ya Türkçede çöktü ya da her ikisi.

---

## 1. Motivasyon

Ana LLM (pi.dev) tur başına ~2,6 sn. Kullanıcının her "ışığı aç" demesinde bu bedeli
ödemek istemiyoruz. Fikir: ana modelin **önüne** küçük, lokal bir router koymak. Router'ın
tek işi üç soruyu yanıtlamak:

1. Bu cümle bir tool gerektiriyor mu?
2. Gerekiyorsa hangisi?
3. Argümanları ne?

Emin değilse **abstain** eder ve iş sessizce ana modele düşer. Yani router'ın kötü günü,
sistemin bugünkü normal günüdür — **güvenli başarısızlık**. Bu tasarım kararı bütün
değerlendirme metriklerini belirledi: *yanlış tool çağırmak, tool çağırmamaktan çok daha
pahalıdır.* Recall'ı değil, **abstain'i** kovaladık.

---

## 2. Deney altyapısı

İki nesil test seti var. **Turlar arası sayıları karşılaştırmayın** — ölçekler farklı.

| | Set A (Tur 1–3) | Set B (Tur 4+) |
|---|---|---|
| Vaka | 35 | **137** |
| Katalog | 20 uydurma tool (Türkçe adlar: `ev_isik_kontrol`, `medya_cal`…) | **Gerçek katalog: 30 tool** (`worker/tool_catalog.py`) |
| Tuzak | 9 | **50** (%36,5) |
| Dil | TR, sonra EN | EN **ve** TR (aynı vakalar, iki dilde) |
| Harness | `bench.py`, `bench_*_en.py`, `bench2.py` (arşiv, sunucuda) | `bench3.py`, `bench4.py` |

### Set B'nin dürüst dökümü

Katalogdaki 30 tool'un hepsi gerçek değil — bu bilinçli:

| origin | adet | ne demek |
|---|---|---|
| `real` | **8** | bugün worker'da gerçekten çalışan (`memory_add`, `memory_search`, `soul_add`, `memory_consolidate`, `reminder_add`, `reminder_list`, `reminder_cancel`, `web_search`) |
| `planned` | **2** | yakında gelecek (`message_leave`, `intercom_open`) |
| `invented` | **20** | **uydurma** — sırf router'ı zorlamak, semantik komşu tuzağı kurmak ve katalog büyüklüğünün etkisini ölçmek için |

Tier dağılımı: **23 low / 7 high**. High-tier = geri alınamaz veya insanı ilgilendiren
eylemler: `money_send`, `mail_send`, `message_leave`, `intercom_open`, `calendar_delete`,
`reminder_cancel`, `memory_consolidate`.

### Vaka kategorileri (137)

| kategori | n | ne ölçer |
|---|---|---|
| `tool` | 35 | düz tool seçimi |
| `pair` | 16 | birbirine yakın tool çiftleri (`memory_add` vs `soul_add`…) |
| `arg` | 17 | argüman çıkarımı (tarih/saat/kişi) |
| `high` | 13 | high-tier istekler → **low tier'da abstain edilmeli** |
| `multi` | 6 | çok-niyetli cümleler |
| `trap_neigh` | 20 | **semantik komşu tuzağı** — katalogda olmayan bir cihaz/eylem ("kombiyi aç") |
| `trap_chat` | 14 | düpedüz sohbet |
| `trap_ctx` | 8 | bağlam gerektiren ("onu 5 yap") |
| `trap_know` | 8 | genel bilgi sorusu ("Sefiller'i kim yazdı") |

Abstain beklenen toplam (low tier'da): **69/137 = %50,4**. Saf `trap_*`: **50/137 = %36,5**.

### Metrik sözlüğü

- **recall** — tool gereken vakada doğru tool.
- **arg** — doğru tool **ve** kabul edilebilir argümanlar.
- **TUZAK-abst** — 50 `trap_*` vakasında doğru abstain oranı. **En önemli metrik.**
- **high-abst / high-FIRE** — 13 high vakada abstain / **ateşleme** oranı. `high-FIRE` = yanlış eylem riski.
- **multi-TAM** — çok-niyetli cümlede her iki niyetin de karşılanması.
- **old35** — eski 35'lik setin regresyon skoru (Set A ile devamlılık için).

Eşikler (`results.json`, en baştan sabit): `tool_sel >= 80`, `chat_abstain >= 80`,
**`trap_wrong <= 20`**.

---

## 3. Tur 1 — ilk elemeler (Set A, Türkçe)

`/root/router-bench/results.json` — 20 tool, 35 vaka, TR cümleler.

| model | recall | abstain | tuzak-yanlış | p50 |
|---|---|---|---|---|
| needle (26M) | — | — | — | — |
| MiniCPM5-1B-Agentic | 37,5 | **0** | **9/9 (%100)** | 172 ms |
| Qwen3-1.7b | 62,5 | 90 | **4/9 (%44)** | 259 ms |
| Hammer2.1-1.5b | 50,0 | **100** | **0/9 (%0)** | **128 ms** |

- **needle (26M):** bench'e bile giremedi. Abstain kanalı yok, Türkçe yok, Ollama'ya oturmuyor. Elendi (ölçüm dosyası yok).
- **MiniCPM5-1B:** hiç abstain etmiyor, 9 tuzağın 9'unda uyduruyor. Elendi.
- **Qwen3-1.7b:** recall fena değil ama tuzakların %44'ünde tehlikeli uydurma.
- **Hammer2.1-1.5b:** en güvenli (abstain %100, sıfır yanlış çağrı, 128 ms) ama recall %50 — çok düşük.

### Dil dersi (bu turun en değerli çıktısı)

`/root/router-bench/results_hammer_en.json` — Hammer2.1-1.5b, tek değişken **dil**:

| varyant | recall |
|---|---|
| TR cümle + TR tool açıklaması (Tur 1 temeli) | 50,0 |
| EN cümle + **TR** tool açıklaması | 43,8 |
| **EN cümle + EN tool açıklaması** | **68,8** |

Aynı model, aynı set, aynı donanım. Sadece dil hizalaması → **recall %50 → %68,8**.
Buradan "Whisper translate → İngilizce router" planı doğdu.

> **Bu plan sonradan İPTAL EDİLDİ** (bkz. Tur 4). Kazanan model çok dilli çıktı ve
> çeviri katmanına gerek kalmadı. Tool katalogu yine de **İngilizce** kaldı — üretimde de
> öyle (açıklamalar koddan geliyor).

---

## 4. Ara deney — kullanıcının "chat tool" fikri (ELENDİ)

**Hipotez:** MiniCPM abstain edemiyor çünkü abstain'i *ifade edecek kanalı* yok. Kataloğa
bir catch-all `chat()` tool'u ekleyelim; "hiçbiri uymuyor" diyebilsin.

Kurulum: aynı 35 vaka, katalog 20 → **21** tool.

| model | recall (baz → chattool) | abstain | tuzak-yanlış | `chat()` çağrısı |
|---|---|---|---|---|
| MiniCPM5-1B | 56,2 → 68,8 | 0 → 20 | **9/9 → 9/9** | **0** |
| Qwen3-1.7b | 68,8 → **62,5** | 100 → 100 | **0/9 → 2/9** | **0** |
| Hammer2.1-1.5b | 68,8 → 68,8 | 100 → 100 | 0/9 → 0/9 | **0** |
| xLAM-2-3b | 87,5 → 87,5 | 90 → 90 | 0/9 → 0/9 | **0** |

**Sonuç:** hiçbir modelde net kazanç yok. Dört koşuda da `chat()` tool'u **35 vakada 0 kez**
çağrıldı (`escape_to_chat: 0`). MiniCPM'in tuzak hatası **9/9'da aynı kaldı**. Qwen3'te
işleri **bozdu** (tuzak 0/9 → 2/9, üstelik bir parse hatası).

> **DERS:** Sorun abstain'i **ifade edememek** değil, *"hiçbir tool uymuyor"* durumunu
> **tanıyamamak**. Modele yeni bir konuşma kanalı açmak, olmayan bir yeteneği yaratmıyor.
> Zararsızdı ama faydasızdı → fikir elendi.

(Bu ders Tur 5'te — `multi_intent` bayrağında — tersine döndü ve çok işe yaradı. Bkz. §9.)

---

## 5. Tur 2 — bir boy büyük (Set A, EN)

| model | recall | arg | abstain | tuzak-yanlış | p50 |
|---|---|---|---|---|---|
| **xLAM-2-3b-fc-r** Q8 | **87,5** | 81,2 | 90 | **0/9** | 390–398 ms |
| Hammer2.1-**3b** Q8 | **62,5** | 62,5 | 100 | 0/9 | 140 ms |

Hammer'ın 3B'si, kendi 1.5B'sinden **kötü** (62,5 vs 68,8 — aynı EN koşulunda).

> **DERS:** Boyut büyütmek her zaman iyileştirmiyor. **Model ailesi, parametre sayısından
> önemli.** Aynı ailenin içinde ölçek büyütmek bedava iyileşme getirmez.

*(Not: eski notlardaki "xLAM 427 ms" değeri p50 değil ortalamadır; p50 = 390–398 ms.)*

---

## 6. Servis katmanı araştırması

Bu, kod yazılmadan önce yapılmış bir literatür/mimari incelemesidir; ürünü doğrudan
belirledi.

**"Ollama mı, C++ mı?" diye bir seçim yok.** Ollama, llama.cpp'nin üstüne yazılmış bir
**kabuktur**. Motor aynı. Asıl sorun kabuğun ne sakladığı:

- Grammar/template kontrolünü **gizler**.
- Kendi (kırık) tool-parser'ını dayatır.
- `num_ctx` varsayılanı 2048 → **istemi sessizce kırpar** (bizim istem 1,9k–2,5k token; ölçüldü: `vram_kv.json`).

**KARAR: `llama-server`.** Sebep tek bir özellik: `response_format: json_schema`.
llama.cpp şemayı **GBNF grammar'a derler** ve sampling anında geçersiz token'ları maskeler.
Sonuç: **model şema dışına çıkamaz.** Geçersiz bir tool adı üretmesi fiziksel olarak
imkânsız. Bu sayede **hiçbir modelin kendi tool-call formatına bağımlı değiliz** — model
değiştirmek bir dosya yolu değiştirmek demek.

**vLLM elendi:**
- `gpu_memory_utilization` **toplam** VRAM'in oranıdır, **boş** VRAM'in değil. Yani yanındaki Whisper'ı **görmez**; KV cache'i ön-tahsis eder ve geri vermez. Paylaşımlı kartta kırılgan.
- GGUF desteği deneysel / out-of-tree.

**vLLM'e geçiş eşiği** (bugün karşılanmıyor): sürekli >8–10 eşzamanlı istek, **veya** ana
LLM'i de GPU'ya almak, **veya** STT/TTS'i başka karta taşımak.

---

## 7. Model taraması (2026 ortası)

Permissive lisanslı, ≤4B, tool-calling iddialı adaylar: **Qwen3.5-4B**,
**Nemotron-3-Nano-4B**, **Ministral-3-3B**, **LFM2-1.2B-Tool**, Gemma-4 (GGUF'u zamanında
hazır değildi), ve yarışa geri dönen **xLAM-2-3b-fc-r**.

VRAM/KV ön ölçümü (`/root/router-bench/vram_kv.json`, 5 paralel slot varsayımıyla):

| model | quant | istem token | VRAM @ctx8192 | KV MiB/token | 5 paralel toplam |
|---|---|---|---|---|---|
| LFM2-1.2B-Tool | Q8_0 | 1877 | 1728 MiB | 0,024 | 1772 MiB |
| Nemotron-3-Nano-4B | Q4_K_M | 2304 | 3188 MiB | 0,036 | 3331 MiB |
| xLAM-2-3b | Q8_0 | 2537 | 3904 MiB | 0,051 | 4165 MiB |
| Ministral-3-3B | Q8_0 | 2338 | 4788 MiB | **0,117** | 5270 MiB |
| **Qwen3.5-4B** | Q8_0 | 1990 | 5034 MiB | 0,044 | **5139 MiB** |

Hepsi 24 GB'lık kartta Whisper+TTS'in yanına sığıyor. VRAM eleyici değil — **kalite eleyici**.

---

## 8. Tur 3 — serbest format vs GRAMMAR (Set A, EN, 35 vaka)

Her aday iki koşulda: `--cond free` (modelin kendi tool-call formatı) ve `--cond grammar`
(JSON şema zorlaması).

| model | koşul | recall | arg | abstain | **tuzak-yanlış** | p50 |
|---|---|---|---|---|---|---|
| **Qwen3.5-4B** Q8 | free | 100 | 100 | 100 | **0/9 (%0)** | 1069 ms |
| **Qwen3.5-4B** Q8 | **grammar** | **100** | **100** | **100** | **0/9 (%0)** | **700 ms** |
| xLAM-2-3b Q8 | free | 87,5 | 81,2 | 90 | **0/9 (%0)** | 398 ms |
| xLAM-2-3b Q8 | grammar | 93,8 | 87,5 | 100 | **1/9 (%11)** | 225 ms |
| Nemotron-3-Nano-4B Q4 | free | 93,8 | 93,8 | 90 | **2/9 (%22)** | 951 ms |
| Nemotron-3-Nano-4B Q4 | grammar | 100 | 100 | 100 | **3/9 (%33)** | 634 ms |
| Ministral-3-3B Q8 | free | 87,5 | 81,2 | 100 | **2/9 (%22)** | 447 ms |
| Ministral-3-3B Q8 | grammar | 81,2 | 75,0 | 100 | **3/9 (%33)** | 384 ms |
| LFM2-1.2B-Tool Q8 | free | 37,5 | 37,5 | 20 | **4/9 (%44)** | 190 ms |
| LFM2-1.2B-Tool Q8 | grammar | 75,0 | 62,5 | 70 | **1/9 (%11)** | 162 ms |

**Kazanan net: Qwen3.5-4B Q8 + grammar — 100/100/100, 0 tuzak hatası, 700 ms, ~5,0 GB.**

### DERS (kritik): GRAMMAR ZORLAMA ABSTAIN'İ BOZUYOR

Şema, modeli **bir şey seçmeye** itiyor:

- Nemotron: 2/9 → **3/9**
- Ministral: 2/9 → **3/9** (recall'ı da düşürdü: 87,5 → 81,2)
- xLAM: 0/9 → **1/9**
- Qwen3.5: 0/9 → **0/9** — **bu etkiye bağışık**

Ve hatalar **rastgele değil**. Ham dosyalardaki tuzak hatalarının **hepsi aynı tool'a**
gidiyor:

```
nemotron/grammar  x01 "turn on the boiler, I'm cold" -> ev_isik_kontrol {"oda":"boiler","durum":"on"}
nemotron/grammar  x07 "close the curtains"           -> ev_isik_kontrol {"oda":"kuruşlar","durum":"off"}
ministral/grammar x04 "set the AC to 22 degrees"     -> ev_isik_kontrol {"oda":"sofa (AC odası)","durum":"22"}
xlam/grammar      x01 "turn on the boiler, I'm cold" -> ev_isik_kontrol {"oda":"klima","durum":"on"}
```

Model, yapamadığı isteği **en yakın semantik komşuya yapıştırıyor**. Kombi yok → ışık var →
ışığı seç. Argümanlar bile saçmalıyor (`"oda": "kuruşlar"`), ama grammar bunu görmüyor:
şema açısından çıktı **kusursuz geçerli**.

> **DERS:** Grammar **format** hatasını çözer, **karar** hatasını artırabilir. Geçerli JSON,
> doğru karar demek değildir. Grammar'ın tek istisnası, serbest formatı zaten kırık olan
> LFM2 oldu (4/9 → 1/9) — orada grammar parser'ın çöpünü temizledi.

**İkinci bulgu:** Ollama `num_ctx=2048`'de istemi **sessizce kırpıyor**. İstemimiz
1,9k–2,5k token. Bütün harness'ler `num_ctx=8192` ile koşuyor (`bench3.py:18`). Ctx ≥ 4096
şart — yoksa ölçtüğünüz şey model değil, kırpılmış bir istem.

---

## 9. Test seti doydu → genişletildi (Set B)

Tur 3'te Qwen 35/35 yaptı. Doymuş bir setle model ayırt edilemez. Set yeniden yazıldı:

- 35 → **137 vaka**
- 9 → **50 tuzak** (%36,5)
- Uydurma 20 tool → **gerçek katalog, 30 tool** (23 low / 7 high; 8 gerçek + 2 planlı + 20 uydurma)
- Kategorili (tool/pair/arg/high/multi/trap_neigh/trap_chat/trap_ctx/trap_know)
- **EN ve TR** — aynı 137 cümle, iki dilde
- Eski 35'lik set `OLD35` olarak korundu (regresyon kontrolü)

Bu rapordaki bütün Tur 4 / Tur 5 sayıları **bu 137 vakalık sete** karşı ölçülmüştür.

---

## 10. Tur 4 — nihai eleme (Set B, `bench3.py`)

Üretim koşulu = **`--cond grammar --tier low`** (high tool'lar katalogda yok).

| model | quant | dil | tier | recall | arg | **TUZAK-abst** | komşu | chat | ctx | know | high-FIRE | old35 | p50 | VRAM |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **qwen35-4b** | Q8_0 | en | **low** | **97** | **96** | **90** | 90 | 100 | 88 | 75 | **15** | 35/35 | 758 | 4,9 GB |
| **qwen35-4b** | Q8_0 | **tr** | **low** | **94** | **84** | **86** | 80 | 100 | 75 | 88 | **23** | 33/35 | 821 | 4,9 GB |
| qwen35-4b | Q8_0 | en | full | 98 | 97 | 88 | 90 | 100 | 75 | 75 | **92** | 34/35 | 827 | 4,9 GB |
| qwen35-4b | Q8_0 | tr | full | 94 | 85 | 88 | 85 | 100 | 75 | 88 | **85** | 33/35 | 815 | 4,9 GB |
| xlam2-3b | Q8_0 | en | low | 93 | 88 | **72** | **65** | 93 | 62 | 62 | 77 | 31/35 | 292 | 3,8 GB |
| xlam2-3b | Q8_0 | **tr** | low | 85 | 75 | **54** | **35** | 86 | 62 | 38 | 77 | 28/35 | 314 | 3,8 GB |
| nemotron3-nano-4b | Q4_K_M | en | low | 94 | 91 | **74** | 65 | 93 | 100 | 38 | 62 | 32/35 | 747 | 3,1 GB |
| nemotron3-nano-4b | Q4_K_M | **tr** | low | **72** | 66 | 78 | 70 | 100 | 100 | 38 | 31 | 30/35 | 724 | 3,1 GB |

Tam tablo (quant varyantları dahil): `experiments/router-bench/TABLE.md`.

### xLAM yarışa geri döndü ve VERİYLE elendi

Geri dönme sebebi: proje bir ürün değil, ev içi kullanım → CC-BY-NC lisansı engel değil.
Ayrıca grammar, xLAM'ın parser kusurunu nötralize ediyordu. Ama Set B'de çöktü:

- **Tuzak hatası %28** (EN) — eşik %20. Kalır kalmaz elendi.
- **Semantik komşu 65** (Qwen 90). En tehlikeli hata sınıfında en kötü.
- **Türkçede çöküyor:** tuzak hatası **%46**, komşu **%35**. Kullanılamaz.
- Tur 3'teki "1/9 vs 0/9" farkı **gürültüymüş** — 9 tuzaklı set bunu ayırt edemiyordu.

### Nemotron elendi

Tuzak hatası %26 (eşik %20 üstü) ve **TR recall 72** — Türkçede kullanılamaz.

### Türkçe kararı: çeviri katmanı GEREKMİYOR

Tur 1'in "Whisper translate → EN router" planı **iptal edildi**. Qwen3.5 Türkçede
recall 94 / arg 84 / tuzak-abstain 86 yapıyor — EN'e göre birkaç puan geride ama üretim
eşiklerinin çok üstünde. Çeviri katmanının gecikmesi ve hata yüzeyi buna değmez.

> Bunu yapabilmemizin **tek sebebi Qwen'in çok dilliliği.** Aynı kararı xLAM veya Nemotron
> ile alamazdık — ikisi de TR'de çöküyor. Model seçimi, mimarinin bir katmanını sildi.

### YETKİ KATALOGLA VERİLİR, PROMPTLA DEĞİL

`--tier full` (7 high tool katalogda) vs `--tier low` (katalogdan çıkarılmış), `high-FIRE`
sütunu:

| dil | tier=full | tier=low |
|---|---|---|
| EN | **%92** | **%15** |
| TR | **%85** | **%23** |

Katalogda `money_send` varken router onu **%85–100 oranında çağırıyor** — prompt'ta ne
yazarsanız yazın. **"Çağırma" demek işe yaramıyor.** Katalogdan çıkardığınız anda yanlış
eylem %92 → %15'e düşüyor.

Bu, `worker/tool_catalog.py`'de mimari kural olarak kodlandı: `router_catalog()` yalnızca
low-tier döndürür, ve bir assert high tool'un router kataloğuna sızmadığını doğrular.

### QUANT, GECİKME KALDIRACI DEĞİL

Aynı koşulda (EN / tier=low / Ollama+grammar) p50:

| quant | p50 | VRAM |
|---|---|---|
| **Q8_0** | **758 ms** | 4,9 GB |
| Q4_K_M | 786 ms | 4,1 GB |
| Q5_K_M | 843 ms | 4,5 GB |
| Q6_K | 874 ms | 4,9 GB |

Q8 **en hızlısı**. Küçültmek hızlandırmıyor — kalite de neredeyse hiç değişmiyor
(Q4 recall 97, Q8 97). Çünkü darboğaz ağırlıklar değil: **~2,4k token'lık statik tool
bloğunun prefill'i** ve motor overhead'i. Gerçek kaldıraçlar: **prefix KV-cache + llama-server**
(bkz. §12). Q8'de kaldık: en iyi kalite, en düşük gecikme, VRAM sığıyor.

---

## 11. Tur 5 — şema deneyi: çok-niyet (`bench4.py`)

**Sorun:** router çok-niyetli cümlelerde **0/6**. "Salondaki ışığı aç ve Neva'ya aşağı
gelmesini söyle" → sadece ışığı açıyor, ikinci niyeti **sessizce düşürüyor**. Bu, kullanıcıya
hiçbir uyarı vermeden yarım iş yapmak demek — sistemin en sinsi hata sınıfı.

Üç şema denendi (Qwen3.5-4B Q8, tier=low, 137 vaka):

| şema | dil | recall | arg | **TUZAK-abst** | komşu | ctx | know | high-abst | **multi-TAM** | old35 |
|---|---|---|---|---|---|---|---|---|---|---|
| **single (baz)** | en | 97 | 96 | **90** | 90 | 88 | 75 | 85 | **0/5** | 35/35 |
| `list` | en | 97 | 96 | **30** | 30 | **0** | **0** | 23 | **5/5** | 26/35 |
| `list_null` | en | 97 | 96 | **54** | 55 | **0** | 38 | 31 | **5/5** | 31/35 |
| **`flag`** | en | **97** | **96** | **90** | 90 | 88 | 75 | 85 | 0/5 | **35/35** |
| **single (baz)** | tr | 94 | 84 | **86** | 80 | 75 | 88 | 77 | **0/5** | 33/35 |
| `list` | tr | 96 | 84 | **20** | 15 | **0** | **0** | 15 | 4/5 | 25/35 |
| `list_null` | tr | 96 | 84 | **42** | 25 | **0** | 25 | 23 | 4/5 | 30/35 |
| **`flag`** | tr | 94 | 82 | **82** | 75 | 75 | 75 | 69 | 0/5 | 32/35 |

### Liste şeması REDDEDİLDİ

`{tools: [...]}` çok-niyeti **çözüyor** (0/5 → 5/5) ama **abstain'i yıkıyor**:
tuzak-abstain %90 → **%30**. Bağlam ve bilgi tuzaklarında **sıfır**. Ham veriden:

```
list/en  (46 hata): memory_add×10, light_control×6, web_search×6, memory_search×4 ...
list/tr  (52 hata): memory_add×20, light_control×9, web_search×5, translate×4 ...
```

Model, boş bir listeyi döndürmek yerine **listeyi doldurmaya çalışıyor**. "Randevuyu sil"
→ `calendar_add`. `{tools: array|null}` varyantı (`list_null`) yarayı sarmaya yetmedi (%54).

### KAZANAN: `multi_intent: bool` bayrağı

Şemaya tek bir boolean eklendi. Tool alanı **tekil kaldı**:

```json
{"tool": "light_control" | null, "args": {...}, "multi_intent": true|false}
```

Router `multi_intent: true` derse → **seçilen tool atılır, iş ana modele düşer.**

| | EN | TR |
|---|---|---|
| multi yakalama | **6/6** | **6/6** |
| yanlış alarm (131 tekil vakada) | **0 (%0,0)** | **1 (%0,8)** |
| recall / arg / tuzak bedeli | **sıfır** (baz ile birebir) | ~3 vakalık küçük bedel |

EN'de **tam bedava**: recall, arg, tuzak-abstain, old35 — hepsi baseline'la birebir aynı.
TR'de küçük bir bedel var (arg 84→82, tuzak 86→82, high-abst 77→69).

> **DERS (en büyüğü):** **Abstain'i TOOL olarak sunmak işe yaramıyor; BAYRAK olarak sunmak
> çalışıyor.** §4'teki `chat()` tool'u başarısız oldu, `list` şeması abstain'i yıktı,
> ama bir boolean bedavaya çalıştı. Modele **eylem alanı** açtıkça (liste = "birden çok şey
> yapabilirsin") **eylemsizlik** yeteneği bozuluyor. Meta-bilgiyi eylem alanının **dışında**,
> ayrı bir kanalda sor.

---

## 12. Canlı entegrasyon — llama-server

systemd: `candan-router.service` (`.25`), port 8080, Qwen3.5-4B Q8_0,
**5.500 MiB VRAM** (ölçüldü).

### `--ubatch-size 128` HAYATİ

llama.cpp prefix KV-cache'ini yeniden kullanırken **ortak önekten bir tam ubatch geri
çekiyor**: `reuse = ortak_önek − n_ubatch`. Statik tool öneki (23 tool) tek başına
**2318 token**. Ölçüldü (unit dosyasında kayıtlı):

| `--ubatch-size` | yeniden kullanılan | prefill | **p50** |
|---|---|---|---|
| 4096 | 0 tok | 1035 ms | 1379 ms |
| 2048 | 412 tok | 910 ms | 1303 ms |
| **512 (VARSAYILAN)** | 1944 tok | 240 ms | **568 ms** |
| 256 | 2200 tok | 127 ms | 409 ms |
| **128 (SEÇİLEN)** | **2318 tok** | **99 ms** | **400 ms** |
| 64 | 0 tok | 1280 ms | 1656 ms (bozuluyor) |

ub=128'de statik öneğin **tamamı** KV'den geliyor; yalnızca değişken kısım (~134 token)
yeniden hesaplanıyor. Varsayılanda bırakmak p50'yi 568 ms yapardı — **%42 daha yavaş**.

### `--ctx-size 20480 --parallel 5`

Slot başına 4096 token. **Ölçüldü:** slot 2560 iken `önek(2318) + cümle + n_predict(256)`
slot'u taşırıyordu → llama-server **context-shift** yapıp KV önbelleğini her turda
**bozuyordu**. 4096'da önek slot'a rahat sığıyor.

### `repeat_penalty: 1.1` ZORUNLU

Ollama bunu **varsayılan olarak uyguluyordu** (`repeat_penalty=1.1, repeat_last_n=64`);
llama.cpp **uygulamıyor**. İstemin kuyruğunda örnek olarak
`{"tool": null, "args": {}, "multi_intent": false}` duruyor — ceza olmadan model bu son
satırı papağan gibi tekrarlıyor ve `multi_intent` hep `false` çıkıyor.

Etkisi: `repeat_penalty` olmadan **`multi_intent` recall'ı %50'ye çöküyor** — yani sessiz
yarım-iş kalkanı **yarı açık** kalıyor. Bu satır silinmemeli (`worker/router.py`).

---

## 13. Gerçek canlı test — gölge mod (2026-07-14)

`ROUTER_ENABLED=true`, `ROUTER_EXECUTE=false`: router karar veriyor, karar loglanıyor, ama
cevabı yine ana model üretiyor. Sıfır risk.

**Gecikme: 197–483 ms** (çoğu 200–330 ms) — laboratuvardan (400 ms p50) bile iyi.

**Doğru kararlar:** `light_control`, `mail_check`, `match_result`, `timer_set`, `weather`,
`media_play`, `shopping_add`. Sohbet ve bağlam cümlelerinde temiz abstain.

**STT çöpünü sindirdi:** Whisper halüsinasyonu ("Bir sonraki videoda görüşmek üzere") →
abstain. Router'a giren şey temiz metin değil, **STT çıktısı** — bu önemli bir dayanıklılık
sinyali.

**En riskli vakayı geçti:** "Neva'ya haber verir misin?" (high-tier `message_leave`, katalogda
yok) → **abstain**. §10'daki katalog kararı canlıda çalıştı.

### Tek gerçek hata — ve laboratuvar bunu ZATEN BİLİYORDU

| cümle | router | olması gereken |
|---|---|---|
| "Kombi aç." | `light_control` | abstain |
| "Perdeleri kapat." | `light_control` | abstain |

Gölge modda zararsız. **Execute açık olsaydı ışıklar yanardı.** Executor izin listesi bunu
**çözmez** — `light_control` zaten izinli bir tool; yanlış olan *karar*, eylem değil.

**Kritik bulgu:** Bu iki cümle laboratuvar setinde zaten var ve Qwen bunları **zaten
kaçırıyordu**. `results3_qwen35-4b-q8_tr_low.json` içindeki 4 `trap_neigh` hatasının
ikisi tam olarak bunlar:

```
[x01] "kombiyi aç, üşüdüm"          -> light_control
[x07] "perdeleri kapat"             -> light_control
[n02] "televizyonun sesini kıs"     -> volume_set
[n09] "alışveriş listesini yazdır"  -> shopping_list
```

Yani canlı test **yeni bir hata bulmadı** — laboratuvarın ölçtüğü hatayı (TR komşu = %80,
yani %20 kaçırma) **doğruladı**. Bu iyi haber: **test seti gerçeği yakalıyor.** Kötü haber:
%80 semantik-komşu skoru, gerçek hayatta duvara asılacak kadar iyi değil ve
`light_control` her seferinde aynı çöp kovası.

Canlıdan gelen **birebir cümleler** de sete eklendi (`n13` "kombi aç", `n14` "perdeleri
kapat") — `x01`/`x07` ile aynı tuzağın saha telaffuzu. Bundan sonraki koşularda set
139 vaka / 52 tuzak olacak.

---

## 14. ÖĞRENİLEN DERSLER

Bu bölüm raporun asıl ürünüdür.

1. **Yanlış eylem, eylemsizlikten pahalıdır.** Router'ı recall'a göre değil, **abstain'e**
   göre seçtik. Bütün metrik tasarımı (%37 tuzak) bunun üzerine kuruldu. Güvenli
   başarısızlık (ana modele düşme) bunu bedava yapıyor.

2. **Dil hizalaması ölçülebilir bir kaldıraçtır.** Aynı model, sadece dil hizalamasıyla
   %50 → %68,8 recall (Hammer). Ama nihayetinde **doğru model seçmek, çeviri katmanı
   eklemekten iyidir**: Qwen'in çok dilliliği, planlanmış bir mimari katmanı **sildi**.

3. **Model ailesi > parametre sayısı.** Hammer 3B, Hammer 1.5B'den kötü çıktı. Büyütmek
   otomatik iyileşme değil.

4. **Grammar FORMAT hatasını çözer, KARAR hatasını artırabilir.** Şema, modeli "bir şey
   seçmeye" iter. Nemotron/Ministral/xLAM'da abstain **bozuldu**. Geçerli JSON ≠ doğru karar.
   Yine de grammar vazgeçilmez: onsuz her modelin kırık parser'ına bağımlı kalırdık. Bedeli
   **grammar'a bağışık bir model seçerek** ödedik (Qwen 0/9 → 0/9).

5. **Semantik komşu yapıştırma, tek büyük hata modudur.** Model yapamadığı isteği
   yapabildiği **en yakın** şeye yapıştırır. Hatalar rastgele değil — hepsi aynı kovaya
   (`light_control`) gider. Kombi yok → ışık var → ışığı yak. Argümanlar saçmalasa bile
   (`{"oda": "kuruşlar"}`) şema geçerlidir; hiçbir doğrulama katmanı bunu yakalamaz.

6. **Abstain'i TOOL olarak sunmak işe yaramıyor; BAYRAK olarak sunmak çalışıyor.**
   `chat()` catch-all tool → faydasız (0 kez çağrıldı). `{tools: [...]}` listesi → abstain'i
   yıktı (%90 → %30). `multi_intent: bool` → **bedavaya çalıştı** (6/6, %0 yanlış alarm).
   Genel kural: modele **eylem alanı** açtıkça **eylemsizlik** yeteneği bozulur. Meta-bilgiyi
   eylem alanının dışında, ayrı bir kanalda sor.

7. **YETKİ KATALOGLA VERİLİR, PROMPTLA DEĞİL.** Katalogda `money_send` varsa router onu
   %85–100 çağırır — prompt'ta ne yazarsanız yazın. Katalogdan çıkarınca yanlış eylem
   %92 → %15. Bir tool'u "yasaklamak" istiyorsan **gösterme**.

8. **Quant, gecikme kaldıracı değil.** Aynı koşulda Q8 (758 ms) Q4'ten (786 ms) **hızlı**.
   Darboğaz ağırlıklar değil, **statik tool bloğunun prefill'i** (2318 token) ve motor
   overhead'i. Gerçek kaldıraçlar: **prefix KV-cache** ve **doğru motor**.

9. **"Ollama mı C++ mı" diye bir soru yok** — Ollama llama.cpp'nin kabuğudur. Soru şu:
   *kabuk senden neyi saklıyor?* (grammar kontrolü, `num_ctx`, `repeat_penalty`, kendi kırık
   tool parser'ı). Ollama'dan llama-server'a geçiş: **~800 ms → 400 ms**, ve şema kontrolü
   bizde.

10. **Motoru değiştirdiğinde sessiz varsayılanları taşımayı unutma.** Ollama
    `repeat_penalty=1.1` uyguluyordu; llama.cpp uygulamıyor. Bunu taşımayı unutsaydık
    `multi_intent` sessizce %50'ye düşecekti — kalkan **yarı açık**, hiçbir hata mesajı yok.
    En tehlikeli regresyonlar, hata vermeyenlerdir.

11. **Ölçüm parametresi, ölçülen şeyi değiştirebilir.** Ollama `num_ctx=2048` varsayılanı
    istemi **sessizce kırpıyordu** (istem 1,9k–2,5k token). Ctx ≥ 4096 olmadan ölçtüğünüz
    şey model değil, kırpılmış bir istem.

12. **Doymuş test seti model ayırt edemez.** Tur 3'te Qwen 35/35 yaptı; xLAM ile arasındaki
    "1/9 vs 0/9" farkı **gürültüydü**. Set 137 vakaya çıkınca aradaki uçurum göründü
    (tuzak %90 vs %72; TR'de %86 vs %54). **Aday elemeden önce setinin ayırt edebildiğinden
    emin ol.**

13. **İyi bir test seti, canlı hatayı önceden söyler.** Canlıdaki iki hata ("Kombi aç",
    "Perdeleri kapat") setin içinde zaten vardı ve lab bunları zaten kaçırıyordu. Canlı test
    yeni bilgi vermedi — **setin geçerliliğini** doğruladı. Buradan çıkan sonuç: setteki
    kalan %14 tuzak hatası da gerçek, ve er geç sahada görünecek.

---

## 15. Nihai üretim konfigürasyonu

**Model:** Qwen3.5-4B-Instruct, **Q8_0** GGUF.
Model dosyası `.25`'te `/opt/models/qwen35-4b-instruct-q8_0.gguf` — Ollama blob'unun
**kopyası**, bilerek ayrıldı (`ollama rm` router'ı öldürmesin).

**Motor:** llama.cpp `llama-server`, systemd `candan-router.service`:

```
--model /opt/models/qwen35-4b-instruct-q8_0.gguf --alias qwen35-4b-q8
--host 0.0.0.0 --port 8080
--ctx-size 20480 --parallel 5      # 5 slot × 4096 token (önek 2318 tok sığsın)
--n-gpu-layers 99 --flash-attn on
--batch-size 2048 --ubatch-size 128   # HAYATİ — bkz. §12
--temp 0 --no-webui
```

**İstek (`worker/router.py`):**

```
json_schema    = {tool: <enum(23)>|null, args: {}, multi_intent: bool}   # grammar
cache_prompt   = true        # statik tool önekinin KV-cache'i → prefill ~99ms
temperature    = 0.0
n_predict      = 256
repeat_penalty = 1.1         # SİLME — multi_intent'i ayakta tutan ayar
```

**Katalog:** `worker/tool_catalog.py` → `router_catalog()` yalnızca **23 low-tier** tool
döndürür. 7 high-tier tool router'a **hiç gösterilmez**.

**Knob'lar (`worker/.env.example`):**

| değişken | varsayılan | not |
|---|---|---|
| `ROUTER_ENABLED` | `false` | router'ı çalıştır |
| `ROUTER_EXECUTE` | `false` | **gölge mod** — karar loglanır, ana model yine cevaplar |
| `ROUTER_URL` | `http://192.168.0.25:8080` | |
| `ROUTER_TIMEOUT_MS` | `1500` | aşılırsa ana modele düş (ölçülen p50 ~400 ms) |
| `ROUTER_LOG_PATH` | `logs/router-decisions.jsonl` | karar defteri |

**VRAM (ölçüldü, `nvidia-smi --query-compute-apps`):**

| süreç | MiB |
|---|---|
| llama-server (router) | **5.500** |
| OmniVoice TTS | 4.680 |
| Whisper | 2.386 |
| moss-diarize | 2.286 |
| **toplam** | **14.852 / 24.576** |

**Başarısızlık davranışı:** router NE ŞEKİLDE olursa olsun başarısız olursa (servis kapalı,
timeout, HTTP hatası, bozuk JSON, abstain, `multi_intent=true`, executor yok) →
**sessizce ana modele düşülür.**

---

## 16. Elenenler ve neden

| aday | tur | neden elendi |
|---|---|---|
| **needle (26M)** | 1 | Abstain kanalı yok, Türkçe yok, Ollama'ya oturmuyor. Bench'e giremedi. |
| **MiniCPM5-1B-Agentic** | 1 | Abstain **%0**. 9 tuzağın **9'unda** uyduruyor. `chat()` tool'u eklendiğinde bile 9/9. |
| **Qwen3-1.7b** | 1 | Tuzakların **%44'ünde** tehlikeli uydurma. Recall 62,5. |
| **Hammer2.1-1.5b** | 1 | En güvenli (abstain %100, 128 ms) ama **recall %50** — çok düşük. |
| **Hammer2.1-3b** | 2 | Recall **%62,5** — kendi 1.5B'sinden kötü. |
| **LFM2-1.2B-Tool** | 3 | Serbest formatta recall 37,5 / abstain 20. Grammar ile 75/70'e çıktı ama hâlâ sınıfın altında. |
| **Ministral-3-3B** | 3 | Grammar tuzağı **%33**. Grammar recall'ı da **düşürdü** (87,5→81,2). KV maliyeti en yüksek (0,117 MiB/tok). |
| **Nemotron-3-Nano-4B** | 4 | Tuzak hatası **%26** (eşik %20). **TR recall 72** — Türkçede kullanılamaz. |
| **xLAM-2-3b-fc-r** | 4 | Tuzak hatası **%28**. Semantik komşu **65**. **TR'de çöküyor (%46 tuzak hatası)**. Hızlıydı (292 ms) ama güvenli değil. |
| **`chat()` catch-all tool** (fikir) | ara | 35 vakada **0 kez** çağrıldı. Hiçbir modelde kazanç yok; Qwen3'te zarar. |
| **`{tools: [...]}` liste şeması** (fikir) | 5 | Multi'yi çözüyor (5/5) ama **abstain'i yıkıyor** (%90→%30). `list_null` varyantı da yetmedi (%54). |
| **vLLM** (motor) | — | `gpu_memory_utilization` **toplam** VRAM oranı → Whisper'ı görmez, KV'yi ön-tahsis eder, geri vermez. GGUF desteği deneysel. |
| **Ollama** (motor) | — | llama.cpp kabuğu; grammar/`num_ctx`/`repeat_penalty` kontrolünü saklıyor, kırık tool parser'ı dayatıyor. p50 ~800 ms (llama-server 400 ms). |
| **Whisper translate katmanı** (mimari) | 4 | **Gereksiz** — Qwen çok dilli. TR'de recall 94. Katman silindi. |

---

## 17. Açık sorular / devam edenler

### `unsupported_request: bool` bayrağı — KOŞUYOR, SONUÇ YOK

§13'teki tek gerçek hatanın (semantik komşu yapıştırma: "Kombi aç" → `light_control`)
panzehiri olarak `multi_intent` numarası tekrarlanıyor: şemaya ikinci bir boolean ekle.

```json
{"tool": ..., "args": {...}, "multi_intent": bool, "unsupported_request": bool}
```

`unsupported_request: true` → tool atılır, ana modele düşülür. Hipotez, §14/6'daki derse
dayanıyor: *meta-bilgiyi eylem alanının dışında, ayrı bir kanalda sor.*

`bench4.py`'de `--schema flag2 / flag2b` olarak kodlandı. **Deney şu an koşuyor; sonucu
henüz yok.** Bu bölüm sonuç geldiğinde güncellenecek.

### Diğer açıklar

- **`multi_intent` TR bedeli.** EN'de bedava, TR'de ~3 vakalık kayıp (arg 84→82, tuzak 86→82, high-abst 77→69). Kabul edildi ama anlaşılmadı.
- **`ROUTER_EXECUTE=true` açılmadı.** Semantik komşu hatası (§13) çözülmeden gerçek eylem açılmayacak. Gölge mod devam.
- **`trap_ctx` (bağlam) %75–88.** Router tek cümle görüyor, konuşma geçmişini görmüyor. "Onu 5 yap" → `volume_set {"level": 5}`. Bugün bilinçli bir sınır; router'a geçmiş vermek prefix KV-cache'i bozar.
- **`trap_know` (genel bilgi) %75.** "Sefiller'i kim yazdı" → `web_search`. Teknik olarak yanlış değil ama gereksiz — ana model zaten biliyor.

---

## 18. Ekler

### A. Dosya yolları

**Repo (`/Users/drascom/work/candan-lite/`):**

| yol | ne |
|---|---|
| `experiments/router-bench/router_set.py` | **Test seti** (137 vaka) + gerçek katalog kopyası |
| `experiments/router-bench/bench3.py` | Tur 4 harness (model × dil × tier × free/grammar) |
| `experiments/router-bench/bench4.py` | Tur 5 harness (şema deneyi: single/list/list_null/flag/flag2) |
| `experiments/router-bench/table3.py` | `results3_*.json` → ana tablo + semantik-komşu analizi |
| `experiments/router-bench/table4.py` | `results_qwen35_*.json` → şema kıyas tablosu |
| `experiments/router-bench/TABLE.md` | Tur 4'ün üretilmiş tam tablosu + hata örnekleri |
| `experiments/router-bench/timing.py` | prefill/decode ayrıştırması |
| `experiments/router-bench/results3_*.json` | **Tur 4 ham sonuçları** |
| `experiments/router-bench/results_qwen35_*.json` | **Tur 5 ham sonuçları** |
| `worker/tool_catalog.py` | 30 tool, tier + origin, `router_catalog()`, `router_json_schema()` |
| `worker/router.py` | llama-server istemcisi, karar/düşme mantığı, karar defteri |
| `worker/.env.example` | `ROUTER_*` knob'ları |

**Sunucu (`.25`, `ssh root@192.168.0.25`):**

| yol | ne |
|---|---|
| `/root/router-bench/` | Yukarıdakilerin hepsi **+ eski turların arşivi** |
| `/root/router-bench/results.json` | **Tur 1** (TR, 35 vaka, 3 model) |
| `/root/router-bench/results_hammer_en.json` | **Dil deneyi** (TR/EN tool açıklaması) |
| `/root/router-bench/results_*_chattool.json` | **`chat()` tool deneyi** (4 model) |
| `/root/router-bench/results_*_en{,_grammar}.json` | **Tur 3** (free vs grammar, 5 model) |
| `/root/router-bench/vram_kv.json` | VRAM / KV-per-token / 5-paralel projeksiyonu |
| `/root/router-bench/bench.py`, `bench2.py`, `bench_*_en.py` | Eski turların harness'leri |
| `/etc/systemd/system/candan-router.service` | **Üretim servisi** (ubatch ölçümleri yorum olarak içinde) |
| `/opt/models/qwen35-4b-instruct-q8_0.gguf` | Model ağırlıkları |

### B. Reprodüksiyon

Bench'ler **Ollama** üzerinden koşar (`localhost:11434`) — üretim llama-server'ı
(`:8080`) etkilemez, ama aynı GPU'yu kullanır.

```bash
ssh root@192.168.0.25
cd /root/router-bench

# Tur 4 — üretim koşulu (grammar + low tier), EN ve TR
python3 bench3.py --model qwen35-4b-q8 --cond grammar --lang en --tier low \
        --out results3_qwen35-4b-q8_en_low.json
python3 bench3.py --model qwen35-4b-q8 --cond grammar --lang tr --tier low \
        --out results3_qwen35-4b-q8_tr_low.json

# Yetki deneyi: high tool'lar katalogda (tier=full) -> high-FIRE fırlar
python3 bench3.py --model qwen35-4b-q8 --cond grammar --lang en --tier full \
        --out results3_qwen35-4b-q8_en_full.json

# Tur 5 — şema deneyi
python3 bench4.py --schema single --lang en --out results_qwen35_en_single.json
python3 bench4.py --schema list   --lang en --out results_qwen35_en_list.json
python3 bench4.py --schema flag   --lang en --out results_qwen35_en_flag.json

# Tabloları üret
python3 table3.py   # -> TABLE.md içeriği
python3 table4.py   # -> şema kıyas tablosu
```

`bench3.py` seçenekleri: `--model` (bkz. dosyadaki `MODELS`), `--cond free|grammar`,
`--lang en|tr`, `--tier full|low`, `--out`.
`bench4.py` seçenekleri: `--schema single|list|list_null|list_guard|flag|flag2|flag2b`,
`--lang`, `--tier`, `--out`.

Her koşu başında model yüklenir + 3 warmup, sonunda `keep_alive=0` ile boşaltılır.
`num_ctx=8192` sabit (2048'de Ollama istemi sessizce kırpar).

### C. Ham veri ile eski notlar arasındaki düzeltmeler

Bu rapor yazılırken ham dosyalar teyit edildi. İki yerde eski notlar ham veriyle
çelişiyordu; **ham veri esas alındı**:

1. **Quant gecikme kıyası.** Notlarda "Q8→Q4 p50 827→786 ms" yazıyor. Bu **elma-armut**:
   827 ms `en/full` koşulunun, 786 ms `en/low` koşulunun sayısı. **Aynı koşulda** (en/low):
   Q8 = **758 ms**, Q4 = **786 ms**, Q5 = 843, Q6 = 874. Yani **Q8 en hızlısı** ve
   küçültmek gecikmeyi **artırıyor**. Sonuç ("quant gecikme kaldıracı değil") değişmiyor,
   hatta güçleniyor.

2. **Canlıdaki "tek gerçek hata" yeni değil.** Notlarda "Kombi aç / Perdeleri kapat"
   hatası canlı testin bulgusu gibi anlatılıyor. Ham veriye göre bu iki cümle
   (`x01`, `x07`) test setinde **zaten var** ve `results3_qwen35-4b-q8_tr_low.json`'da
   Qwen bunları **zaten kaçırıyordu**. Canlı test yeni bir hata bulmadı;
   laboratuvarın ölçtüğü hatayı doğruladı. (Bu, setin geçerliliği açısından **iyi** haber.)

Ayrıca üç küçük düzeltme: (a) Tur 2'de xLAM için not edilen "427 ms" p50 değil
**ortalamadır** (p50 = 390–398 ms); (b) `chat()` tool'u notlarda "0–2 kez çağrıldı"
deniyor, ham veride dört koşuda da **tam 0** (`escape_to_chat: 0`); (c) Ollama ctx için
notlarda "≥4096 şart" deniyor, harness'ler fiilen **8192** kullanıyor.

**Bir tuzağa da bu rapor yazılırken düşüldü, not edelim:** çalışma ağacındaki
`router_set.py` o sırada 139 vaka gösteriyordu ve bu bir an için "notlar yanlış, set 139"
diye yazıldı. Yanlış olan notlar değildi — **eşzamanlı bir değişiklik** setin sonuna canlı
hataları (`n13`, `n14`) ekliyordu. Ölçümlerin koştuğu sürüm **137 vakalık** olandır
(`git show HEAD:experiments/router-bench/router_set.py`). Ders: **sonuçları, ölçümün koştuğu
commit'e göre oku** — çalışma ağacı altından kayabilir.
