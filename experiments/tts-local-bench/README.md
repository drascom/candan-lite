# tts-local-bench — Türkçe TTS karşılaştırması (lokal, Mac M4 Pro)

Aynı Türkçe cümleler, farklı TTS modelleri → yan yana dinle, kulakla karar ver.

**Neden:** serverdeki (`.25:8808`) OmniVoice Türkçe'de hafif aksanlı; bazı kelimeler bozuk,
akıcılık zaman zaman düşük. Bu bench alternatifleri **servere dokunmadan** lokalde dener.

**Ana karşılaştırma ekseni: modellerin KENDİ doğal sesleri.** Ses klonlama zorunlu değil —
tek sabit ses de kabul. Klonlayabilen modellerde ayrıca ayhan referansıyla bir set daha var,
ama o bonus eksen.

> Bu klasör `experiments/` altında ve **self-contained**: üretim koduna hiçbir bağı yok,
> hiçbir şey servere yazmaz. `worker/data/`'dan sadece OKUR.
>
> **Hiçbir API tabanlı model yok** (Qwen3.5-Omni vb. dahil) — her şey bu makinede, offline.

---

## Hızlı başlangıç

```bash
cd experiments/tts-local-bench
./run_all.sh                 # kurulum + sentez + timings (tek model: ./run_all.sh freya)

python3 -m http.server 8009  # compare.html fetch kullanıyor → file:// ÇALIŞMAZ
# → http://localhost:8009/compare.html
```

`compare.html`: satır = cümle, sütun = set. Sütunlar **DEFAULT** (modelin kendi sesi) /
**KLON** (ayhan referansı) / **OMNIPICK** (beğenilen sesten çıkarılan yeni referans) /
**AUTOSEED** (sabit-seed deneyi) / **NORM** (trnorm vs OmniVoice dahili normalizasyon) /
**DUYGU** gruplarına ayrık, gruplar tek tıkla gizlenebilir.
Üstte iki kaynak düğmesi: *Referans: ayhan* ve *Kaynak ses: omnipick* — klonun orijinaline
benzeyip benzemediğini doğrudan karşılaştırabilirsin.
Kör dinleme için "İsimleri gizle" + "Karıştır" (karıştırma grup içinde kalır).

---

## Sonuçlar — 24 set, 294 wav

| Set | Model | Ses | Klonlama | Duygu kontrolü |
|---|---|---|---|---|
| `piper-default` | 99eren99/piper-turkish-tts | tek sabit ses | ❌ | ❌ |
| `freya-default` | freyavoice/Freya-TTS | tek sabit ses | ❌ | ❌ |
| `omnivoice-default` | k2-fsa/OmniVoice | kendi (auto mod) | ✅ var | ⚠️ yok (pitch/speed taklidi) |
| `omnivoice-clone` | ↑ ayhan referansı, her cümlede yeniden tokenize | ayhan klonu | — | — |
| `omnivoice-clone-cached` | ↑ referans BİR KEZ tokenize (`VoiceClonePrompt`) | ayhan klonu | — | — |
| `chatterbox-default` | ResembleAI Multilingual | kendi | ✅ var | ✅ `exaggeration` |
| `chatterbox-default-exag` | ↑ exaggeration 1.0 | kendi | — | ✅ |
| `chatterbox-clone` | ↑ ayhan referansı | ayhan klonu | — | ✅ |
| `chatterbox-clone-exag` | ↑ klon + exaggeration 1.0 | ayhan klonu | — | ✅ |
| `orpheus-default` | Karayakar/Orpheus-TTS-Turkish-PT-5000 | tek sabit ses | ❌ | ✅ `<laugh>` `<sigh>` |
| `orpheus-emotion` | ↑ etiketli 3 cümle | tek sabit ses | — | ✅ |

### omnipick — beğenilen sesten çıkarılan referans + ince ayar deneyleri

Kullanıcı 11 seti dinledi: **OmniVoice** en doğru + duygulu hissedilen model, devam kararı bu
modelde. (Piper telaffuzda en doğru ama robotik → aday değil.) Kullanıcı
`out/omnivoice-default/09-uzun-akici.wav` içindeki erkek sesi beğendi; auto modda üretildiği
için seed'i yok, tekrar üretilemez — ama wav elde, o yüzden **klon referansı** yapıldı.

- **Referans:** `refs/omnipick.wav` (11.57 s, 24 kHz, kırpılmadı)
- **ref_text:** ASR KULLANILMADI — cümle zaten biliniyor (`sentences.json` → `09-uzun-akici`),
  birebir `refs/omnipick.txt`'ye yazıldı
- **Prompt:** `create_voice_clone_prompt()` ile bir kez üretilip `refs/omnipick.omniprompt.pt`
  (20 KB) olarak kaydedildi, tüm setlerde `.load()` ediliyor — production'da yapılacak şeklin aynısı

| Set | Cümle | ok | Wall | RTF | Ne test ediyor |
|---|---|---|---|---|---|
| `omnipick-clone` | 15 | 15/15 | 161.1 s | 3.519 | yeni referans, varsayılan (num_step=32, gs=2.0) |
| `omnipick-norm` | 15 | 15/15 | 161.0 s | 3.582 | `normalize_text=True` — rakam/tarih düzeliyor mu |
| `omnipick-step64` | 5 | 5/5 | 111.3 s | 5.740 | `num_step=64` — kalite/gecikme takası |
| `omnipick-instruct-a-noinstruct` | 4 | 4/4 | 45.1 s | 2.984 | klon prompt, instruct yok |
| `omnipick-instruct-b-highpitch` | 4 | 4/4 | 45.4 s | 2.876 | klon prompt + instruct="high pitch" |
| `omnipick-instruct-c-designonly` | 4 | 4/4 | 16.9 s | 0.733 | klon YOK, sadece instruct (voice-design kontrolü) |

**Eski referans (15 s) vs yeni referans (11.57 s)** — aynı 15 cümle:

| | Wall | Üretilen ses | RTF |
|---|---|---|---|
| `omnivoice-clone` (eski, ayhan 15 s) | 193.8 s | 68.1 s | 3.437 |
| `omnipick-clone` (yeni, 11.57 s) | **161.1 s** | 60.7 s | 3.519 |

Toplam süre %17 düştü (referans context'i kısaldı), ama RTF neredeyse aynı — çünkü üretilen
ses de kısaldı. Yani hız kazancı referans uzunluğundan geliyor, modelden değil.

---

## Bulgular (omnipick deneyleri)

### 1. `instruct` klon prompt varken YOK SAYILIYOR — doğrulandı

Serverdeki `worker/omnivoice_tts.py` → `MOOD_PRESETS`, duygu için `instruct="high pitch"` /
`"low pitch"` gönderiyor. **instruct kısmı hiçbir şey yapmıyor.**

Test deterministik yapıldı: OmniVoice üretimi stokastik olduğu için `manual_seed` ile
sabitlendi ve tekrarlanabilirlik önce kanıtlandı (aynı seed → **4/4 bit-bit aynı**).
Üç varyant aynı seed'i gördü, `speed=1.0` sabit tutuldu (hız etkisi ayıklandı).

| Karşılaştırma | Sonuç |
|---|---|
| a (instruct yok) run1 vs run2, aynı seed | **4/4 bit-bit aynı** → determinizm çalışıyor |
| a vs b (instruct="high pitch") | 4/4 farklı dosya |

Ama "farklı dosya" tek başına instruct'ın işe yaradığını göstermez: instruct token eklemek
RNG tüketim yolunu değiştirir, çıktı semantik olarak yok sayılsa bile dalga formu değişir.
Bu yüzden **perde (F0) ölçüldü** (librosa `pyin`, medyan F0 — dinleme değil, ölçüm):

| | Ortalama fark | \|Ortalama\| | Std |
|---|---|---|---|
| Gürültü tabanı (aynı ayar, farklı RNG), n=3 | −1.7 Hz | 10.4 Hz | 14.3 Hz |
| **instruct etkisi** b−a, n=4 | **−5.3 Hz** | **7.7 Hz** | 9.8 Hz |

instruct'ın etkisi **gürültü tabanının içinde** (7.7 < 10.4) ve işareti **ters** —
"high pitch" istenmişken perde ortalama 5.3 Hz *düşmüş*. Yani sistematik bir etki yok.

Kontrol (`c-designonly`, klon prompt olmadan sadece instruct): tamamen farklı bir ses
(F0 sapması cümleye göre −65 … +34 Hz) ve **çok daha hızlı** (RTF 0.733 vs 2.98) — referans
context'i olmadığı için. Yani voice-design modu ayrı bir yol; instruct orada bir şey yapıyor,
klonla birlikte değil.

> **Sonuç:** `MOOD_PRESETS`'in `instruct` alanı ölü kod. Duygu etkisi ne kadar hissediliyorsa
> o, preset'in `speed` bileşeninden geliyor (1.18 / 0.85) — o parametre ayrı ve çalışıyor.
> Dokümana göre `instruct` voice-design moduna ait ve yalnız Çince/İngilizce eğitilmiş,
> yani bu sonuç BEKLENEN.

### 2. `normalize_text=True` rakam sorununu ÇÖZMÜYOR — bazı yerde bozuyor

`num2words==0.5.14` kuruldu (Türkçe destekliyor, `setup_omnivoice.sh`'a eklendi) ve
`omnipick-norm` 15/15 üretildi. Ama normalizasyonun metin çıktısı doğrudan incelendiğinde
(dinleme değil, `omnivoice.utils.text.normalize_text` çağrısı):

| Girdi | normalize_text çıktısı | Durum |
|---|---|---|
| `3.500 TL` | `üç.beşyüz TL` | ❌ **Türkçe binlik ayracı `.` anlaşılmıyor** — "üç bin beş yüz" olmalı |
| `2.625 TL` | `iki.altıyüzyirmibeş TL` | ❌ aynı hata |
| `%25` | `%yirmibeş` | ❌ `%` işareti çevrilmemiş — "yüzde yirmi beş" olmalı |
| `14:30'da` | `ondört:otuz'da` | ❌ saat iki nokta üst üste ile bırakılmış |
| `1994` | `bindokuzyüzdoksandört` | ⚠️ rakam doğru ama kelimeler bitişik |

En kritik olanı binlik ayracı: `3.500` iki ayrı sayı gibi işlenip `üç` + `.` + `beşyüz`
oluyor. Yani `normalize_text` kullanıcının en çok şikâyet ettiği para/saat/yüzde
biçimlerinde **düzeltmiyor, yanlış okutuyor**. Kulakla teyit kullanıcıya kalıyor ama
metin katmanındaki hata ölçülebilir ve nettir.

### 3. `num_step=64` süreyi artırıyor — kalite kararı kullanıcıda

Aynı 5 cümle üzerinden birebir karşılaştırma (`step64`'ün cümleleri `clone` setinden süzüldü):

| | Wall | Üretilen ses | RTF |
|---|---|---|---|
| `num_step=32` (omnipick-clone'dan aynı 5 cümle) | 55.7 s | 22.9 s | 2.44 |
| `num_step=64` (omnipick-step64) | 111.3 s | 23.5 s | 4.74 |

Süre tam **2.00 katına** çıkıyor, üretilen ses uzunluğu pratikte değişmiyor (+0.6 s).
Yani num_step doğrudan gecikme çarpanı. Kalite farkı olup olmadığı kulakla belirlenecek —
bench bunu ölçmez.

### 4. Ara sıra BOŞ çıktı (aralıklı hata)

OmniVoice bazı **kısa** cümlelerde sıfır uzunlukta ses üretiyor. Gözlenen 2 vaka:
`12-soru-kisa` ("Bunu sen mi yaptın?", `omnipick-norm` koşusunda) ve `01-simdi`
(`omnipick-clone` koşusunda). Tekrarlanabilir değil — aynı set yeniden koşulduğunda
15/15 geçti, yani stokastik.

Bench harness'ı artık bunu **başarısızlık sayıyor** (`common.py`: `dur <= 0` → hata);
önceden sıfır uzunluklu wav sessizce "ok" görünüp RTF hesabını `ZeroDivisionError` ile
patlatıyordu. Raporlanan tüm setler bu düzeltmeden sonra 15/15 temiz.

> Bu, production için not: OmniVoice'a kısa cümle gönderirken boş çıktı ihtimaline karşı
> uzunluk kontrolü + retry mantığı gerekebilir.

---

### Klon maliyetinden kaçış — iki deney

Ölçülen durum: `omnivoice-default` (auto, referans YOK) RTF 0.89 · `omnivoice-clone` RTF 3.44 →
**3.4× fark**. Kullanıcı iki klon setini (eski vs yeni referans) dinledi, **kalite farkı yok**;
yani klonlamanın tek işlevi sesi sabit tutmak. Soru: sabit ses, klon maliyeti ödenmeden alınır mı?

Ölçüm **kulakla değil konuşmacı gömmesiyle**: `worker/models/campplus.onnx` (projenin kendi
speaker-ID modeli), sherpa-onnx ile `worker/speaker_id.py` ile aynı yoldan. **Bench venv'lerine
hiçbir şey kurulmadı** — `worker/.venv`'de sherpa-onnx 1.13.4 zaten vardı, salt okuma kullanıldı
(`runners/speaker_sim.py` → `out/speaker_sim.json`).

#### Deney A — sabit seed konuşmacıyı sabitlemiyor ❌

Bilinen: aynı metin + aynı seed → bit-bit aynı çıktı. Test edilen: **farklı metinlerde** aynı
seed aynı konuşmacıyı veriyor mu?

| Set | Set-içi ikili kosinüs (ort) | min | std |
|---|---|---|---|
| `omnivoice-clone` — **üst taban** (klon, ses sabit olmalı) | **0.787** | 0.624 | 0.069 |
| `autoseed-fixed` (hepsinde `manual_seed(1234)`) | 0.481 | 0.127 | 0.162 |
| `autoseed-random` — **alt taban** (seed serbest) | 0.476 | 0.131 | 0.136 |

`autoseed-fixed` (0.481) ile `autoseed-random` (0.476) arasındaki fark **0.005** — yok denecek
kadar az, ve ikisi de üst tabandan (0.787) çok uzak.

> **Karar: sabit seed farklı metinlerde konuşmacıyı SABİTLEMİYOR.** Seed yalnız *aynı* metni
> tekrarlanabilir kılıyor. Sabit ses için klonlama gerekli; 3.4× kazanç bu yoldan alınamıyor.

#### Deney B — kısa referans hem daha hızlı hem daha tutarlı ✅

`refs/omnipick.wav`'dan cümle/kelime sınırında kesildi, **ref_text her kesite göre düzeltildi**:
`omnipick_3s.wav` (3.90 s, "…çok şaşırdım." — cümle sonu) ve `omnipick_6s.wav` (6.30 s,
"…çoktan yola çıkmış" — öbek sonu).

| Referans | Wall (15 cümle) | RTF | Set-içi tutarlılık | Kaynağa benzerlik |
|---|---|---|---|---|
| yok (auto) | 54.6 s | **0.86** | 0.481 | 0.340 |
| **3.90 s** | **84.5 s** | **1.66** | **0.815** | **0.841** |
| 6.30 s | 108.8 s | 2.25 | 0.764 | 0.829 |
| 11.57 s (mevcut) | 161.1 s | 3.52 | 0.744 | 0.824 |

Maliyet referans uzunluğuyla neredeyse doğrusal ölçekleniyor. Beklenmedik olan: **kısa referans
kaliteyi düşürmüyor** — 3.9 s hem set-içi tutarlılıkta (0.815 > 0.744) hem kaynağa sadakatte
(0.841 > 0.824) 11.57 s'yi geçiyor, üstelik **1.9× daha hızlı**.

> **En kısa kabul edilebilir referans: ~4 saniye.** Ölçüme göre 11.57 s'yi kullanmak için bir
> sebep yok. Yorum ölçümle sınırlı: kulakla teyit kullanıcıda.

Metrik doğrulaması: `omnivoice-clone` (ayhan referansı) kaynağa benzerlik **0.159** —
farklı konuşmacı olduğu için düşük çıkıyor, yani ölçüt konuşmacıları gerçekten ayırt ediyor.

---

### trnorm — kendi Türkçe normalizer'ımız + ASR ile objektif doğrulama

`omnipick-norm` dinlendiğinde `normalize_text=True` tarih/bazı rakamları düzeltmişti ama
`%25` yüzde okunmuyor, `2.6xx TL` rakam rakam okunuyor, `14:30'da` cümlesinde **"başlıyor"
kelimesi düşüyordu**. Kök sebep: OmniVoice Çince/İngilizce dışındaki dillerde `num2words` ile
**yalnız çıplak tam sayıları** çeviriyor → `%25`, binlik ayıraçlı `3.500`, kesme ekli
`14:30'da` ham kalıyor. Çözüm: normalizasyonu modele bırakmayıp kendimiz yapmak.

**`trnorm.py`** — bağımsız, stdlib-only, `worker/`'a olduğu gibi kopyalanabilir.
`normalize_tr(text) -> str`. Selftest: `python3 trnorm.py --selftest` → **26/26 geçti**.

**Hangi kütüphane?** Hiçbiri — kendimiz yazdık, çünkü:
- PyPI'da `trnorm` / `turkish-normalizer` / `turkish-text-normalizer` / `tr-normalizer` /
  `turknorm` paketlerinin **hiçbiri yok** (hepsi 404). VoxCPM2 kartındaki "trnorm" yayınlanmış
  bir paket değil.
- `num2words` Türkçe biliyor ama çıktısı **bitişik**: `num2words(3500, lang="tr")` →
  `"üçbinbeşyüz"`. Geri bölmek morfem ayrıştırıcı ister. Türkçe sayı sistemi tamamen düzenli
  olduğu için boşluklu üretimi doğrudan yazmak hem kısa hem kesin.

Kapsam: binlik ayıraç, ondalık, yüzde (**"yüzde" sayıdan ÖNCE**), para (TL/₺/$/€/£), saat,
yıl, tarih, birim (kg/cm/km…), kısaltma (Dr./No./vs.), ve **kesme işaretli ekler** —
ünlü uyumu + sert ünsüz benzeşmesiyle: `1994'te` → "bin dokuz yüz doksan dör**tte**",
`14:30'da` → "on dört otuz**da**", `%25'lik` → "yüzde yirmi beş**lik**", `4.750 TL'den` →
"dört bin yedi yüz elli lira**dan**". İngilizce ödünç kelimeler ve `[laughter]` / `[mood:...]`
etiketleri **aynen** geçer.

#### ASR geri-dönüş testi — `out/asr_eval.json`

Kulakla değil ölçerek. Her wav Whisper (`mlx-community/whisper-large-v3-turbo`) ile
transkribe edilip `sentences_norm.json`'daki `expected_spoken` ile karşılaştırılıyor
(WER + atlanan kelime). Her iki set **aynı** `expected_spoken`'a karşı ölçülüyor → adil.

**Taban gürültüsü: WER 0.000** — `refs/omnipick.wav` (metni kesin biliniyor) kusursuz
transkribe edildi. Yani ölçülen hatalar ASR'ın değil, sentezin.

| Set | Normalizasyon | Ortalama WER | Atlanan kelime |
|---|---|---|---|
| **`omnipick-trnorm`** | **trnorm.py (bizim)** | **0.063** | **9** |
| `omnipick-norm2` | OmniVoice dahili | 0.350 | 61 |
| *taban (gerçek insan sesi)* | — | *0.000* | *0* |

**trnorm WER'i 5.6 kat düşürüyor, atlanan kelimeyi 6.8 kat azaltıyor.**

Kullanıcının şikâyet ettiği cümlede (`n02-saat-ek`) doğrudan doğrulama:

| Set | WER | Transkript |
|---|---|---|
| `omnipick-trnorm` | **0.000** | "Toplantı saat 14.30'da **başlıyor**. Lütfen geç kalma." |
| `omnipick-norm2` | 0.222 | "başlıyor" **DÜŞTÜ** |

> **Ölçüm tuzağı (kayda değer):** Whisper konuşulan sayıları **rakama geri çeviriyor**
> ("üç bin beş yüz lira" → "3500 lira", "yüzde yirmi beş" → "%25"). Ham karşılaştırma bu
> yüzden DOĞRU konuşmayı hatalı sayıyordu (trnorm WER 0.485 görünüyordu). Düzeltme:
> hipotez de `normalize_tr`'den geçiriliyor → gerçek WER 0.063. `asr_eval.py` bunu yapıyor.

#### trnorm'da kalan 9 "atlanan kelime" — çoğu ölçüm artefaktı

Tek tek bakıldığında yalnız **biri gerçek kelime düşmesi**:

| Cümle | "Atlanan" | Gerçekte |
|---|---|---|
| `n07` | `kaçırma` | ❌ **GERÇEK DÜŞME** — cümle erken bitiyor ("Tren sabah 9.05 trenidir.") |
| `n07` | `sıfır` | ölçüm artefaktı: ASR "9.05" yazıyor, normalizer `05`→"beş" (baştaki sıfır kayıp) |
| `n04` | `kâr` | ASR "kar" yazmış — aynı kelime, şapkasız |
| `n13` | `link`,`ten` | ASR "Linkten" tek kelime yazmış — bölünme farkı |
| `n10` | `euro`,`idi` | ASR "euroydu" — konuşma dili kısalması |
| `n01` | `bugün` | ASR "One-Eye" duymuş |
| `n06` | `sözleşme` | ASR "Rösleşme" duymuş |

Yani trnorm ile pratikte **tek gerçek kelime düşmesi kaldı** (`n07`'de cümle sonu "kaçırma").

#### speed=0.9 denemesi — hipotez DOĞRULANMADI

"Kelime düşmesi süre tahmini yetersizliğinden" hipotezi test edildi: aynı 6 cümle
(`trnorm`'da eksik çıkanlar) `speed=0.9` ile yeniden üretildi (`omnipick-trnorm-slow`).

| | Ortalama WER | Atlanan kelime |
|---|---|---|
| `speed=1.0` (aynı 6 cümle) | 0.147 | 9 |
| `speed=0.9` | **0.112** | **7** |

Küçük bir iyileşme var (`n10` tamamen düzeldi, `n01` iyileşti) **ama asıl sorunu çözmedi**:
`n07`'deki "kaçırma" düşmesi iki hızda da **birebir aynı** ("Tren sabah 9.05 trenidir.").
Yani cümle-sonu kelime düşmesi süre tahmininden değil, başka bir şeyden kaynaklanıyor.

#### Bilinen eksikler (uydurmuyorum, listeliyorum)

- **Cümle sonu kelime düşmesi** (`n07` "kaçırma") — trnorm çözmüyor, `speed=0.9` de çözmüyor.
  Nedeni bulunamadı.
- `attach_suffix` yalnız yaygın ekleri biliyor (-DA, -DAn, -lI, -lIk, yönelme/belirtme/tamlayan).
  **Tanımadığı eki olduğu gibi ekler** (bozmaz ama uyumlamaz).
- Saat ayracı yalnız `:` — ASR'ın ürettiği `9.05` biçimi saat sayılmıyor (yalnız ölçüm
  tarafını etkiler, sentezi değil).
- `TL` gibi net kısaltmalar çevriliyor; `m` (metre) gibi İngilizce kelimeyle çakışabilecek
  belirsiz birimler **bilerek** kapsam dışı.
- Sayı okuma `10^12`'ye kadar (`milyar`); trilyon üzeri yok.

---

### Elenen / kurulmayan

- **F5-TTS (marduk-ra/F5-TTS-Turkish) — ELENDİ, set üretilmedi.** Referans sızması
  (`ref_text` ile ses örtüşmezliği: F5 referans SESİ 12 s'ye kırpıyor ama `ref_text`'e
  dokunmuyor → karşılığı kalmayan kelimeler çıktıya sızıyor) + RTF 9.58 ile realtime dışı.
  `runners/run_f5tts.py` ve `venvs/f5tts` duruyor, istenirse tekrar denenebilir.
- **VoxCPM2** — daha önce denenip elenmişti, kurulmadı. (FreyaTTS onun AudioVAE2'sini latent
  uzayı olarak kullanıyor, ama VoxCPM2'nin kendisi TTS olarak koşmuyor.)
- **`dcx514ai/omnivoice_tr_finetune`** — repo **gated**, erişim bekleniyor. Runner iskeleti
  hazır (`runners/run_omnivoice_tr_finetune.py`), erişim gelince `revision="v1500"` ile
  tek komutla koşar.

### Duygu / expressivity ekseni

| Model | Mekanizma | Bench'te |
|---|---|---|
| **Orpheus** | metne gömülü etiket: `<laugh> <chuckle> <sigh> <cough> <sniffle> <groan> <yawn> <gasp>` | `orpheus-emotion` — 3 etiketli cümle (`sentences_emotion.json`) |
| **Chatterbox** | `exaggeration` (0.5 → 1.0) + `cfg_weight` (0.5 → 0.3) | `-exag` setleri, hem default hem klon seste |
| **OmniVoice** | **Gerçek duygu desteği YOK.** Serverdeki `[mood:excited/sad]` sadece pitch+speed preset'i (`worker/omnivoice_tts.py`) | bench'te uygulanmadı (ham çıktı isteniyor) |
| **FreyaTTS** | yok — tek sabit ses, duygu parametresi yok | — |
| **Piper** | yok — `length_scale`/`noise_scale` var ama duygu değil, sadece hız/varyans | — |

---

## Ölçüm — `out/timings.json`

| Set | Toplam wall | Üretilen ses | Ortalama RTF |
|---|---|---|---|
| `piper-default` | 1.9 s | 74.4 s | **0.037** |
| `freya-default` | 42.9 s | 90.4 s | **0.754** |
| `omnivoice-default` | 56.9 s | 67.8 s | **0.887** |
| `chatterbox-default` | 183.5 s | 82.8 s | 2.226 |
| `omnivoice-clone-cached` | 188.5 s | 69.5 s | 3.235 |
| `omnivoice-clone` | 193.8 s | 68.1 s | 3.437 |
| `chatterbox-clone` | 225.4 s | 82.4 s | 2.723 |
| `chatterbox-clone-exag` | 236.4 s | 88.4 s | 2.501 |
| `chatterbox-default-exag` | 349.4 s | 111.4 s | 2.586 |
| `orpheus-default` | 557.0 s | 100.2 s | 5.516 |
| `orpheus-emotion` (3 cümle) | 84.2 s | 19.1 s | 4.047 |
| `omnipick-clone` | 161.1 s | 60.7 s | 3.519 |
| `omnipick-norm` | 161.0 s | 60.1 s | 3.582 |
| `omnipick-step64` (5 cümle) | 111.3 s | 23.5 s | 5.740 |
| `omnipick-instruct-a-noinstruct` (4) | 45.1 s | 19.4 s | 2.984 |
| `omnipick-instruct-b-highpitch` (4) | 45.4 s | 19.8 s | 2.876 |
| `omnipick-instruct-c-designonly` (4) | 16.9 s | 24.1 s | 0.733 |
| `omnipick-trnorm` (14) | 147.2 s | 48.3 s | 3.024 |
| `omnipick-norm2` (14) | 142.9 s | 40.0 s | 3.903 |
| `omnipick-trnorm-slow` (6) | 62.9 s | 21.7 s | 3.108 |
| `autoseed-fixed` | 54.6 s | 67.1 s | 0.859 |
| `autoseed-random` | 54.2 s | 67.3 s | 0.858 |
| `omnipick-clone-3s` | 84.5 s | 58.8 s | 1.655 |
| `omnipick-clone-6s` | 108.8 s | 58.8 s | 2.252 |

> ⚠️ **Bu sayılar MPS (Apple M4 Pro, 24 GB unified) ölçümleridir — serverdeki RTX 3090
> değerleri DEĞİLDİR.** Karşılaştırmalı okuyun (hangi model diğerine göre hızlı), mutlak
> değer olarak değil. Kalite kulakla belirlenir; RTF yalnız "serverde koşturulabilir mi"
> sorusuna kabaca fikir verir.
>
> `piper-default` ayrıca **CPU/ONNX** üzerinde koşuyor, MPS değil — o satır diğerleriyle
> aynı donanım ekseninde değil.

`wall_s` = sentez duvar-saati, `audio_s` = üretilen sesin uzunluğu, `rtf = wall_s / audio_s`
(1.0'ın altı gerçek zamandan hızlı). **İlk cümle model ısınmasını içerir** ve ortalamayı
yukarı çeker — örn. FreyaTTS'in ilk cümlesi 24.7 s, kalan 14'ü 0.5–3.4 s arası.

### OmniVoice: default vs clone vs clone-cached — klonlama neye mal oluyor?

| Set | Toplam wall | RTF | default'a göre |
|---|---|---|---|
| `omnivoice-default` | 56.9 s | 0.887 | — |
| `omnivoice-clone` | 193.8 s | 3.437 | **+136.9 s** |
| `omnivoice-clone-cached` | 188.5 s | 3.235 | +131.7 s |

Referansı `create_voice_clone_prompt()` ile bir kez tokenize edip `VoiceClonePrompt` olarak
`refs/ayhan_ref.omniprompt.pt`'ye (23 KB) yazmak ve 15 cümlenin hepsinde aynı nesneyi
kullanmak **5.2 s kazandırıyor** (193.8 → 188.5, %2.7). Prompt üretimi tek seferlik 1.57 s,
diskten yükleme 0.002 s — bunlar cümle ölçümlerinin dışında.

**Yorum:** klonlamanın default'a göre getirdiği 136.9 s'lik ek maliyetin yalnız **~5 s'i (%4)
tokenizasyon**; kalan **~132 s (%96) uzun-context üretim maliyeti**. Referansın ses token'ları
her cümlede modelin context'inde duruyor ve her adımda dikkat hesabına giriyor — cache'lenen
şey sadece o token'ların bir kereliğine ÜRETİLMESİ, her üretimde KULLANILMASI değil.
Yani cache'leme gerçek kazanç getirmiyor; klonlama pahalıysa sebebi tokenizasyon değil,
mimarinin kendisi.

**Cache'leme sesi değiştirmiyor** (bu bir bulgu DEĞİL, beklenen davranış). Doğrulama:
OmniVoice üretimi stokastik — aynı ayarla iki kez koşulan `clone` setinin kendi içindeki fark
ile `clone` ↔ `clone-cached` farkı **birebir aynı büyüklükte**:

| Karşılaştırma | Birebir aynı | Ort. \|korelasyon\| | Ort. süre farkı |
|---|---|---|---|
| `clone` run1 vs run2 (gürültü tabanı) | 0/15 | 0.019 | 0.24 s |
| `clone` run1 vs `clone-cached` | 0/15 | 0.019 | 0.18 s |

Fark tamamen örnekleme (sampling) gürültüsü; cache'lemeye atfedilebilecek bir sapma yok.

### Ölçülen bir aykırılık

`chatterbox-default-exag` setinde `09-uzun-akici` cümlesi **32.6 s** ses üretti — aynı cümle
diğer setlerde 9–12 s. Yüksek `exaggeration`'ın uzun cümlelerde çıktıyı uzattığını gösteriyor.
(Bu bir süre kaydı, kulak değerlendirmesi değil.)

---

## Girdi

### Test cümleleri — `sentences.json` (15) + `sentences_emotion.json` (3)

| Kategori | id'ler | Ne test ediyor |
|---|---|---|
| `problemli-kelime` | 01-03 | **şimdi, şu an, işine, konuşuyor** — serverde `/opt/omnivoice/pronounce_tr.json` ile yamanan kelimeler. Bench'te yama YOK; modelin ham hâli görülüyor. |
| `sayi-tarih-saat` | 04-06 | `14:30`, `1994`, `%25`, `3.500 TL`, `2.625 TL` |
| `ingilizce-odunc` | 07-08 | Wi-Fi, router, restart, link, download |
| `akicilik` | 09-10 | 25+ kelimelik virgüllü bileşik cümleler |
| `tonlama` | 11-13 | soru tonlaması (uzun/kısa), ünlem-tepki |
| `ek-yogun` | 14-15 | `görüşemeyeceğimizi`, `yapabileceklerimizden`, ünlü uyumu |
| `duygu` | e1-e3 | `<laugh>` / `<sigh>` etiketleri (yalnız Orpheus) |

### Referans ses — `refs/ayhan_ref.wav` (yalnız KLON setleri için)

Görevdeki klip `06-serbest.wav` **2.0 saniye** — klonlama için çok kısa. Aynı konuşmacının
6 klibi `ffmpeg` ile birleştirildi (**15.0 s**, 24 kHz mono; `_16k` varyantı da var):

```
06-serbest → 01-neseli → 02-uzgun → 03-merakli → 04-kizgin → 05-aceleci
```

06 başa alındı (görevde işaret edilen asıl referans o; klon modelleri baştaki sesi ağırlıklandırır).

**Transkript — `refs/ayhan_ref.txt`** (OmniVoice `ref_text` istiyor):

> Merhaba, ben evin babasıyım. Bugün güzel bir şey oldu. Çok mutluyum. Bugün kendimi biraz üzgün
> hissediyorum. Acaba şimdi ne olacak? Gerçekten merak ediyorum. Buna gerçekten kızdım. Böyle
> olmasını istemezdim. Hemen çıkmam gerekiyor. Biraz acelem var.

Kaynak: `mlx-community/whisper-large-v3-turbo` (lokal; `refs/ayhan_ref.asr.json` zaman
damgalarıyla). **Uydurma değil** ve doğrulanabilir: 01–05 klipleri sabit prompt'larla kaydediliyor
(`worker/pi_brain.py` → `SPEAKER_EXPRESSION_PROMPTS`) ve ASR çıktısı o cümlelerle birebir örtüşüyor.
Yalnız ilk cümle (`06-serbest`, serbest konuşma) prompt'suz — onu da ASR verdi.

`refs/ayhan_ref_f5.*` — elenen F5-TTS için üretilmiş 10 s'lik kısa varyant. Başka hiçbir set
kullanmıyor; ortak referans (`ayhan_ref.wav`) değiştirilmedi.

---

## Kurulum notları (tuzaklar)

- **Model başına ayrı venv** (`venvs/<model>/`, `uv venv --python 3.12`). Ortak venv denenmedi:
  torch sürümü, librosa/numba ve transformers bağımlılıkları çakışıyor.
- **Kurulumlar SIRAYLA koşar.** 5 paralel torch indirmesi bağlantıyı doyurup
  `address not available` ile düşürdü.
- **librosa/numba tuzağı (FreyaTTS, F5-TTS):** bağımlılık çözümü eski `librosa`'ya düşünce
  `numba==0.53.1` çekiliyor, o da Python 3.12'de derlenmiyor (`only versions >=3.6,<3.10`).
  Çözüm: `librosa>=0.11` + `numba>=0.60` açıkça önce pinleniyor.
- **setuptools tuzağı (Chatterbox):** chatterbox → `perth` (ses filigranı) → `pkg_resources`.
  uv, 3.12 venv'ine setuptools koymuyor; koyunca da 83.0.0 geliyor ve orada `pkg_resources`
  KALDIRILMIŞ. `perth`'in `__init__`'i ImportError'ı yutup watermarker'ı `None` yapıyor →
  model yüklenirken anlamsız bir `TypeError: 'NoneType' object is not callable`.
  Çözüm: `setuptools<81`.
- **FreyaTTS**'in `pyproject.toml`/`setup.py`'si yok → `pip install -e` çalışmaz; paket
  klonlanan repo kökünden import ediliyor (`runners/run_freya.py` içinde `sys.path`).
- **Orpheus** GGUF yerine **safetensors + transformers** ile koşuyor: llama.cpp'de audio-token
  akışını elle çözmek gerekirdi, transformers yolu model kartındaki referans akışın aynısı.
  Ayrıca repo eğitim artıklarını da taşıyor (`optimizer.pt` ~12 GB) → `prefetch_weights.py`
  `allow_patterns` ile yalnız inference dosyalarını çekiyor (17 GB yerine ~12 GB).
  3B fp32 24 GB unified'da sıkışık → `float16` yükleniyor.

---

## Yapı

```
experiments/tts-local-bench/
  README.md               # bu dosya
  run_all.sh              # kurulum + sentez + timings, tek komut
  sentences.json          # 15 sabit test cümlesi
  sentences_emotion.json  # 3 duygu-etiketli cümle (Orpheus)
  sentences_norm.json     # 14 normalizasyon cümlesi + expected_spoken (ASR referansı)
  trnorm.py               # Türkçe TTS-öncesi normalizer (bağımsız, stdlib, taşınabilir)
  compare.html            # bağımlılıksız karşılaştırma sayfası (CDN yok)
  refs/
    ayhan_ref.wav         # 15 s, 24 kHz — tüm KLON setlerinde aynı
    ayhan_ref_16k.wav     # 16 kHz varyant
    ayhan_ref.txt         # transkript (OmniVoice ref_text)
    ayhan_ref.asr.json    # ASR çıktısı, zaman damgalı
    ayhan_ref.omniprompt.pt  # OmniVoice VoiceClonePrompt cache'i (23 KB)
    omnipick.wav          # BEĞENİLEN ses (omnivoice-default/09-uzun-akici), 11.57 s
    omnipick.txt          # ref_text — ASR değil, sentences.json'dan birebir
    omnipick.omniprompt.pt   # omnipick VoiceClonePrompt cache'i (20 KB)
    omnipick_3s.wav/.txt  # 3.90 s kesit + DÜZELTİLMİŞ ref_text (kısa-referans deneyi)
    omnipick_6s.wav/.txt  # 6.30 s kesit + DÜZELTİLMİŞ ref_text
  runners/
    common.py             # ortak bench döngüsü: sentezle, süre ölç, wav yaz
    setup_<model>.sh      # model başına venv kurulumu (idempotent)
    run_<model>.py        # model başına sentez
    prefetch_weights.py   # ağırlıkları önden indir (inference yok)
    merge_timings.py      # out/timings.json + out/manifest.json üretir
    asr_eval.py           # Whisper geri-dönüş testi → out/asr_eval.json (WER + atlanan kelime)
    speaker_sim.py        # campplus gömme benzerliği → out/speaker_sim.json (worker/.venv ile)
    run_seedref.py        # sabit-seed ve kısa-referans deneyleri
  out/<set>/<id>.wav      # üretilen sesler  (git'e girmez)
  venvs/, vendor/         # venv'ler ve klonlanan repolar (git'e girmez)
```

`.gitignore`: `out/`, `venvs/`, `vendor/`, `refs/*.wav` hariç tutuldu — bench **araçları**
commit edilir, GB'larca ağırlık ve üretilen ses edilmez.

`./check.sh` bu klasörü etkilemez: `ruff.toml` zaten `experiments`'ı `extend-exclude` ediyor.

---

## Kapsam dışı

- **Serverdeki hiçbir şeye dokunulmadı** (`.25` / oracle-stage). Her şey lokal.
- **API tabanlı model kullanılmadı** — tamamı offline.
- **Kalite değerlendirmesi yapılmadı** — sesler üretildi, dinleme ve karar kullanıcıda.
  Buradaki doğrulama yalnız teknik: dosya oluştu mu, WAV geçerli mi (`ffprobe`), süre/sample
  rate makul mü.
- `pronounce_tr.json` telaffuz yaması **uygulanmadı** (amaç modelin ham Türkçesini görmek).
