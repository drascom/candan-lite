# A/B protokolü — 26B vs 12B yerel beyin

Bu dosya **protokolün kendisi**. 12B turunda birebir tekrar çalıştırılacak.
(Handoff'un işaret ettiği `scratchpad/ab-diyalog.md` geçici dizindeydi ve uçtu; kalıcı olan bu.)

**Öncelik sırası: (1) cevap kalitesi → (2) hız → (3) tool seçimi/hatalar.**
Kaliteyi worker puanlamaz; **kullanıcı kör karşılaştırmayla hakemlik eder.**

---

## 0. Ortam ve değişmezler

| | |
|---|---|
| Beyin | `.25:8082` llama-server, `candan-brain.service` |
| 26B baseline | `gemma-4-26B-A4B-it-UD-IQ4_XS`, `--ctx-size 65536 --parallel 1`, `--spec-type draft-mtp`, `--flash-attn on`, `--cache-reuse 256` |
| 12B (karşılaştırma) | `gemma-4-12B-it-qat-q4_0` — **model takası kullanıcı onayıyla, AYRI turda** |
| Erişim | `ssh root@192.168.0.25` |

**Beyin tek şerit (`--parallel 1`).** Ölçüm kullanıcının turunu bloklar → scriptlerde
istekler arası `SLEEP` var, seri koşarlar. Uzun koşuları arka arkaya dizme.

Ölçüm sırasında GPU boştaydı (`utilization.gpu = 0%`, throttle yok). Aynı GPU'da Whisper
(2.4 GB) + OmniVoice TTS (4.7 GB) + llama-server (15.6 GB) duruyor; kullanıcı konuşurken
ölçüm yapılırsa sayılar düşer → **12B turunda da GPU boşken ölç.**

---

## 1. Sistem promptu — TAHMİN EDİLMEDİ, YAKALANDI

Kalite koşusu persona olmadan anlamsız. Sistem promptu elle yeniden kurulmadı; **pi'nin
modele gönderdiği gerçek HTTP gövdesi** araya giren bir proxy ile yakalanıp fixture'a donduruldu:

**`bench/canli-sistem-prompt.json`** ← *12B turunda AYNI dosya kullanılır (yeniden yakalama yok).*

- 11.300 karakter, 10 tool.
- İçeriği (pi'nin birleştirme sırası, `worker/pi_brain.py:869-909` `_build_pi_args`):
  1. pi taban prompt'u (*"You are an expert coding assistant operating inside pi…"* + tool açıklamaları)
  2. `pi/AGENTS.md`
  3. `pi/personas/candan.md`
  4. `memory/family.md`
  5. `memory/users/ayhan/soul.md`
  6. kimlik satırı (*"Aktif kullanıcı: ayhan…"*)
  7. `Current date` / `cwd` + `<mode-switch>` bloğu
- `memory/soul.md` ve `memory/users/ayhan/profile.md` **yok** → zincire girmiyor (graceful).
- Tool sırası (gövdeden birebir; `pi_brain.py:148` allowlist + `enter_dev_mode`):
  `reminder_add, memory_add, soul_add, memory_search, reminder_list, reminder_cancel,
  web_search, fetch_content, memory_consolidate, enter_dev_mode`
- **Sampling: pi `temperature`/`top_p` GÖNDERMİYOR** → llama-server varsayılanı. `max_tokens=4096`, `stream=true`.
  Ölçüm scriptleri de göndermiyor. → Cevaplar **stokastik**; blok başına tek örnek alındı.

### Yakalama nasıl tekrarlanır (gerekirse)
```bash
# 1) proxy'yi başlat (127.0.0.1:9099 -> .25:8082, ilk gövdeyi diske döker)
python3 bench/proxy.py &
# 2) ~/.pi/agent/models.json'a GEÇİCİ 'llama-proxy' sağlayıcısı ekle (baseUrl=127.0.0.1:9099/v1)
# 3) pi'yi canlı argümanlarla bir kez çalıştır (args _build_pi_args'tan İMPORT edilir, elle yazılmaz)
python3 bench/yakala.py
# 4) models.json'u geri al (yakala.py sonrası ZORUNLU) ve proxy'yi öldür
```
> `yakala.py` canlı `sessions/ayhan` oturumunu kirletmemek için session dizinini geçiciye çevirir.
> **models.json'a eklenen `llama-proxy` girdisi bu turda eklendi ve geri alındı** (dosya orijinaliyle
> bayt-bayt aynı olduğu `diff` ile doğrulandı).

---

## 2. Kalite koşusu (ÖNCELİK 1)

**Script:** `bench/kalite.py` · **.25 yolu:** `/root/kalite.py` (fixture: `/root/canli-sistem-prompt.json`)

```bash
scp bench/kalite.py bench/canli-sistem-prompt.json root@192.168.0.25:/root/
ssh root@192.168.0.25 'cd /root && python3 kalite.py --cikti /root/cevaplar-12b.md'
scp root@192.168.0.25:/root/cevaplar-12b.md bench/12b/cevaplar.md
```

Tool çağrılırsa **sahte ama makul bir sonuç** beslenir ve model son cevabını üretir
(ölçülen şey kullanıcının duyacağı cevap, sadece tool değil). Sahte sonuçlar `kalite.py`
içinde `SAHTE_SONUC` sözlüğünde — **12B turunda değiştirilmemeli.**

### Sabit diyalog seti (10 blok)

| # | Blok | Ne ölçülüyor | Kullanıcı turu |
|---|---|---|---|
| 1 | duygusal destek — yorgunluk | soul.md "aktif dinleyici ol, çözüm sıralama" | "Bugün çok yoruldum ya. İşte her şey üstüme geldi, eve zor attım kendimi." |
| 2 | duygusal destek — üzüntü | ton, empati, `[mood:sad]`/`[sigh]` yerinde mi | "Bugün kötü bir haber aldım. Amcam hastaneye kaldırılmış, durumu pek iyi değilmiş." |
| 3 | günlük sohbet | doğal açılış; takip sorusu yasağı | "Günaydın Candan, nasılsın bugün?" |
| 4 | hatırlatıcı | reminder_add + cevabın doğallığı | "On beş dakika sonra çamaşırı almayı hatırlat." |
| 5 | hafızadan sorma | family.md'den cevap; not TARİHİNİ söylüyor mu (soul.md yasaklıyor) | "Annem temizlik konusunda nasıldı, hatırlıyor musun?" |
| 6 | bilmediği şey | uyduruyor mu, web_search çağırıyor mu | "Dün akşamki derbi kaç kaç bitti?" |
| 7 | duygu/ifade anı | `[mood:excited]`/`[laughter]` yerinde mi, abartı var mı | "Bil bakalım ne oldu! Terfi ettim, müdür bugün söyledi!" |
| 8 | çok kısa cevap | soul.md "kısa ve öz" | "Suyun kaynama noktası kaç derece?" |
| 9 | espri | soul.md "kısa, nazik, eğlenceli karşılık" | "Bugün buzdolabına telefonu, cebime de yumurtayı koydum. Sanırım tatile ihtiyacım var." |
| 10 | çok turlu | bağlam takibi + kısa kalma | 1) "Akşama misafir geliyor da ne yapsam bilemedim." 2) "Fırın işi iyi olabilir ama vaktim az, bir saatim var." |

*Promptların birebir hâli `bench/kalite.py` → `BLOKLAR`. Tablo ile kod arasında fark olursa **kod esastır**.*

### Kör karşılaştırma formatı
- Cevaplar: `bench/26b-baseline/cevaplar.md` (26B) ve `bench/12b/cevaplar.md` (12B).
- Her dosyada **model adı yalnızca en üstteki başlıkta**; bloklar sadece *prompt + cevap*.
- Hakemlik: aynı blok no, iki cevap yan yana, model etiketi gizli → kullanıcı seçer.
- Worker kendi izlenimini yalnızca **"worker izlenimi (bağlayıcı değil)"** başlığı altında yazar.

---

## 3. Nicel koşular (ÖNCELİK 2-3)

**Script:** `bench/ab_bench.py` · **.25 yolu:** `/root/ab_bench.py`

```bash
ssh root@192.168.0.25 'cd /root && python3 ab_bench.py kontrol 10'       # ÖNCE bu
ssh root@192.168.0.25 'cd /root && python3 ab_bench.py tool_secim 20'
ssh root@192.168.0.25 'cd /root && python3 ab_bench.py soul_kalip 10'
ssh root@192.168.0.25 'cd /root && python3 ab_bench.py gecikme 5'
ssh root@192.168.0.25 'cd /root && python3 ab_bench.py decode_rejim 5'
ssh root@192.168.0.25 'cd /root && python3 ab_bench.py hata_yutma 10'
# python3 ab_bench.py all          -> hepsi
# python3 ab_bench.py <test> --ciplak  -> minimal prompt (kontrol kipi)
```

Varsayılan kip **CANLI**: fixture'daki gerçek pi promptu + 10 tool. `--ciplak` bayrağı
minimal prompt + 9 tool'a düşürür (harness karşılaştırması için; canlıyı temsil etmez).

| test | ne ölçer | N |
|---|---|---|
| `kontrol` | **harness doğrulaması** — tool'suz sohbette tool çağrısı olmamalı | 10 |
| `tool_secim` | reminder_add / memory_add / soul_add isabeti | 20 |
| `soul_kalip` | soul_add'in "bundan sonra" kalıbına bağımlılığı (kalıplı vs kalıpsız) | 10 |
| `gecikme` | TTFT, prompt eval, decode — **SOĞUK/SICAK** ve bağlam uzunluğuna göre | 5 |
| `decode_rejim` | decode ↔ MTP draft kabul oranı (cevap uzunluğuna göre) | 5 |
| `hata_yutma` | tool HATA sonucunu yutup "tamamdır" diyor mu | 10 |

### Yöntem tuzakları (bu turda yakalandı — 12B'de tekrarlama)
1. **Prefix cache, prompt eval'i gizler.** `--cache-reuse 256` var: aynı prefix tekrar
   gönderilince prompt eval atlanır. İlk ölçümde `pp ≈ 10 tok/s` / TTFT 0.12s çıktı — bu
   *yavaşlık değil, cache isabetiydi*. `gecikme` testi artık her tekrarda eşsiz bir
   `[oturum <uuid>]` ön-eki basarak **SOĞUK** (cache miss) ve **SICAK** (cache hit)
   satırlarını ayrı raporlar. İkisini de raporla.
2. **`eval time` regex'i `prompt eval time`'ı da yakalar.** Journal'dan decode çıkarırken
   `(?<!prompt )eval time` kullan. Yoksa prompt-eval satırları decode'a karışır
   (bu turda 2417 tok/s'lik sahte "decode" değerleri üretti).
3. **Decode hızı tek sayı değil.** MTP speculative decoding var → decode, metnin
   öngörülebilirliğine (draft kabul oranına) bağlı. Kısa/kalıplaşmış onay ≈ 150 tok/s,
   uzun yaratıcı metin ≈ 50-75 tok/s. **Aynı modelde 3x fark.** Tek bir "decode tok/s"
   sayısı vermek yanıltıcı → rejimle birlikte raporla.

### Skor tablosu şablonu (12B turunda doldur)

| metrik | N | 26B | 12B | kazanan |
|---|---|---|---|---|
| kalite — kör karşılaştırma (blok sayısı) | 10 | — | — | *kullanıcı* |
| reminder_add isabeti | 20 | 20/20 | | |
| memory_add isabeti | 20 | 20/20 | | |
| soul_add isabeti (kalıplı) | 20 | 20/20 | | |
| soul_add (kalıpsız — "Bana kısa cevap ver.") | 10 | 0/10 | | |
| soul_add (kalıpsız — "Benimle resmi konuş.") | 10 | 10/10 | | |
| kontrol: sohbette yanlış tool | 20 | 0/20 | | |
| TTFT soğuk @~3.7k prompt | 5 | 2.17 s | | |
| TTFT soğuk @~17k prompt | 5 | 12.65 s | | |
| TTFT sıcak (cache hit) | 5 | 0.11 s | | |
| prompt eval (soğuk) @~17k | 5 | 1341 tok/s | | |
| decode — kısa onay (canlı rejim) | 5 | 151 tok/s | | |
| decode — uzun hikaye | 5 | 74 tok/s | | |
| hata yutma (uydurma) | 10 | 10/10 | | |

---

## 4. Dosya ve script yolları

| dosya | ne |
|---|---|
| `bench/ab-protokol.md` | bu dosya |
| `bench/canli-sistem-prompt.json` | **fixture** — yakalanan gerçek pi gövdesi |
| `bench/ab_bench.py` | nicel testler (`/root/ab_bench.py`) |
| `bench/kalite.py` | kalite diyalog seti (`/root/kalite.py`) |
| `bench/proxy.py` | sistem promptu yakalama proxy'si (yalnız yerelde çalışır) |
| `bench/yakala.py` | pi'yi canlı argümanlarla koşturup gövdeyi yakalar (yalnız yerelde) |
| `bench/26b-baseline/sonuclar.md` | 26B sayıları |
| `bench/26b-baseline/cevaplar.md` | 26B cevapları (birebir, kör karşılaştırma için) |
| `bench/26b-baseline/*.log` | canlı oturum snapshot'ları (agent / transcript / brain journal) |

**.25:/root/** altında bırakılanlar: `ab_bench.py`, `kalite.py`, `canli-sistem-prompt.json`,
`cevaplar-26b.md`. Önceki turdan kalanlar (`bench.py`, `canli_sira.py`, `ctx_vs_hiz.py`,
`sessiz_hata.py` …) **silinmedi**, duruyor.

> `bench.py` (önceki tur) hâlâ geçerli bir bağımsız kontrol:
> `python3 bench.py <model-alias> 5 300` — küçük prompt + 300 token hikâye + MTP kabul oranı.
