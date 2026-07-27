# Kombo ölçümü — kulakla seçilen `emotion+prosody` çiftleri (27 Tem)

Görev: `handoff/task-2026-07-27-kombo-olcumu.md`.
Takım: `experiments/higgs-tts3/kombo_probe.py` (yeni) + `token_eval.py` (değişmedi).
Kulak seti: `experiments/higgs-tts3/kombo_set.py` + `kombo.html`.

**Bu turda `worker/` altında HİÇBİR dosyaya dokunulmadı, sunucuya hiçbir değişiklik
yapılmadı, `higgs-tts` servisi restart edilmedi.** Yalnız `POST /api/tts/stream`
istekleri atıldı. Eşleme değişikliği bu belgenin ÖNERİSİ, uygulaması ayrı tur.

## 1. Yöntem — 43 token'lık ölçümle AYNI

Kombolar atlasta (`experiments/duygu-atlasi/`) kulakla beğenildi ama hiç ölçülmemişti.
Kural ayakta: **ölçülmemiş token canlıya girmez.** Tekil token'ın TEMİZ olması
komboyu temiz yapmaz — iki kontrol token'ı arka arkaya gelince model onları metin
sanabilir ya da cümlenin başını yiyebilir.

* Ölçüm **CANLI YOLDAN**: `POST /api/tts/stream` (192.168.0.25:8809), referans klonu,
  streaming. Deney koşumu değil — eski `elation` yanlış teşhisi tam bundan çıkmıştı.
* Kombo başına **12 örnek**, Whisper (`mlx-community/whisper-large-v3-turbo`)
  geri-dönüşü, WER + atlanan kelime.
* Karar **dört ölçüte** dayanıyor (öncekiyle aynı): boş çıktı · çok kısa (medyanın
  yarısından kısa) · uydurma konuşma (medyanın 2 katından uzun) · cümlenin başını yeme.
  TEMİZ = anlaşılan oran ≥ %90 **ve** dört kusurdan hiçbiri yok. Eşik `WER_OK = 0.25`.
* Ölçüm aynı `out/token_probe.json` / `out/token_eval.json` dosyalarına eklendi;
  sayılar 43 token'lık tabloyla doğrudan kıyaslanabilir.
* Toplam **14 koşul (10 kombo + 4 taban), 168 wav.** HTTP hatası 0.

**Cümleler gerçek kullanım cümleleri** — atlasın dersi buydu, nötr tek cümle bir
duyguyu gösteremez. Cümleler atlastaki kombo satırlarıyla aynı, ki kulak notu ile
ölçüm aynı sesi anlatsın:

| kod | cümle | neden |
|---|---|---|
| U1 | Vay canına! Kargon tam bir gün erken gelmiş. | ünlemli — kelime şaşkınlığı zaten taşıyor |
| U2 | Sınavdan tam not almışsın, hem de tek başına çalışarak. | **ünlemsiz — şaşkınlığı yalnız ton taşıyor, zor sınav** |
| P1 | Gerçekten başardın işte, hem de tek başına. | gurur |
| C1 | Acelesi yok, her şey yolunda. Önce bir nefes al. | sakinleştirme |

⚠️ **Δsüre ELEME ÖLÇÜTÜ DEĞİL** (kullanıcı kararı: *"çalışıyorlarsa süresi çok önemli
değil"*). Aşağıda etiketin iş yapıp yapmadığının kaba göstergesi olarak duruyor;
ret sebebi yalnız anlaşılırlıktır.

## 2. SONUÇ TABLOSU

Δsüre = aynı cümlenin **etiketsiz** hâline göre fark (taban da 12 örnek).
Sıra: `eo` = `<|emotion|><|prosody|>`, `oe` = `<|prosody|><|emotion|>`.

| kombo | cümle | sıra | karar | anlaşılan | WER | Δsüre | kusur |
|---|---|---|---|---|---|---|---|
| `emotion:surprise` + `prosody:expressive_high` | U1 (ünlemli) | eo | **TEMİZ** | 12/12 | 0.000 | +0.18 s | — |
| `emotion:surprise` + `prosody:expressive_high` | U1 (ünlemli) | oe | **TEMİZ** | 12/12 | 0.010 | +0.12 s | — |
| `emotion:surprise` + `prosody:expressive_high` | U2 (ünlemsiz) | eo | **TEMİZ** | 12/12 | 0.000 | +0.09 s | — |
| `emotion:surprise` + `prosody:expressive_high` | U2 (ünlemsiz) | oe | **TEMİZ** | 12/12 | 0.000 | +0.06 s | — |
| `emotion:pride` + `prosody:expressive_high` | P1 | eo | **TEMİZ** | 12/12 | 0.000 | +0.42 s | — |
| `emotion:pride` + `prosody:expressive_high` | P1 | oe | **TEMİZ** | 12/12 | 0.000 | +0.26 s | — |
| `emotion:contentment` + `prosody:expressive_low` | C1 | eo | **TEMİZ** | 12/12 | 0.000 | +0.94 s | — |
| `emotion:contentment` + `prosody:expressive_low` | C1 | oe | **TEMİZ** | 12/12 | 0.000 | +1.02 s | — |
| `emotion:awe` + `prosody:expressive_high` | U2 | eo | **TEMİZ** | 12/12 | 0.000 | +0.73 s | — |
| `emotion:awe` + `prosody:expressive_high` | U2 | oe | **TEMİZ** | 12/12 | 0.000 | +0.65 s | — |
| taban U1 / U2 / P1 / C1 (etiketsiz) | — | — | TEMİZ | 12/12 | 0.000 | — | — |

**10 koşulun onu da TEMİZ. 0 ŞÜPHELİ, 0 BOZUK.** Boş çıktı yok, çok kısa yok,
uydurma konuşma yok, **cümle başı yeme yok** (asıl korkulan kusur buydu: iki etiket
arka arkaya gelince model ilk kelimeyi yutabilir — 168 örnekte hiç olmadı).

Tek WER'li örnek: `surprise+expressive_high@U1/oe` #9'da Whisper "Kargon" yerine
"Kargom" yazdı (WER 0.125, eşiğin altında). Tek harflik ASR kayması, kelime düşmesi
değil — kusur sayılmadı.

**Δsüre gözlemi (karara girmez, ama söylemeye değer):** `contentment+expressive_low`
(+0.94 s) ve `awe+expressive_high` (+0.73 s) cümleye belirgin biçimde dokunuyor;
`surprise+expressive_high` ünlemsiz cümlede yalnız **+0.09 s** — 10 koşulun en zayıf
etkisi. Tekil `surprise`'ın 27 Tem'de Δsüre'si en düşük token olması (-0.02 s) ve
kulakta şaşkın duyulmaması bu ölçüde yankılanıyor: **`expressive_high` eklemek
`surprise`'ı ölçülebilir biçimde canlandırmıyor.** Kanıt değil, işaret — son sözü
kulak söyleyecek (§5).

## 3. Token sırası — FARK ETMİYOR

Beş kombonun **ikisi sırası da** ayrı ayrı ölçüldü (bir tanesi değil; ucuzdu, kesin
cevap verdi).

* **Anlaşılırlıkta fark yok:** on koşulun onu da 12/12 TEMİZ.
* **Süre farkı gürültünün içinde.** Sıralar arası ortalama fark, koşulların kendi
  standart sapmasından küçük:

| kombo | eo | oe | fark | eo'nun s.sapması |
|---|---|---|---|---|
| `surprise+expressive_high@U1` | 3.410 s | 3.350 s | +0.060 | 0.144 |
| `surprise+expressive_high@U2` | 3.507 s | 3.477 s | +0.030 | 0.151 |
| `pride+expressive_high@P1` | 3.163 s | 3.010 s | +0.153 | 0.173 |
| `contentment+expressive_low@C1` | 4.353 s | 4.427 s | −0.073 | 0.222 |
| `awe+expressive_high@U2` | 4.153 s | 4.070 s | +0.083 | 0.184 |

**Karar: `<|emotion:X|><|prosody:Y|>` (emotion önce) kullanılsın.** Sebep ölçüm değil
tutarlılık: atlasta kulakla dinlenen sıra bu, resmi PROMPTING.md örnekleri de bu sırayı
yazıyor. Ölçüm bu seçimi serbest bırakıyor — ters sıra da güvenli, iki sıra da
canlıya girebilir.

## 4. ÖNERİ — hangi kombo hangi etikete

⚠️ Öneri; **uygulanmadı**. `worker/higgs_tts.py` bu turda hiç açılmadı.

| etiket | şu an canlıda | ölçülmüş öneri | gerekçe |
|---|---|---|---|
| `[surprise-*]` | `<\|emotion:awe\|>` (tekil) | `<\|emotion:surprise\|><\|prosody:expressive_high\|>` | tekil `awe` kulakta **KÖTÜ** ("şuh kalmış, heyecan yok"); kombo A/B'de ünlemli cümlede seçilmişti, iki cümlede de TEMİZ |
| `[mood:proud]` | `<\|emotion:pride\|>` | `<\|emotion:pride\|><\|prosody:expressive_high\|>` | kullanıcı **"mükemmel"** dedi, TEMİZ, Δsüre +0.42 s (etiket gerçekten iş yapıyor) |
| `[mood:calm]` | `<\|emotion:contentment\|>` | `<\|emotion:contentment\|><\|prosody:expressive_low\|>` | kulakta "iyi", TEMİZ, en güçlü Δsüre (+0.94 s) |
| — | — | `<\|emotion:awe\|><\|prosody:expressive_high\|>` = **yedek** | tek başına `awe` kötüydü ama kombosu TEMİZ ve ünlemsiz cümlede +0.73 s; `[surprise-*]` kulakta tutmazsa sıradaki aday bu |

`[question-*]` hâlâ SİLİNİYOR — bu turda ölçülmedi, değişmiyor.

## 5. Kulak seti — son söz kullanıcının

Ölçüm "anlaşılıyor mu"yu cevapladı; "kombo tekilden GERÇEKTEN daha iyi mi"yi
cevaplamadı. Sayfa her satırda **dört sesi yan yana** koyuyor —
**kombo · yalnız emotion · yalnız prosody · taban (etiketsiz)** — artı ters sıra düğmesi.

```bash
cd experiments/higgs-tts3
python3 kombo_probe.py            # ölçüm (yapıldı; wav varsa yeniden üretmez)
python3 kombo_set.py              # kulak setini kur (tekil parçaları üretir)
./serve.sh kombo.html             # http://localhost:8009/kombo.html
```

Kombo ve taban sesleri ölçüm koşumundan alınıyor — yeniden üretilmiyor; her koşulun
12 örneğinden **süresi medyana en yakın** olan seçiliyor ki uç örnek dinletilmesin.
Sayfa "komboyu al / tekil yeter / hiçbiri" işaretlemesi + serbest not tutuyor,
en altta JSON kopyalanıyor (atlas sayfasıyla aynı desen, `localStorage`).

**Asıl bakılacak satır 2** (`surprise+expressive_high` @ ünlemsiz cümle): §2'deki
Δsüre işareti bu kombonun zayıf olabileceğini söylüyor. Aynı cümlede satır 5
(`awe+expressive_high`) yedek — ikisi arka arkaya dinlenip seçilmeli.

## 5b. KULAK SONUCU — beşinin beşi de "komboyu al" (20:55 UTC)

Kullanıcı setin tamamını dinledi ve **beş kombonun beşini de seçti.** Ham not
sayfadan JSON olarak alındı:

| kombo | karar | kullanıcının notu |
|---|---|---|
| `surprise+expressive_high@U1` | **kombo** | "tek tek sürpriz sürpriz gibi, `expressive_high` şaşırmayı gayet güzel veriyor. Kombo her ikisinin tam bir karışımı olmuş. **Farklı durumlarda üçünü de ayrı ayrı kullanabiliriz.**" |
| `surprise+expressive_high@U2` | **kombo** | "Hem kombo hem yalnız `surprise` gayet güzel." |
| `pride+expressive_high@P1` | **kombo** | "**Kesinlikle kombo daha güzel.**" |
| `contentment+expressive_low@C1` | **kombo** | — |
| `awe+expressive_high@U2` | **kombo** | — |

Buradan çıkan üç şey:

1. **§4'teki öneri tabloso kulakla onaylandı.** Üç eşleme değişikliği de aday:
   `[surprise-*]`, `[mood:proud]`, `[mood:calm]`. Uygulama SONRAKİ TUR.
2. ⚠️ **Δsüre işareti YANILDI — ve bu bir ders.** `surprise+expressive_high@U2`
   on koşulun en zayıf Δsüre'siydi (+0.09 s) ve "zayıf olabilir" diye işaretlenmişti;
   kullanıcı onu "gayet güzel" buldu. DEVİR'deki *"Δsüre ≈ 0 olan bir duygu token'ı
   büyük ihtimalle hiçbir şey yapmıyordur"* sezgisi **kombolar için geçerli değil**:
   `expressive_high` süreyi uzatmadan tonu değiştirebiliyor. Δsüre'yi eleme ölçütü
   yapmama kararı (kullanıcının kararıydı) burada kendini doğruladı.
3. **Yeni fikir — `expressive_high` kendi başına bir kol olabilir.** Kullanıcının U1
   notu üç sesin de (tekil `surprise`, tekil `expressive_high`, kombo) ayrı ayrı
   kullanılabileceğini söylüyor. Yani `expressive_high` duygu değil **vurgu** kolu;
   duygudan bağımsız bir etikete (ör. `[emphasis]`) bağlanabilir. TEMİZ ölçümü zaten
   var (27 Tem, 12/12). Karar verilmedi, sonraki tura not.

## 6. Dosyalar

* `experiments/higgs-tts3/kombo_probe.py` — kombo ölçümü (yeni). `token_probe.py`'nin
  `synth`/`_wav`'ını içe aktarır, ölçümü aynı `out/token_probe.json`'a **ekler**;
  var olan wav'ı yeniden üretmez (yarım koşum kaldığı yerden devam eder).
* `experiments/higgs-tts3/kombo_set.py` + `kombo.html` — kulak seti (yeni).
* `experiments/higgs-tts3/token_eval.py` — **değişmedi**, `--only` ile çağrıldı.
* Ham veri: `out/token_probe.json`, `out/token_eval.json` (43 token'lık ölçümün
  yanına eklendi, üzerine yazılmadı), `out/kombo.json`, `out/tokens/kombo_*`,
  `out/tokens/kb_*`, `out/kombo/`.
