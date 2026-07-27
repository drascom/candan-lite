# VURGU — bir kelimeyi öne çıkarabiliyor muyuz?

Duygu atlası kulakla dinlendiğinde (44 satır) en çok tekrar eden şikâyet duygu
değil **vurgu**ydu — beş satırda, kullanıcının kendi kelimeleriyle:

| token | şikâyet | hedef kelime |
|---|---|---|
| `emotion:affection` | "**olur mu** kısmında vurgu eksik" | olur mu |
| `emotion:arousal` | "**on dakikaya** derken vurgu lazım" | on dakikaya |
| `emotion:contemplation` | "sadece **da** vurgusu eksik" | da (ölçüm dışı) |
| `emotion:awe` | "**tek başına** kısmına da vurgu lazım" | tek başına |
| `kombo:confusion+pause` | "**anlamadım** kelimesinde vurgu yok" | anlamadım |

Eşleme değiştirilmedi, `worker/`'a hiç dokunulmadı; yalnız canlı `POST
/api/tts/stream` istekleri atıldı. Üç tur oldu: **1)** yol var mı (§1-3),
**2)** kulak kararı — kombo kazandı (§4), **3)** kombonun iki kusuru:
kelime sonrası bekleme ve `awe`de vurgunun yanlış kelimeye gitmesi (§5).

## 1. Katalogda vurgu YOK — belge de model de aynı şeyi söylüyor

Resmi `PROMPTING.md` 43 etiket sayıyor (21 emotion + 10 prosody + 3 style +
9 sfx). İçinde `emphasis` / `stress` / `accent` **yok**, SSML benzeri bir
sözdizimi de yok; belge ayrıca uyarıyor: *"Only the tags below are recognized —
anything else degrades output or gets read literally."*

`katalog_yoklama.py` bunu modele de sordu (7 sözdizimi × 3 örnek, canlı uç):

| deneme | WER | sonuç |
|---|---|---|
| taban | 0.000 | — |
| `<\|prosody:emphasis\|>` | 0.222 | **"Prosody, emphasis, sınavdan…"** — harfi harfine okundu 3/3 |
| `<\|emphasis:strong\|>` | 0.222 | **"Emphasis strong. Sınavdan…"** 3/3 |
| `<\|prosody:stress\|>` (satır içi) | 0.370 | "…hem de **belip prosodi stres** tek başına…" |
| `<\|emphasis\|>…<\|/emphasis\|>` | 0.148 | "…tek başına **enfasis** çalışarak" |
| SSML `<emphasis level="strong">` | 0.556 | "…**emphasis level IQ strong**…" |
| `<\|accent:…\|>` | 0.111 | "…**aksent** tek başına…" |

Yani gizli bir vurgu kolu yok. Kalan tek yol **dolaylı**: duraklama, noktalama,
büyük harf, cümle geneli ifade.

## 2. Ölçüm — neden "kontrast"

"Vurgu geldi mi" sorusunun tuzağı şu: `<|prosody:expressive_high|>` cümlenin
TAMAMINI canlandırır. Hedef kelimenin mutlak enerjisi/perdesi yükselir ama
kelime **öne çıkmaz**. Bu yüzden her ölçü hedef kelimenin **cümlenin geri
kalanına oranı**:

    süre    = (hedef süresi / hecesi) ÷ (diğer kelimelerin hece başına süresi)
    enerji  = 20log10 RMS(hedef) − 20log10 RMS(geri kalan)          [dB]
    perde   = 12log2( hedefin F0 tepesi / geri kalanın F0 medyanı ) [yarım ton]

Sonra tabana göre fark. Cümle genelini yükselten yol burada **0** verir.

Kelime sınırları Whisper kelime zaman damgalarından
(`mlx-community/whisper-large-v3-turbo`, `word_timestamps=True`), F0 kendi
otokorelasyon izleyicimizden (numpy, ek bağımlılık yok).

⚠️ **SESSİZLİK KIRPMA — ölçümü ters çeviren ayrıntı.** `<|prosody:pause|>`
sessizliği hedeften ÖNCE ekliyor ve Whisper o sessizliği çoğu zaman kelimenin
span'ine yapıştırıyor. Kırpmadan ölçünce `pause` "kelimeyi %55 uzattı" ve
"enerjisini düşürdü" görünüyordu; ikisi de yanlıştı — sessizliği kırpınca
uzama kayboldu, enerji artıya döndü. Bütün kelime aralıkları (hedef de, geri
kalan da) 10 ms'lik pencerelerde sessizlikten kırpılıyor.

Anlaşılırlık aynı eşikle (`WER ≤ 0.25`, `token_eval.py`/`speed_eval.py` ile
aynı) ölçülür; `bas_yendi` ve "hedef kelime transkriptte bulunamadı" ayrıca
sayılır — büyük harf/noktalama denemelerinde model harf harf okuyabilir.

**Koşul:** 4 cümle × 7 yol × 8 örnek = 224 ses, hepsi canlı
`POST /api/tts/stream` (:8809), referans klonu, streaming.

`emotion:contemplation` ("**da** vurgusu eksik") ölçüme alınmadı: hedef iki
harflik bir bağlaç, 10 ms çözünürlükte kelime sınırı güvenilir değil.

## 3. Sonuç

Tabana göre fark, 4 cümlenin ortalaması (+ = hedef öne çıktı):

| yol | Δ süre | Δ enerji dB | Δ perde yarım ton | WER | anlaşılan |
|---|---|---|---|---|---|
| `expressive_high` | −0.00 | +0.16 | +0.22 | 0.009 | 31/32 |
| **`pause`** (hedeften önce, bitişik) | +0.15 | +0.16 | **+1.22** | 0.000 | 32/32 |
| `buyuk` (BÜYÜK HARF) | +0.11 | −0.42 | −0.88 | 0.000 | 32/32 |
| `virgul` (iki yana virgül) | +0.22 | −1.36 | −0.57 | 0.000 | 32/32 |
| `tire` (— hedef —) | +0.31 | −1.43 | −0.60 | 0.000 | 32/32 |
| `kombo` = `pause` + `tire` | +0.19 | −0.84 | −0.03 | 0.000 | 32/32 |

Cümle cümle (Δ perde, yarım ton) — **tutarlılık asıl mesele**:

| yol | affection | arousal | awe | confusion |
|---|---|---|---|---|
| `pause` | +0.92 | +1.36 | +1.56 | +1.05 |
| `expressive_high` | +1.77 | +0.47 | +0.06 | −1.41 |
| `tire` | −0.02 | −1.57 | +0.68 | −1.50 |
| `buyuk` | −2.31 | −1.34 | +0.60 | −0.47 |
| `kombo` | +0.05 | −0.78 | +1.70 | −1.08 |

Okunuşu:

* **`<|prosody:pause|>` tek tutarlı yol.** Dört cümlenin dördünde de hedefin
  perde tepesini yükseltiyor (+1.2 yarım ton ortalama) ve önüne ~150 ms
  sessizlik koyuyor. Ama kelimeyi **uzatmıyor** (Δ süre +0.15, cümleye göre
  değişken) — yaptığı şey "duraklat, sonra kelimeye taze bir perdeyle gir".
  Bu klasik anlamda hece vurgusu DEĞİL; dikkat çekmenin başka bir yolu.
  Anlaşılırlık bedava: WER 0.000, baş yeme 0/32 — `confusion`'da etiket
  2. kelimenin önünde olmasına rağmen ilk kelime yenmedi.
* **`expressive_high` vurgu yapmıyor**, beklendiği gibi: Δ süre −0.00. Cümlenin
  tamamını canlandırıyor, hedefi ayırt etmiyor; işareti cümleden cümleye
  değişiyor (+1.77 … −1.41).
* **Büyük harf ÇALIŞMIYOR.** "Modeller çoğu zaman buna tepki verir" varsayımı
  Higgs+Türkçe için yanlış: hiçbir ölçüde artı yok, perde ortalama −0.88.
  Tek iyi haber, harf harf okuma tuzağına da düşmedi (WER 0.000).
* **Noktalama (virgül / tire) uzatıyor ama söndürüyor.** En büyük Δ süre
  onlarda (+0.22 / +0.31), ama enerji −1.4 dB ve perde −0.6: hedef kelime
  yalıtılıyor, karşılığında sıralama/ara cümle tonuna kayıyor. Vurgu değil,
  parantez.
* **Kombo (`pause` + `tire`) TOPLANMIYOR, BİRBİRİNİ SİLİYOR.** Fikir "hem uzun
  hem tiz hedef"ti; çıkan sonuç tirenin perde düşürmesinin duraklamanın +1.22
  kazancını yemesi (kombo perde −0.03). Uzama da tek başına tireden az (+0.19 <
  +0.31). İki yol birlikte, `pause` tek başına kadar bile iyi değil.

**Yol yok denemez ama "vurgu var" da denemez:** kelime düzeyinde gerçek vurgu
(hece uzatma + perde tepesi + enerji birlikte) hiçbir yolda çıkmadı. Ölçümün
verdiği tek sağlam aday `pause`; karar kulakta.

## 4. KULAK ÖLÇÜMÜ YENDİ (2. tur) — ve ölçüm neyi göremiyor

Kullanıcı seti örnek örnek dinledi. "3/3 tuttu mu" tablosu:

| yol | affection | arousal | awe | confusion |
|---|---|---|---|---|
| taban | 0/3 | 0/3 | 1/3 | 0/3 |
| `expressive_high` | 0/3 | 0/3 | 0/3 | 0/3 |
| `pause` | **3/3** | 2/3 | 0/3 | 1/3 |
| `tire` | **3/3** | 0/3 | (işaretsiz) | **3/3** |
| **`kombo`** | **3/3** | **3/3** | 0/3 | **3/3** |

**Kombo kazandı** — ölçümün ELEDİĞİ yol. Ölçüm "kombo birbirini siliyor"
demişti (Δ perde −0.03), kulak dört cümlenin üçünde tam not verdi.

> **DERS — ölçüm tonun BİR boyutunu görüyor, hepsini değil.** Aynı gün ikinci
> kez oldu (`surprise`+`expressive_high` de öyleydi). Δ perde/enerji/süre bir
> yolu **eleyemez**; olsa olsa aday sıralar. Eleme kulakta. Bu belgede sayı
> gördüğün her yerde bu paragrafı hatırla.

`expressive_high` dört cümlede de sıfır çekti — **ölü**, aday listesinden
çıkarıldı.

## 5. 3. tur — "kelime sonrası uzun bekleme"

Kombo kazandı ama kullanıcı altı ayrı notta aynı şeyi yazdı: *"on dakikaya
sonrasına uzun bekleme"*, *"vurgu güzel ama kelime sonrası uzun bekleme"*,
*"şimdi sonrası tekrarlayan bekleme bozuyor"*, *"sonrasında uzun bekleme"*.

**Koda bakıldı, sebep bulundu.** `_tireli()` tireyi hedefin **İKİ YANINA**
koyuyordu (`f"{on} — {hedef} — {arka}"`), kombo da bunun önüne duraklamayı
ekliyordu:

    kombo:    Hadi kalk bakalım —<|prosody:pause|>on dakikaya — çıkmamız gerekiyor!
                                ↑ vurguyu getiren             ↑ YALNIZ BEKLEME

Öndeki tire+duraklama vurguyu getiriyor; **arkadaki tire hiçbir şeye
yaramadan bekleme üretiyor**. Yeni yollar arkadaki işareti atar:

| yol | ne yapar |
|---|---|
| `kombo-on` | tire+duraklama yalnız ÖNDE, arkada hiçbir şey yok |
| `tire-on` | yalnız öndeki tire (duraklama bile yok) |
| `kombo-on-dar` | yalnız `awe`: sınır 2. turdaki yerinde, arkada işaret yok |

### Yeni ölçü: kelime SONRASI bekleme

2. turda bu büyüklük **hiç ölçülmemişti** — asıl şikâyet oydu. `vurgu_eval.py`
artık `arka_bosluk_s` ve `on_bosluk_s` üretiyor: hedefin ve komşusunun
**kırpılmış** sınırları arasındaki gerçek sessizlik (ham Whisper damgası
kullanılamaz, sessizliği keyfî bir yana yapıştırıyor — bkz. §2).

Hedef kelimeden sonraki bekleme, saniye (tabana göre FAZLASI):

| yol | affection | arousal | awe | confusion | ORT |
|---|---|---|---|---|---|
| `pause` | +0.192 | +0.005 | +0.000 | +0.000 | +0.049 |
| `tire` | +0.052 | +0.366 | +0.247 | +0.371 | +0.259 |
| **`kombo`** | +0.132 | **+0.501** | +0.051 | **+0.556** | **+0.310** |
| **`kombo-on`** | −0.007 | +0.001 | +0.000 | +0.000 | **−0.002** |
| `tire-on` | −0.093 | +0.001 | +0.000 | +0.000 | −0.023 |

**Hipotez doğrulandı, hem kodda hem sayıda.** Kombo hedeften sonra ortalama
**0.31 saniye** fazladan bekleme koyuyordu (`confusion` 0.56 s, `arousal`
0.50 s — kullanıcının şikâyet ettiği iki cümle tam da bunlar). `kombo-on`'da
bu bekleme **tamamen** gitti (−0.002 s, yani taban seviyesi) ve öndeki
duraklama **duruyor** (ön boşluk 0.68 / 0.60 / 0.48 s).

Mutlak sayıya aldanma: `affection`da hedeften sonra zaten VİRGÜL var, tabanda
bile 0.489 s bekleme oluyor. Önemli olan **yolun eklediği** fazla.

### `awe` — vurgu yanlış kelimeye gitmişti

Kullanıcı: *"vurgu hem de kelimesinde"*. Hedef `tek başına`ydı.

**Kod hatası değil, TANIM hatası.** İşaret tam da tanımın söylediği yere
konmuştu (`on` ile `hedef` arasına); yanlış olan tanımın kendisiydi:

    Sınavdan tam not almışsın, hem de —<|prosody:pause|>tek başına — çalışarak.
                               ↑ sınırdan ÖNCEKİ kelime öne çıkar

İki sebep üst üste geldi: (a) Türkçede öbek sınırından **önceki** kelime
öbek-sonu belirginliği alır, (b) "hem de" hedefe bağlı bir pekiştirici — ondan
koparılınca vurguyu kendine çeker. Diğer üç cümlede sınırdan önce sıradan bir
kelime vardı (`unutma`, `bakalım`, `Tam`), o yüzden sorun çıkmadı.

Düzeltme: `CUMLELER`e `bagli` alanı eklendi — sınırın **arkasına** düşmesi
gereken parça. `awe` için `bagli = "hem de "`, sınır artık onun **önünde**:

    Sınavdan tam not almışsın —<|prosody:pause|>hem de tek başına çalışarak.

`bagli` yalnız yeni `*-on` yollarını etkiler; eski yollar `on + bagli`yi tek
parça görür, yani **2. turun metinleri ve wav'ları harfi harfine aynı kaldı**
(kıyas zemini bozulmadı; testle doğrulandı).

Ölçüm `awe`de ne diyor (Δ perde, yarım ton): `kombo` +1.70 · `kombo-on-dar`
**+2.17** · `kombo-on` −0.72 · `tire-on` −1.98. Yani **ölçüme göre eski sınır
daha iyi**. Ama ölçüm bu deneyde iki kez yanıldı (§4) ve kullanıcının duyduğu
şey ölçünün göremediği şeydi: perde tepesi HANGİ kelimede. O yüzden sayfada
her iki sınır da var, karar kulakta.

## 6. Kulak seti (3. tur)

```bash
cd experiments/vurgu && ./serve.sh        # http://localhost:8012/vurgu-seti.html
```

4 cümle × 4 yol × 3 örnek (`awe`de 4 yol, ötekilerde 3) — **48 örnek**.
Gösterilen yollar: `kombo` (kıyas zemini) · `kombo-on` · `tire-on` ·
`kombo-on-dar` (yalnız `awe`). `taban`/`pause`/`tire`/`expressive_high` bu tura
alınmadı: hepsi 2. turda dinlendi (60 örnek), tekrar dinletmenin bilgisi yok.

Her satırda **kelime SONRASI bekleme** saniyesi ve tabanın kaç saniye olduğu
yazılı; renk **yolun eklediği fazlaya** göre. Örnek başına iki ayrı işaret:
vurgu kararı (geldi / az / gelmedi) ve **bağımsız** `⏱ bekleme` düğmesi —
"vurgu geldi AMA bekleme bozdu" tek düğmede kaybolurdu.

Notlar `vurgu-seti-notlar-v3` anahtarında; **v1 ve v2 yedekleri silinmiyor,
taşınmıyor, yazılmıyor**. 2. tur kararı `kombo` satırında rozet olarak görünür.
"notları JSON kopyala" `tur: 3` üretir.

## 7. Yeniden koşum

```bash
# 0) katalogda vurgu var mı (belgeye ek olarak modele sor)
../tts-local-bench/venvs/whisper/bin/python katalog_yoklama.py

# 1) ses üret (canlı uç; üretilmiş wav'a dokunmaz, --yenile ile baştan)
python3 vurgu_probe.py --n 8 --yollar taban expressive_high pause buyuk virgul tire
python3 vurgu_probe.py --n 8 --yollar kombo
python3 vurgu_probe.py --n 5 --yollar kombo-on tire-on                 # 3. tur
python3 vurgu_probe.py --n 5 --yollar kombo-on-dar --cumleler awe      # yalnız awe

# 2) ölç (Whisper kelime zaman damgaları + F0/RMS)
../tts-local-bench/venvs/whisper/bin/python vurgu_eval.py

# 3) dinle
./serve.sh
```

`vurgu_set.py` gönderilen metinleri tek başına da basar:
`python3 vurgu_set.py`.

Dosyalar: `vurgu_set.py` (cümleler + yollar, tek kaynak) · `vurgu_probe.py`
(üretim) · `vurgu_eval.py` (ölçüm) · `katalog_yoklama.py` (katalog boşluğu) ·
`vurgu-seti.html` + `serve.sh` (kulak seti) · `out/` (wav + JSON, git dışı).
