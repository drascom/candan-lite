# Duygu katmanı — 43 kontrol token'ının TAMAMI ölçüldü (27 Tem)

Görev: `handoff/task-2026-07-27-duygu-katmani.md`. Taban commit `2a98abf` (streaming).
Ölçüm takımı: `experiments/higgs-tts3/token_probe.py` + `token_eval.py`.
Dinleme sayfaları: `./serve.sh tokens.html` (ölçüm) · `./serve.sh demo.html` (kulak testi).

## 0. Geri alma (tek blok)

```bash
ssh root@192.168.0.25 'cd /opt/candan-lite && \
  cp worker/higgs_tts.py.bak-duygu-20260727-1436 worker/higgs_tts.py && \
  cp pi/AGENTS.md.bak-duygu-20260727-1436 pi/AGENTS.md && \
  cp pi/personas/candan.md.bak-duygu-20260727-1436 pi/personas/candan.md && \
  systemctl restart candan-worker'
```
Yereldeki karşılığı: `git checkout -- worker/higgs_tts.py pi/AGENTS.md pi/personas/candan.md`.

## 1. Yöntem — neden bu ölçüm güvenilir

Eski `elation` ölçümü deney koşumundan alınmıştı. Bu koşum **canlı yolun ta
kendisinden** geçiyor: `POST /api/tts/stream`, referans klonu, aynı blok/lookahead
mantığı. Sunucuya, servise, streaming yapısına DOKUNULMADI — yalnız HTTP isteği atıldı.

* Token başına **12 örnek** (sınırdakiler 24), aynı Türkçe cümle.
* Whisper (`mlx-community/whisper-large-v3-turbo`) geri-dönüşü, WER + atlanan kelime.
* Karar tek sayıya değil **dört ölçüte** dayanıyor: boş çıktı · çok kısa · **uydurma
  konuşma** (medyanın 2 katından uzun) · cümlenin başını yeme. Dördüncüsü olmasa
  `speed_very_slow`'un tek felaket örneği (`"Main testi Enya, İvet ve İyali
  devirsiniz. Yasalış reni bugün harika bir haber var."`) WER ortalamasında kaybolurdu.
* Üç cümle: **S1** cümle-başı token'ları · **S2/S3** satır içi duraklama (S3'te etiket
  cümlenin ortasında — aşağıdaki asıl bulgu buradan çıktı).
* Toplam **43 koşul, 528 wav**. Ham veri: `out/token_probe.json` / `out/token_eval.json`.

## 2. SONUÇ TABLOSU

Δsüre = aynı cümlenin etiketsiz hâline göre fark (etiketin gerçekten iş yapıp
yapmadığının kaba göstergesi).

| token | karar | anlaşılan | WER | Δsüre | kusur |
|---|---|---|---|---|---|
| `emotion:affection` | TEMİZ | 12/12 | 0.000 | +0.22 s | — |
| `emotion:amusement` | TEMİZ | 12/12 | 0.000 | +0.31 s | — |
| `emotion:anger` | TEMİZ | 12/12 | 0.000 | -0.04 s | — |
| `emotion:arousal` | TEMİZ | 12/12 | 0.000 | +0.46 s | — |
| `emotion:awe` | TEMİZ | 12/12 | 0.000 | +0.35 s | — |
| `emotion:bitterness` | TEMİZ | 12/12 | 0.000 | -0.13 s | — |
| `emotion:confusion` | TEMİZ | 12/12 | 0.000 | +0.12 s | — |
| `emotion:contemplation` | TEMİZ | 12/12 | 0.000 | +0.69 s | — |
| `emotion:contentment` | TEMİZ | 12/12 | 0.000 | +0.36 s | — |
| `emotion:determination` | TEMİZ | 12/12 | 0.000 | +0.04 s | — |
| `emotion:disgust` | TEMİZ | 12/12 | 0.000 | +0.00 s | — |
| `emotion:elation` | **TEMİZ** | **24/24** | 0.000 | +0.35 s | — ⚠ bkz. §3 |
| `emotion:enthusiasm` | TEMİZ | 12/12 | 0.000 | +0.15 s | — |
| `emotion:fear` | TEMİZ | 12/12 | 0.000 | -0.17 s | — |
| `emotion:helplessness` | TEMİZ | 12/12 | 0.000 | +0.26 s | — |
| `emotion:longing` | TEMİZ | 12/12 | 0.000 | +0.45 s | — |
| `emotion:pride` | TEMİZ | 12/12 | 0.000 | +0.19 s | — |
| `emotion:relief` | TEMİZ | 12/12 | 0.000 | +0.03 s | — |
| `emotion:sadness` | TEMİZ | 12/12 | 0.000 | +0.31 s | — |
| `emotion:shame` | TEMİZ | 12/12 | 0.000 | +0.11 s | — |
| `emotion:surprise` | TEMİZ | 12/12 | 0.000 | -0.02 s | — |
| `prosody:pitch_low` | TEMİZ | 12/12 | 0.000 | +0.77 s | — |
| `prosody:pitch_high` | TEMİZ | 12/12 | 0.000 | +0.08 s | — |
| `prosody:speed_slow` | TEMİZ | 12/12 | 0.000 | +0.09 s | — |
| `prosody:speed_fast` | TEMİZ | 12/12 | 0.000 | -0.20 s | — |
| `prosody:speed_very_fast` | TEMİZ | 12/12 | 0.000 | -0.15 s | — |
| `prosody:expressive_high` | TEMİZ | 12/12 | 0.000 | -0.15 s | — |
| `prosody:expressive_low` | TEMİZ | 12/12 | 0.000 | -0.07 s | — |
| `prosody:speed_very_slow` | **ŞÜPHELİ** | 23/24 | 0.075 | +0.22 s | 1 uydurma |
| `style:shouting` | TEMİZ | 12/12 | 0.000 | +0.18 s | — |
| `style:whispering` | TEMİZ | 12/12 | 0.000 | +0.23 s | — |
| `style:singing` | ŞÜPHELİ | 12/12 | 0.000 | +1.68 s | 2 aşırı uzun |
| `sfx:laughter` | TEMİZ | 12/12 | 0.000 | +0.83 s | — |
| `sfx:sigh` | TEMİZ | 12/12 | 0.000 | +0.90 s | — |
| **`prosody:pause` (cümle ortası, bitişik)** | **TEMİZ** | **24/24** | 0.000 | **+0.32 s** | — |
| **`prosody:long_pause` (cümle ortası, bitişik)** | **TEMİZ** | **24/24** | 0.000 | **+0.50 s** | — |
| `prosody:pause` (boşluklu) | ŞÜPHELİ | 12/12 | 0.036 | +0.62 s | 3 baş yeme |
| `prosody:pause` (cümle başına yakın) | ŞÜPHELİ | 24/24 | 0.012 | +0.68 s | 2 baş yeme |
| `prosody:long_pause` (boşluklu) | ŞÜPHELİ | 12/12 | 0.048 | +0.87 s | 4 baş yeme |
| `prosody:long_pause` (cümle başına yakın) | ŞÜPHELİ | 23/24 | 0.018 | +0.59 s | 1 baş yeme |
| taban S1 / S2 / S3 (etiketsiz) | TEMİZ | 24/24 | 0.000 | — | — |

**37 TEMİZ, 6 ŞÜPHELİ, 0 tamamen bozuk.** Boş çıktı HİÇBİR koşulda görülmedi.

## 3. ⚠️ `elation` düzeltmesi — eski kayıt YANLIŞ

`worker/higgs_tts.py` ve `handoff/2026-07-27-higgs-canliya-gecis.md` "`elation`
5–7/12, cümle başını yiyor, 3 örnek tamamen boş" diyordu. **Canlı yoldan 24 örnekte
24/24 TEMİZ, WER 0.000, boş yok.** Eski ölçüm canlı yoldan (referans klonu +
streaming) değil, deney koşumundan alınmıştı; referans klonu üretimi belirgin biçimde
sabitliyor olmalı.

`excited` yine de `enthusiasm`'da BIRAKILDI: ikisi de temiz, `enthusiasm` canlıda
kullanıcı tarafından onaylandı, ölçülmüş bir kazanç olmadan canlı davranış
değiştirilmez. Dersin kendisi ayakta: **ölçülmemiş token canlıya girmez** — sadece
ölçümün DOĞRU YOLDAN yapılması şartı eklendi.

## 4. ASIL BULGU — `pause` / `long_pause` çalışıyor, ama YERLEŞİM kritik

Duraklama duygu gerektirmiyor ve konuşmanın ritmini doğrudan düzeltiyor; bu yüzden
önce ölçüldü. İki kural ÖLÇÜLDÜ, ikisi de koda girdi:

1. **Token iki yanında BOŞLUK OLMADAN durmalı.** `"Bir saniye <|prosody:pause|>
   düşüneyim"` 12 örnekte 3 kez cümlenin ilk kelimesini yedi; boşluksuzu 0/12.
   (`sfx`'in resmi "arada boşluk yok" kuralıyla aynı.)
2. **Cümle başına yakın duraklama zehirli.** Boşluksuz hâli bile etiket 2. kelimeden
   hemen sonraysa 24'te 2 kez "Bir"i yuttu. **Aynı token cümlenin ortasında
   (4 kelime sonra) 24/24 temiz** ve +0.32 s (`long_pause` +0.50 s) gerçek sessizlik
   ekliyor. Yani sorun token'da değil, token'ın cümle başına yakınlığında.

Koddaki karşılığı: `_HUG_INLINE_RE` boşlukları yutuyor, `_MIN_WORDS_BEFORE_PAUSE = 3`
erken duraklamayı ATIYOR. Şüphede kalırsak duraklamayı kaybederiz, ilk kelimeyi asla.

## 5. Eşlemeye eklenenler (`worker/higgs_tts.py`)

| yeni | → Higgs | neden |
|---|---|---|
| `[pause]` | `<|prosody:pause|>` | ritim; duygu gerektirmiyor, ölçülen en somut kazanç |
| `[long_pause]` | `<|prosody:long_pause|>` | aynısının uzunu |
| `[mood:warm]` | `<|emotion:affection|>` | şefkat/destek — Candan'ın kendi tonu |
| `[mood:calm]` | `<|emotion:contentment|>` | sakinleştirme |
| `[mood:proud]` | `<|emotion:pride|>` | kullanıcı bir şey başardığında |
| `[mood:confused]` | `<|emotion:confusion|>` | "tam anlamadım" |

**Neden hepsi değil:** 37 token temiz çıktı ama temiz olmak yeterli değil —
`anger`, `disgust`, `fear`, `shame`, `bitterness` bir ev asistanının ağzına uymuyor;
`singing`/`shouting` sohbette yeri yok; `speed_*`/`pitch_*` etkisi zayıf (|Δsüre| ≤
0.2 s) ve modelin bunları ne zaman isteyeceği belirsiz. Prompt her turda maliyet:
**6 satır** eklendi, 4 mood + 1 duraklama satırı + 1 örnek.

`[question-*]` hâlâ SİLİNİYOR. `<|prosody:pitch_high|>` temiz çıktı ve "soru tonu"na
benziyor ama **aynı şey değil** — uydurma eşleme olurdu, kulakla doğrulanmadan girmez.

`_MOOD_RE` artık `MOOD_PRESETS`'ten üretiliyor: yeni duygu eklenip regex güncellenmeyi
unutulursa etiket sesli okunurdu; test bunu kilitliyor.

## 6. Anlatılan etiket düzeltmesi (canlı hata, 13:09:46)

```
model yazdı    : "...şaşırdığımda [surprise-oh] gibi efektlerle tepki verebilirim..."
ÖNCE kullanıcı : "...şaşırdığımda ___ gibi efektlerle..."      ← temizleyici sildi, DELİK
ŞİMDİ          : "...şaşırdığımda şaşırma gibi efektlerle..."  ← okunur karşılık
```

Ayırt etme **dar ve sağ bağlamlı**: etiketten hemen sonra `gibi / diye / etiketi /
efektini / yazarak / kullanarak…` geliyorsa ya da etiket tırnak içindeyse
ANLATILIYOR sayılır ve `_READABLE` karşılığına çevrilir. Gerçek kullanımda bu kalıp
görünmüyor — `"[laughter] Bunu gerçekten yaptın mı?"` sağında normal cümle var,
eşleşmiyor (regresyon testiyle kilitlendi).

Anlatılan `[mood:X]` de artık KONTROL işareti sayılmıyor: eskiden tur boyu tonu
değiştiriyordu, şimdi metinde kalıp "üzgün ton" diye okunuyor.

Tanınmayan etiket ANLATILIYORSA yine siliniyor — uydurma karşılık yazılmıyor.

## 7. Kanıt / durum

* Testler: `cd worker && ./.venv/bin/python -m unittest discover -s tests` → **243 OK**
  (232 taban + 11 yeni: `MentionedTagTest` 6, `PauseTagTest` 5).
* Sunucuya giden: `worker/higgs_tts.py`, `pi/AGENTS.md`, `pi/personas/candan.md`
  (üçünün de `.bak-duygu-20260727-1436` yedeği alındı). Sunucudaki dosya
  `/opt/candan-lite/worker/.venv` ile import edildi ve dönüşümler doğrulandı.
* `higgs-tts.service`, `candan-brain`, `whisper`, streaming blok/lookahead mantığı:
  **DOKUNULMADI**. Ölçüm boyunca yalnız HTTP isteği atıldı.
* `candan-worker.service` şu an **KAPALI** — 14:31'de dışarıdan durdurulmuştu, o
  hâlde bırakıldı. Açmak için: `ssh root@192.168.0.25 'systemctl start candan-worker'`
  (yeni prompt ve eşleme ancak o zaman devreye girer).

## 7b. KULAK TESTİ — `[surprise-*]` artık `<|emotion:awe|>` (15:11)

§8/3'teki "duygunun doğru duyulduğu ölçülmedi" maddesinin şaşırma ayağı KAPANDI.

**Sorun:** `<|emotion:surprise|>` ölçümde tertemizdi (12/12, WER 0.000) ama kulakta
şaşkın DUYULMUYORDU. Ölçüm de aslında işaret ediyordu: Δsüre **-0.02 s**, 21 emotion
içinde en düşük — yani tabandan neredeyse farksız. Kullanıcının ilk şikâyeti buydu.

**Deney:** `experiments/higgs-tts3/surprise_set.py` → `out/surprise/`, `out/surprise.json`,
dinleme sayfası `./serve.sh surprise.html`. İki cümle × 8 aday = 16 wav.
* **A** ("Vay canına! Kargon bir gün erken gelmiş.") — ünlem VAR, kolay.
* **B** ("Sınavdan tam not almışsın…") — ünlem YOK, şaşkınlığı yalnız ton taşır: ZOR sınav.

**Kullanıcının kararı (kulakla):**

| cümle | kazanan | süre | Δ |
|---|---|---|---|
| **B (zor, ünlemsiz)** | **`<|emotion:awe|>`** | 4.08 s | +0.64 s |
| A (ünlemli) | `<|emotion:surprise|><|prosody:expressive_high|>` | 3.12 s | -0.04 s |

**Canlıya giren: `awe`** — 27 Tem ölçümünde §2'de zaten var (12/12, WER 0.000,
Δsüre +0.35 s), yani ölçülü. Kombo A'da beğenildi ama **ÖLÇÜLMEDİ → ALINMADI**
(kural: ölçülmemiş token canlıya girmez).

**Kayda geçsin:** `awe` A cümlesinde kullanıcıya "şuh / romantik, yumuşak" duyulmuş.
Ünlemli şaşırma cümlelerinde bu ton rahatsız ederse kombo yeniden gündeme gelir —
ama önce canlı yoldan ölçülmesi şartıyla.

**Değişmeyenler:** etiket adları (`[surprise-ah/oh/wa/yo]`), `pi/AGENTS.md`,
`pi/personas/candan.md`, `_READABLE` karşılığı ("şaşırma"). Prompt maliyeti artmadı;
değişen TEK şey `HIGGS_TAG_MAP`'teki dört değer.

**Doğrulama:** 243 test OK. Deploy: yedek `worker/higgs_tts.py.bak-sasirma-20260727`,
md5 eşleşti (`d2bc1f6f…`), sunucuda import + dönüşüm doğrulandı, `candan-worker`
restart (YALNIZ o — `pi-service`'e dokunulmadı), journalctl traceback 0, bayat
TTS cache silindi (32 `.pcm`).

Geri alma (tek blok):
```bash
ssh root@192.168.0.25 'cd /opt/candan-lite && \
  cp worker/higgs_tts.py.bak-sasirma-20260727 worker/higgs_tts.py && \
  rm -f worker/data/tts-cache/*.pcm && systemctl restart candan-worker'
```

## 8. Bilinen sınır / açık kalan

1. **OmniVoice'a geri dönüş artık İKİ adım.** Prompt'a Higgs'e özgü etiketler girdi;
   `omnivoice_tts._MOOD_RE` yalnız `excited|sad` biliyor ve OmniVoice tanımadığı
   `[...]`'yi HARFİ HARFİNE OKUR. Motoru geri alırken `TTS_ENGINE` satırını silmek
   YETMEZ, `pi/AGENTS.md` + `pi/personas/candan.md` de geri alınmalı (§0'daki blok
   ikisini de yapıyor). Modül docstring'ine de yazıldı.
2. **Duygu SEÇİMİNİN tutarlılığı ölçülmedi.** Mimari karar gereği duyguyu model
   seçiyor (ön sınıflandırıcı YOK, gerekçe görev dosyasında). 2'den 6 moda çıkınca
   model tutarsızlaşırsa bu ölçülebilir; şu an tutarsızlık kanıtı YOK, kanıtsız
   katman eklenmedi.
3. **Duygunun DOĞRU duyulduğu ölçülmedi** — ölçüm "anlaşılıyor mu"yu söylüyor,
   "şefkatli mi duyuluyor"u söylemiyor. Kulak testi: `./serve.sh demo.html`.
   ⚠️ **Şaşırma ayağı KAPANDI** (§7b): kullanıcı dinledi, `surprise` → `awe` oldu.
   Diğerleri (`warm`, `proud`, `calm`, `confused`, `pause`) hâlâ kulakla teyit bekliyor.
4. `speed_very_slow` 24'te 1 kez cümlenin önüne uydurma konuşma ekledi; eşlemeye
   ALINMADI. `style:singing` de 2 aşırı uzun örnek verdi (şarkı için normal olabilir,
   sohbette kullanılmıyor).
