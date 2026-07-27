# Vurgu 3. tur — kelime sonrası bekleme + `awe`de yanlış kelime (28 Tem)

Önceki tur: `handoff/2026-07-28-vurgu.md`. Takım: `experiments/vurgu/`.
Ayrıntılı anlatım: `experiments/vurgu/README.md` §4-6.

`worker/` altında **hiçbir dosyaya dokunulmadı**, sunucuya değişiklik yok,
`higgs-tts` restart edilmedi. Yalnız `POST /api/tts/stream` istekleri
(45 yeni ses). Eşleme değişikliği hâlâ bu turun işi değil.

## 0. Nereden geldik

2. turda kullanıcı seti kulakla dinledi ve **kombo** (`pause`+`tire`) kazandı —
4 cümlenin 3'ünde 3/3. Ama iki kusur bıraktı:

* **A)** altı ayrı notta *"kelime sonrası uzun bekleme"*
* **B)** `awe` cümlesinde *"vurgu hem de kelimesinde"* (hedef `tek başına`ydı)

> **DERS (belgeye yazılması istenen):** kombo'yu ÖLÇÜM ELEMİŞTİ (Δ perde −0.03,
> "birbirini siliyor"); kulak tersini söyledi. Aynı gün ikinci kez oldu
> (`surprise`+`expressive_high`). **Ölçüm tonun bir boyutunu görüyor, hepsini
> değil.** Δ perde/enerji/süre bir yolu eleyemez, olsa olsa aday sıralar.
> Eleme kulakta. `expressive_high` ise dört cümlede de sıfır çekti — ölü,
> aday listesinden çıkarıldı.

## 1. A — hipotez DOĞRU çıktı, hem kodda hem sayıda

Hipotez: tire hedefin iki yanına konuyor, arkadaki yalnız bekleme üretiyor.

**Kodda gerçekte şöyleymiş.** `vurgu_set.py::_tireli()` tireyi harfiyen iki
yana koyuyor (`f"{on} — {hedef} — {arka}"`), `kombo` da bunun önüne duraklamayı
enjekte ediyor. Gönderilen metin:

    kombo:  Hadi kalk bakalım —<|prosody:pause|>on dakikaya — çıkmamız gerekiyor!
                              ↑ vurguyu getiren            ↑ YALNIZ BEKLEME

Yani hedef kelime **önde tire+duraklama, arkada çıplak tire** ile
kuşatılmıştı. Hipotez birebir doğru.

### Yeni ölçü: `arka_bosluk_s`

2. turda kelime SONRASI sessizlik **hiç ölçülmemişti** — asıl şikâyet oydu.
`vurgu_eval.py` artık `arka_bosluk_s` + `on_bosluk_s` üretiyor: hedefin ve
komşu kelimenin **kırpılmış** sınırları arasındaki gerçek sessizlik. Ham
Whisper damgası kullanılamaz (sessizliği keyfî bir yana yapıştırıyor; 1. turu
ters çeviren tuzağın aynısı).

Hedeften sonraki bekleme, saniye — **tabana göre fazlası**:

| yol | affection | arousal | awe | confusion | ORT |
|---|---|---|---|---|---|
| `pause` | +0.192 | +0.005 | +0.000 | +0.000 | +0.049 |
| `tire` | +0.052 | +0.366 | +0.247 | +0.371 | +0.259 |
| **`kombo`** (2. tur) | +0.132 | **+0.501** | +0.051 | **+0.556** | **+0.310** |
| **`kombo-on`** (yeni) | −0.007 | +0.001 | +0.000 | +0.000 | **−0.002** |
| `tire-on` (yeni) | −0.093 | +0.001 | +0.000 | +0.000 | −0.023 |

Mutlak saniye (aynı satırlar): taban 0.127 · `kombo` **0.437** ·
`kombo-on` **0.126** · `tire-on` 0.104.

**Öncesi/sonrası tek cümlede:** kombo hedeften sonra ortalama **0.31 s**
fazladan bekleme koyuyordu (`confusion` 0.56 s, `arousal` 0.50 s — kullanıcının
şikâyet ettiği iki cümle tam da bunlar). `kombo-on`'da bekleme **sıfırlandı**
(−0.002 s = taban seviyesi), öndeki duraklama ise **duruyor** (kelime öncesi
boşluk 0.68 / 0.60 / 0.48 s; kombo'da 0.65 / 0.59 / 0.32 s).

Mutlak sayıya aldanmamalı: `affection`da hedeften sonra zaten VİRGÜL var,
tabanda bile 0.489 s bekleme oluyor. Bakılacak olan **yolun eklediği fazla**.

Anlaşılırlık bedava: yeni yolların ikisinde de WER 0.000, 20/20 anlaşıldı,
baş yeme 0, hedef 20/20 bulundu.

## 2. B — `awe`de yerleşim: KOD hatası değil, TANIM hatası

İşaret tam da tanımın söylediği yere konmuştu (`on` ile `hedef` arasına).
Yanlış olan tanımın kendisiydi:

    Sınavdan tam not almışsın, hem de —<|prosody:pause|>tek başına — çalışarak.
                               ↑ sınırdan ÖNCEKİ kelime öne çıkar

İki sebep üst üste geldi:
1. Türkçede öbek sınırından **önceki** kelime öbek-sonu belirginliği alır.
2. "hem de" hedefe **bağlı** bir pekiştirici; ondan koparılınca vurguyu
   kendine çeker.

Diğer üç cümlede sınırdan önce sıradan bir kelime vardı (`unutma`, `bakalım`,
`Tam`), o yüzden aynı hata görünmedi.

**Düzeltme:** `CUMLELER`e `bagli` alanı eklendi — sınırın **arkasına** düşmesi
gereken parça. `awe` için `bagli = "hem de "`:

    kombo-on:  Sınavdan tam not almışsın —<|prosody:pause|>hem de tek başına çalışarak.

`bagli` yalnız yeni `*-on` yollarını etkiliyor; eski yollar `on + bagli`yi tek
parça görüyor, yani **2. turun 32 metni harfi harfine aynı kaldı** (test edildi:
0/32 değişti) ve eski wav'lar geçerli, kıyas zemini bozulmadı.

A'yı B'den ayırmak için **yalnız `awe`ye** üçüncü bir yol üretildi:
`kombo-on-dar` — sınır 2. turdaki yerinde ("hem de"den SONRA) ama arkada
işaret yok. Böylece "beklemeyi mi düzelttik, kelimeyi mi" ayrı ayrı duyulur.

Ölçüm `awe`de ne diyor (Δ perde, yarım ton): `kombo` +1.70 · **`kombo-on-dar`
+2.17** · `kombo-on` −0.72 · `tire-on` −1.98 → **ölçüme göre ESKİ sınır daha
iyi.** Ama ölçüm bu deneyde iki kez yanıldı ve kullanıcının duyduğu şey
ölçünün göremediği şeydi: perde tepesi **hangi kelimede**. Karar kulakta;
sayfada iki sınır da var.

## 3. Kulak seti — 3. tur

```bash
cd experiments/vurgu && ./serve.sh        # http://localhost:8012/vurgu-seti.html
```

* **48 örnek**: 4 cümle × 3 yol × 3 örnek + `awe`ye 1 yol daha × 3.
  Yollar: `kombo` (kıyas zemini) · `kombo-on` · `tire-on` ·
  `kombo-on-dar` (yalnız `awe`).
* `taban`/`pause`/`tire`/`expressive_high` **alınmadı** — hepsi 2. turda
  dinlendi (60 örnek), tekrar dinletmenin bilgisi yok.
* Her satırda **kelime SONRASI bekleme** saniyesi + tabanın kaç saniye olduğu;
  renk yolun EKLEDİĞİ fazlaya göre (yeşil / sarı / kırmızı).
* Örnek başına **iki ayrı işaret**: vurgu kararı (geldi / az / gelmedi) ve
  **bağımsız** `⏱ bekleme` düğmesi. "Vurgu geldi AMA bekleme bozdu" tek
  düğmede kaybolurdu — bu turun bütün sorusu o.
* Notlar `vurgu-seti-notlar-v3` anahtarında. **v1 ve v2 okunmuyor bile
  (v2 salt-okunur rozet hariç), yazılmıyor, silinmiyor.** "notları sil" yalnız
  v3'ü siler. `kombo` satırında "2. tur: 3/3 geldi" rozeti çıkar.
* "notları JSON kopyala" → `tur: 3`, örnek başına `kararlar[]` + `bekleme[]`.

## 4. Kullanıcıdan beklenen

Her cümlede sırayla `kombo` → `kombo-on` → `tire-on` dinlenip iki soru:
**(1)** vurgu duruyor mu, **(2)** kelimeden sonraki bekleme gitti mi.
`awe`de ayrıca `kombo-on-dar` ile `kombo-on` kıyaslanacak: hangisinde vurgu
"hem de"ye değil **"tek başına"**ya biniyor?

Çıkacak sonuçlar:
* `kombo-on` hem vurguyu tutar hem beklemeyi kaldırırsa → eşleme adayı budur;
  bir sonraki tur `worker/higgs_tts.py` tarafı (vurgulanacak kelimenin önüne
  ` —` + `[pause]`, arkasına **hiçbir şey**).
* `tire-on` de yetiyorsa daha ucuz: duraklama etiketi hiç gerekmez.
* Vurgu `kombo-on`'da kaybolursa → arkadaki tire vurgunun bir parçasıymış
  demektir, o zaman "vurgu var ama bedeli bekleme" diye kabul edilir ya da
  konu kapanır. **"Olmuyor" demek de geçerli bir sonuç.**

## 5. Değişen dosyalar

* `experiments/vurgu/vurgu_set.py` — `bagli` alanı (`awe`), `_on_isaretli()`,
  `kombo-on` / `tire-on` / `kombo-on-dar` yolları. Eski metinler değişmedi.
* `experiments/vurgu/vurgu_eval.py` — `arka_bosluk_s` + `on_bosluk_s` ölçüsü,
  mutlak "kelime SONRASI bekleme" tablosu, Δ tablosuna aynı alan.
* `experiments/vurgu/vurgu-seti.html` — 3. tur: yeni yollar, `v3` anahtarı,
  bekleme saniyesi satırı, bağımsız `⏱ bekleme` düğmesi, SUNULAN sırası.
* `experiments/vurgu/README.md` — §4 (kulak ölçümü yendi + ders),
  §5 (3. tur, A ve B), §6 (kulak seti), §7 (yeniden koşum).
